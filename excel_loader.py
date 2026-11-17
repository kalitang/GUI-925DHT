"""电力低压设备 Excel 解析器（按用户给定的读取规则）

规则（用户 2026-09-25 指定）：
  1. 只读文件名包含「柱上变安卓」的 .xls 文件（Android 版导出）
  2. 每个文件 = 一个台区
  3. 表头 R0: A=线路编号 B=线路名称 C=设备名称 D=经度 E=纬度 F=备注
  4. 读 C 列（设备名称）：
     - 纯数字 → 接入点（墙支架）
     - 含「杆」字 → 杆塔
     - 「0杆」 → 特殊：既是杆塔（生成 0杆），其所在位置同时生成变压器
                变压器名称 = 该行 A 列（线路编号）
  5. 坐标在 D（经度）、E（纬度）列；若该两列非数字则退回 F 列再探测
  6. 设备名称显示用 C 列原值（接入点保留数字、杆塔保留"X杆"）

类型枚举（与前端 atlas / GUI 一致）：
  接入点 / 杆塔 / 变压器 / 其他
"""
from __future__ import annotations
import os
import re
import glob
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional

log = logging.getLogger("excel_loader")

TYPE_ACCESS_POINT = "接入点（墙支架）"   # 纯数字（墙支架）  → C
TYPE_POLE         = "杆塔"               # 含「杆」字        → B
TYPE_TRANSFORMER  = "柱上变（变压器）"    # 0杆 所在位置      → A
TYPE_METER_BOX    = "计量箱"             # 含「计量/表箱」   → D
TYPE_OTHER        = "其他"

# 图标映射（与 web/index.html 的 ICON_SOURCES 一致）
ICON_MAP = {
    TYPE_TRANSFORMER:  "transformer.svg",
    TYPE_ACCESS_POINT: "access_point.svg",
    TYPE_POLE:         "pole.svg",
    TYPE_METER_BOX:    "meter_box.svg",
    TYPE_OTHER:        "other.svg",
}

# 真实世界几何尺寸（米） — 用于按真实地理数据缩放地图图标
# 变压器：俯视圆形 + 立面（≈ 0.6m × 1.2m，整体可视尺寸约 1.2m）
# 杆塔：   40cm 直径水泥柱
# 接入点： 40cm 方形墙支架
# 计量箱： 20cm × 40cm 长方形
SIZE_M = {
    TYPE_TRANSFORMER:  {"w": 1.2, "h": 1.2},     # 1.2m × 1.2m（俯视为主）
    TYPE_POLE:         {"w": 0.4, "h": 0.4},     # 直径 40cm
    TYPE_ACCESS_POINT: {"w": 0.4, "h": 0.4},     # 40cm × 40cm
    TYPE_METER_BOX:    {"w": 0.2, "h": 0.4},     # 20cm × 40cm
    TYPE_OTHER:        {"w": 0.3, "h": 0.3},     # 默认 30cm
}


@dataclass
class Device:
    id: int
    name: str            # 设备名（C 列原值或 变压器名）
    line: str            # 台区名（A 列）
    lon: float
    lat: float
    device_type: str
    district: str = ""   # 镇街
    src_file: str = ""
    props: dict = field(default_factory=dict)


def _is_pure_number(s: str) -> bool:
    """C 列是否为纯数字（接入点）"""
    s = s.strip()
    if not s:
        return False
    return re.fullmatch(r"\d+(\.\d+)?", s) is not None


def _classify(c_val: str, a_val: str):
    """返回 (name, device_type)；0杆 额外返回变压器信息。

    输入已 strip。
    分类规则（用户 2026-09-26 指定 A/B/C/D）：
      A 变压器  → 仅当 0杆（line_no = cable_id）所在位置额外生成
      B 杆塔    → C 列含「杆」字（0杆 也算杆塔）
      C 墙支架  → C 列为纯数字（用户编号）
      D 计量箱  → C 列含「计量/表箱/电表/表位」关键字
      其他      → 兜底
    """
    if c_val == "0杆":
        # 0杆：既是杆塔，又在该位置生成变压器
        return c_val, TYPE_POLE, a_val.strip()   # 第三项=变压器名称
    # D 类：计量箱（用户电表）— 含计量/表箱/电表/表位
    if any(kw in c_val for kw in ("计量", "表箱", "电表", "表位")):
        return c_val, TYPE_METER_BOX, None
    if "杆" in c_val:
        return c_val, TYPE_POLE, None
    if _is_pure_number(c_val):
        return c_val, TYPE_ACCESS_POINT, None
    return c_val, TYPE_OTHER, None


def _parse_xls_one(fp: str, root: str, progress_done: int = 0) -> tuple:
    """解析单个「柱上变安卓.xls」。

    返回 (district, devices, total_rows, ok_count)。
    使用 xlrd（.xls 老格式）。
    """
    import xlrd
    rel = os.path.relpath(fp, root)
    district = _guess_district(rel, root)
    # 台区名：优先用 A 列（线路编号，每行都相同）；兜底用文件名去后缀
    # 支持「柱上变安卓」/「柱上安卓」/「柱上变」/「公变」等多种后缀（取最长匹配）
    import re as _re
    base = os.path.basename(fp)[:-4]
    m = _re.search(r"(.+?)(?:柱上变安卓|柱上安卓|柱上变|公变)安卓?$", base)
    if m:
        line_no = m.group(1) or base
    else:
        # 兜底：去「安卓」字样
        line_no = base[:-2] if base.endswith("安卓") else base

    devices: List[Device] = []
    seen = set()
    uid = 0
    total_rows = 0
    ok = 0

    wb = xlrd.open_workbook(fp)
    # 找数据 sheet：优先「墙支架」，否则第一个有数据的 sheet
    sheet = None
    for sn in wb.sheet_names():
        if sn == "墙支架":
            sheet = wb.sheet_by_name(sn)
            break
    if sheet is None:
        for sn in wb.sheet_names():
            s = wb.sheet_by_name(sn)
            if s.nrows > 1:
                sheet = s
                break
    if sheet is None:
        return district, devices, 0, 0

    def cell(r, c):
        try:
            v = sheet.cell_value(r, c)
            if v is None:
                return ""
            if isinstance(v, float):
                # 去掉尾零
                return str(v)
            return str(v)
        except Exception:
            return ""

    # 探测经度/纬度列：默认 D(3)/E(4)；若 R1 该两列非数字则自动找含 7~8 位小数的列
    lon_col, lat_col = 3, 4
    if sheet.nrows > 1:
        d1 = cell(1, 3)
        e1 = cell(1, 4)
        if not (re.fullmatch(r"\d+\.\d+", d1) and re.fullmatch(r"\d+\.\d+", e1)):
            # 自动探测：找两列连续都是 7~8 位小数的
            cand = []
            for c in range(sheet.ncols):
                v = cell(1, c)
                if re.fullmatch(r"\d+\.\d+", v):
                    cand.append(c)
            if len(cand) >= 2:
                lon_col, lat_col = cand[0], cand[1]

    # 跳过表头行
    start = 1
    for r in range(start, sheet.nrows):
        total_rows += 1
        c_val = cell(r, 2).strip()
        if not c_val:
            continue
        a_val = cell(r, 0).strip()
        # 台区名优先取 A 列（线路编号），所有行一致；为空才用文件名兜底
        if a_val:
            line_no = a_val
        lon_s = cell(r, lon_col).strip()
        lat_s = cell(r, lat_col).strip()
        try:
            lon = float(lon_s)
            lat = float(lat_s)
        except (ValueError, TypeError):
            continue
        if not (70 < lon < 140 and 15 < lat < 55):
            continue   # 粗略过滤非中国坐标

        name, dtype, transformer_name = _classify(c_val, a_val)
        # 去重 key 含台区：同一坐标若属于不同台区，是两个独立杆塔，必须都保留
        # （否则同坐标不同台区的杆塔会被吃掉，地图上无法区分）
        key = (line_no, round(lon, 6), round(lat, 6))
        uid += 1
        if key not in seen:
            seen.add(key)
            devices.append(Device(
                id=uid, name=name, line=line_no,
                lon=lon, lat=lat, device_type=dtype,
                district=district, src_file=os.path.basename(fp),
            ))
            ok += 1

        # 0杆 → 同位置额外生成变压器（名称取 0杆 所在行 A 列）
        if transformer_name:
            uid += 1
            tkey = (line_no, "TX", round(lon, 6), round(lat, 6))
            if tkey not in seen:
                seen.add(tkey)
                devices.append(Device(
                    id=uid, name=transformer_name, line=line_no,
                    lon=lon, lat=lat, device_type=TYPE_TRANSFORMER,
                    district=district, src_file=os.path.basename(fp),
                    props={"via_0pole": c_val, "a_col_name": transformer_name},
                ))
                ok += 1
    return district, devices, total_rows, ok


def load_dir(root: str, progress_cb=None) -> List[Device]:
    """递归找「柱上变安卓」文件并解析。

    兼容两种输入：
      - 已解压目录（含 *.xls）
      - 原始 zip（含 *.zip）→ 先正确解压（GBK 文件名还原）再读
    """
    # 1) 直接找 .xls（兜底模式：文件名带「安卓」「柱上」「公变」或所有 .xls）
    files = []
    # 优先匹配安卓/柱上/公变命名
    for pat in ("*安卓*.xls", "*柱上*.xls", "*公变*.xls"):
        files.extend(glob.glob(os.path.join(root, "**", pat), recursive=True))
    files = sorted(set(files))
    # 如果上述命名没匹配到，兜底取所有 .xls（避免 GBK 乱码文件名被漏掉）
    if not files:
        files = sorted(set(glob.glob(os.path.join(root, "**", "*.xls"), recursive=True)))

    # 2) 若一个没找到，尝试把 root 里的 zip 解压（正确还原 GBK 文件名）
    if not files:
        zips = glob.glob(os.path.join(root, "**", "*.zip"), recursive=True)
        if zips:
            log.info("no 安卓 .xls found, extracting %d zip", len(zips))
            _extract_zips(zips, root)
            for pat in ("*安卓*.xls", "*柱上*.xls", "*公变*.xls"):
                files.extend(glob.glob(os.path.join(root, "**", pat), recursive=True))
            files = sorted(set(files))

    total = len(files)
    if total == 0:
        log.warning("no 柱上变安卓 xls found under %s", root)
        return []

    log.info("parsing %d 安卓 files", total)
    global _LAST_REPORT
    _LAST_REPORT = {"scanned_files": total, "matched_files": total, "devices": 0}
    devices: List[Device] = []
    seen = set()
    from concurrent.futures import ThreadPoolExecutor
    done = 0
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(_parse_xls_one, fp, root): fp for fp in files}
        for fut in futures:
            district, devs, total_rows, ok = fut.result()
            done += 1
            for d in devs:
                # 跨文件去重 key 含台区：不同台区的同名同坐标设备是两个独立实体
                key = (d.line, round(d.lon, 6), round(d.lat, 6), d.name, d.device_type)
                if key in seen:
                    continue
                seen.add(key)
                devices.append(d)
            if progress_cb:
                progress_cb(done, total, f"{os.path.basename(futures[fut])}: +{ok} 设备")

    # 关键：各文件内部 id 从 1 起（局部），跨文件大量重复 → Cesium entities.add
    # 报 "entity with id X already exists" 去重，只剩第一个文件的实体。
    # 这里重新分配全局唯一 id（1..N）。
    for i, d in enumerate(devices, start=1):
        d.id = i

    _LAST_REPORT["devices"] = len(devices)
    return devices


_LAST_REPORT: dict = {}


def _extract_zips(zips: List[str], root: str):
    """把 zip 按 GBK 正确还原文件名解压到 root 下对应位置。"""
    import zipfile
    for zp in zips:
        z = zipfile.ZipFile(zp)
        base = os.path.splitext(os.path.basename(zp))[0]
        target_root = os.path.join(root, base)
        for info in z.infolist():
            raw = info.filename.encode("cp437", errors="replace")
            try:
                name = raw.decode("gbk")
            except Exception:
                name = info.filename
            target = os.path.join(target_root, name)
            if name.endswith("/"):
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
        log.info("extracted %s", zp)


def _guess_district(rel_path: str, root: str) -> str:
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) >= 2:
        head = parts[0]
        m = re.match(r"^([^\d]+)", head)
        if m:
            return m.group(1).strip()
    return os.path.basename(root) or "未知"


def device_summary(devices: List[Device]) -> dict:
    s = {}
    for d in devices:
        s[d.device_type] = s.get(d.device_type, 0) + 1
    return s


def excel_last_report() -> dict:
    """返回最近一次 load_dir 的解析报告（诊断用）。"""
    return _LAST_REPORT


if __name__ == "__main__":
    import sys, json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    from app_paths import find_data_root, DATA_ROOT
    # 无参数时自动识别数据目录（不硬编码盘符）
    root = sys.argv[1] if len(sys.argv) > 1 else str(find_data_root() or DATA_ROOT)
    devs = load_dir(root, progress_cb=lambda d, t, m: print(f"[{d}/{t}] {m}"))
    print(f"\n共 {len(devs)} 个设备")
    print("按类型:", device_summary(devs))
    if devs:
        print("\n样本:")
        for d in devs[:8]:
            print(" ", asdict(d))
