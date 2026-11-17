# -*- coding: utf-8 -*-
"""
户表批量工具 —— 核心业务逻辑（与 GUI 解耦，可单独命令行调用）。

两个功能：
  1) 生成客户档案：按《低压客户模板.xls》在每个子文件夹生成 "<文件夹名>用户.xls"
  2) 修改安卓表：把各子文件夹 "*安卓.xls" 中设备名称列的 "0杆" 改成文件夹名，原地保存
"""
import os
import shutil
import sys
import tempfile
import time

import openpyxl


def _prog_dir():
    """程序所在目录：打包后为 exe 目录，开发模式为 hutool_core.py 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# 模板候选目录（按优先级）。不做盘符硬编码 —— 打包后可能装在任意盘符。
# 1) 程序同目录（开发模式 hutool_core.py 所在目录 / 打包后 exe 所在目录）
# 2) PyInstaller onefile 的 _MEIPASS（打进 exe 的内置副本）
# 3) 用户主目录下的公共文档目录
# 4) 系统临时目录
_TEMPLATE_DIR_CANDIDATES = [
    None,  # _prog_dir() 在运行时填充
    getattr(sys, "_MEIPASS", None),
    os.path.join(os.path.expanduser("~"), "Documents"),
    os.path.join(os.path.expanduser("~"), "Documents", "低压电力信息采集"),
    tempfile.gettempdir(),
]


def _template_candidates():
    """按优先级列出模板候选路径。

    - PyInstaller onefile：exe 同目录（用户手动放置）→ _MEIPASS（打进 exe 的内置副本）
    - 开发模式：hutool_core.py 同目录 → 用户文档目录 → 临时目录
    """
    cands = []
    prog = _prog_dir()
    cands.append(os.path.join(prog, "低压客户模板.xls"))
    for d in _TEMPLATE_DIR_CANDIDATES[1:]:
        if d:
            cands.append(os.path.join(d, "低压客户模板.xls"))
    return cands


def default_template():
    """返回可用的模板路径：同目录副本优先，其次 _MEIPASS，最后用户文档目录。"""
    for p in _template_candidates():
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "找不到客户档案模板（应放在程序所在目录: %s）" % os.path.join(_prog_dir(), "低压客户模板.xls"))

SHEET_BOX = "计量箱台区录入接入点导入"
SHEET_USER = "同期用户"

# 模板列索引 (0-based)
C_TQBH, C_TQMC, C_DBNB, C_KHBH = 0, 1, 2, 3
C_KHMC, C_YDZZ, C_LXR, C_LXSJ, C_ZJBH = 4, 5, 6, 7, 8
DATA_START_ROW = 2

ANDROID_SUFFIX = "安卓.xls"
ANDROID_TARGET = "0杆"

XLS_FORMAT = 56  # xlExcel8


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------
def _s(v):
    """单元格值 -> 字符串；None -> ''"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _co_init():
    """在当前线程初始化 COM。

    COM 公寓是线程局部的：后台线程首次使用 COM 自动化前必须调用
    CoInitialize，否则抛 (-2147221008, '尚未调用 CoInitialize。')。
    在主线程里调用是安全的（幂等）。
    """
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass


def list_subfolders(root):
    """列出 root 下的子文件夹（按名称排序）"""
    if not root or not os.path.isdir(root):
        return []
    out = []
    for n in sorted(os.listdir(root)):
        p = os.path.join(root, n)
        if os.path.isdir(p):
            out.append((n, p))
    return out


def find_xlsx(folder):
    """文件夹内第一个 .xlsx（跳过 Excel 临时文件 ~$）"""
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith(".xlsx") and not f.startswith("~$"):
            return os.path.join(folder, f)
    return None


def find_android_xls(folder):
    """文件夹内文件名含 '安卓' 的 .xls"""
    hits = []
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith(".xls") and "安卓" in f:
            hits.append(os.path.join(folder, f))
    return hits


# --------------------------------------------------------------------------
# 功能 1：生成客户档案
# --------------------------------------------------------------------------
def read_source_records(xlsx_path):
    """从 .xlsx 读取记录。

    返回 (records, missing)：
      records: [{tq, bm, khbh, name, addr}]
      missing: [未能在同期用户中匹配到表码的 表码]
    """
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)

    ws = wb[SHEET_BOX]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [_s(v) for v in rows[0]]

    def col(*names):
        for n in names:
            if n in hdr:
                return hdr.index(n)
        raise KeyError("在「%s」中找不到列 %s，实际表头: %s" % (SHEET_BOX, names, hdr))

    i_bm = col("电能表条形码")
    i_hh = col("户号")
    i_tq = col("台区名称")

    box_rows = []
    for r in rows[1:]:
        bm = _s(r[i_bm] if i_bm < len(r) else None)
        if not bm or bm == "电能表条形码":
            continue
        box_rows.append({
            "bm": bm,
            "hh": _s(r[i_hh] if i_hh < len(r) else None),
            "tq": _s(r[i_tq] if i_tq < len(r) else None),
        })

    # 同期用户：无表头，A=户号 B=表码 C=名称 D=地址
    ws2 = wb[SHEET_USER]
    user_by_bm = {}
    for r in ws2.iter_rows(values_only=True):
        bm = _s(r[1] if len(r) > 1 else None)
        if not bm:
            continue
        user_by_bm[bm] = {
            "hh": _s(r[0] if len(r) > 0 else None),
            "name": _s(r[2] if len(r) > 2 else None),
            "addr": _s(r[3] if len(r) > 3 else None),
        }
    wb.close()

    records = []
    missing = []
    matched = []
    unmatched = []
    for b in box_rows:
        u = user_by_bm.get(b["bm"])
        if u is None:
            # 未匹配：只保留电能表条形码，客户名称/用电地址留空
            missing.append(b["bm"])
            unmatched.append({
                "tq": b["tq"],
                "bm": b["bm"],
                "khbh": "",        # 留空
                "name": "",       # 留空
                "addr": "",       # 留空
                "matched": False,
            })
            continue
        matched.append({
            "tq": b["tq"],          # 台区名称(必填) 与 台区编号(必填) 同值
            "bm": b["bm"],          # 电能表编号
            "khbh": b["hh"] or u["hh"],  # 客户编号
            "name": u["name"],      # 客户名称
            "addr": u["addr"],      # 用电地址
            "matched": True,
        })

    # 已匹配的在前，未匹配的排在最后（每个台区各自成档，不跨台区）
    records = matched + unmatched
    return records, missing


def write_customer_xls(tpl_path, out_path, records, excel=None):
    """用 Excel COM 把 records 写进模板并另存为 .xls。返回 excel 实例（供批量复用）。"""
    import win32com.client as win32

    own_excel = excel is None
    if own_excel:
        _co_init()
        excel = win32.gencache.EnsureDispatch("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False

    tmp_in = os.path.join(tempfile.gettempdir(), "tpl_%d.xls" % os.getpid())
    shutil.copyfile(tpl_path, tmp_in)

    wb = None
    try:
        wb = excel.Workbooks.Open(tmp_in)
        ws = wb.Worksheets(1)

        # 清空数据区（含模板自带的示例数据与说明行）。
        # 注意：模板第20行 A20:I20 是合并单元格("以下5条为示例数据")，
        # 直接 ClearContents 清不掉合并区左上角的值，必须先取消合并。
        last_row = max(ws.UsedRange.Rows.Count, 25)
        ws.Range(ws.Cells(DATA_START_ROW, 1), ws.Cells(last_row, 9)).UnMerge()
        ws.Range(ws.Cells(DATA_START_ROW, 1), ws.Cells(last_row, 9)).ClearContents()
        for r in range(DATA_START_ROW, last_row + 1):
            for c in range(1, 10):
                ws.Cells(r, c).ClearContents()

        n = len(records)
        last = DATA_START_ROW + n - 1

        # 先把编号类列设为文本格式，避免 Excel 把长数字串转成浮点数
        for c in (C_TQBH + 1, C_TQMC + 1, C_DBNB + 1, C_KHBH + 1):
            ws.Range(ws.Cells(DATA_START_ROW, c), ws.Cells(last, c)).NumberFormat = "@"

        for i, rec in enumerate(records):
            r = DATA_START_ROW + i
            vals = [
                rec["tq"],       # 台区编号(必填)
                rec["tq"],       # 台区名称(必填)
                rec["bm"],       # 电能表编号
                rec["khbh"],     # 客户编号
                rec["name"],     # 客户名称
                rec["addr"],     # 用电地址
                rec["name"],     # 联系人（必填）= 客户名称
                "",              # 联系手机（必填）留空
                "",              # 证件编号 留空
            ]
            for c, v in enumerate(vals, start=1):
                cell = ws.Cells(r, c)
                if c in (C_TQBH + 1, C_TQMC + 1, C_DBNB + 1, C_KHBH + 1):
                    if v:
                        cell.Value = str(v)
                        cell.NumberFormat = "@"
                    else:
                        # 未匹配的行：这几列留空（真正空白，不是空字符串）
                        cell.ClearContents()
                else:
                    cell.Value = v

        tmp_out = os.path.join(tempfile.gettempdir(), "out_%d.xls" % os.getpid())
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        wb.SaveAs(tmp_out, FileFormat=XLS_FORMAT)
        wb.Close(SaveChanges=False)
        wb = None

        # 移动到目标位置（重试以避开文件占用）
        if os.path.exists(out_path):
            for _ in range(5):
                try:
                    os.remove(out_path)
                    break
                except OSError:
                    time.sleep(0.4)
        for _ in range(5):
            try:
                shutil.move(tmp_out, out_path)
                break
            except OSError:
                time.sleep(0.4)
        else:
            raise RuntimeError("无法移动输出文件到目标位置: " + out_path)
    finally:
        try:
            if wb is not None:
                wb.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            os.remove(tmp_in)
        except OSError:
            pass
        if own_excel:
            try:
                excel.Quit()
            except Exception:
                pass
    return excel


def generate_for_folder(folder, tpl_path=None, excel=None, skip_unmatched=True):
    """在单个文件夹生成 '<文件夹名>用户.xls'。

    tpl_path=None 表示用内置模板（程序同目录的 低压客户模板.xls）。

    skip_unmatched=True  : 未匹配的记录仍写入，但只填电能表条形码，
                           其余列留空，排在已匹配记录之后（默认）
    skip_unmatched=False : 未匹配的记录整条跳过

    返回 (ok, message)：
      ok=True  -> 生成成功
      ok=False -> 失败，message 为原因
    """
    name = os.path.basename(os.path.abspath(folder))
    xlsx = find_xlsx(folder)
    if not xlsx:
        return False, "文件夹内没有 .xlsx 源文件"

    if tpl_path is None:
        tpl_path = default_template()
    if not os.path.isfile(tpl_path):
        return False, "找不到客户档案模板: %s" % tpl_path

    records, missing = read_source_records(xlsx)
    if not records:
        return False, "未读到有效记录"

    # skip_unmatched=True -> 未匹配的整条跳过
    # skip_unmatched=False -> 保留未匹配记录（只填条形码，其余留空），排在最后
    if skip_unmatched and missing:
        miss_set = set(missing)
        records = [r for r in records if r["bm"] not in miss_set]

    out = os.path.join(folder, name + "用户.xls")
    try:
        write_customer_xls(tpl_path, out, records, excel=excel)
    except Exception as e:
        return False, "写入失败: %s" % e
    if missing:
        if skip_unmatched:
            return True, "%d 条记录（跳过 %d 条未匹配）" % (
                len(records), len(missing))
        return True, "%d 条记录（其中 %d 条未匹配，仅填条形码）" % (
            len(records), len(missing))
    return True, "%d 条记录" % len(records)


# --------------------------------------------------------------------------
# 功能 2：修改安卓表中的 "0杆"
# --------------------------------------------------------------------------
def is_zero_gan(value):
    """判断单元格值是否就是「0杆」这两个字。

    规则（硬性）：
      只修改整格内容恰好是 "0杆" 的单元格。
      含 "0杆" 但还有别的字的一律不动，例如：
        A10杆  B10杆  10杆  A10-B2杆  B2-B10杆  B5-10杆  A0杆  B0杆
      这些杆名里的 "0" 属于支线/分支编号，不是 0 号杆。

    实现要点：
      - 必须用整格相等（==），不能用 "0杆" in value（否则上面那些全部误改）
      - 不能用 startswith/endswith（'0杆' 不等于 '10杆'/'A10杆'，
        但 '0杆A'、'0杆-1' 也不该改）
      - 前后空白视为等价（Excel 里手输常带空格）
      - 数值型 0 不是 "0杆"，不匹配
    """
    if not isinstance(value, str):
        return False
    return value.strip() == ANDROID_TARGET


def find_zero_gan_cells(xls_path):
    """扫描 xls 中值为 '0杆' 的单元格，返回 [(sheet_index, row, col), ...]。

    只做整格精确匹配 —— 不能用 'in'，否则 'A10杆'、'B2-B10杆' 会被误判。
    """
    import xlrd
    b = xlrd.open_workbook(xls_path)
    hits = []
    for si in range(b.nsheets):
        s = b.sheet_by_index(si)
        for r in range(s.nrows):
            for c in range(s.ncols):
                if is_zero_gan(s.cell(r, c).value):
                    hits.append((si, r, c))
    b.release_resources()
    return hits


def modify_android_xls(xls_path, new_value, sheet_name=None, excel=None):
    """把 xls_path 中值为 '0杆' 的单元格改成 new_value，原地保存（格式不变）。

    可传入 excel 复用同一个 Excel 实例（批量时省掉反复启动的开销）。

    返回 (count, message)
    """
    import win32com.client as win32

    # COM 是每线程初始化的：后台线程里必须先 CoInitialize，
    # 否则抛 (-2147221008, '尚未调用 CoInitialize。')
    _co_init()

    own_excel = excel is None
    if own_excel:
        excel = win32.gencache.EnsureDispatch("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False

    wb = None
    try:
        wb = excel.Workbooks.Open(xls_path)
        total = 0
        for ws in wb.Worksheets:
            if sheet_name and ws.Name != sheet_name:
                continue
            used = ws.UsedRange
            nr, nc = used.Rows.Count, used.Columns.Count
            if nr == 0 or nc == 0:
                continue
            for r in range(1, nr + 1):
                for c in range(1, nc + 1):
                    cell = ws.Cells(r, c)
                    # 只改整格就是 "0杆" 的；A10杆 / B2-B10杆 / 10杆 等一律不动
                    if is_zero_gan(cell.Value):
                        cell.Value = new_value
                        total += 1
        if total:
            wb.Save()
        wb.Close(SaveChanges=False)
        wb = None
        return total, "修改 %d 处" % total
    finally:
        try:
            if wb is not None:
                wb.Close(SaveChanges=False)
        except Exception:
            pass
        if own_excel:
            try:
                excel.Quit()
            except Exception:
                pass


def modify_android_in_folder(folder, excel=None, keep_collector_copy=False, root=None):
    """修改该文件夹内所有 '*安卓.xls' 中的 '0杆' -> 文件夹名。

    keep_collector_copy=True 时采用「双输出」模式：
        先把原始文件复制一份到 <根目录>\\_采集软件数据\\<台区名>\\ 下
        （0杆 原样保留），再把原文件里的 0杆 改成文件夹名。
        原因是 D 盘《电力信息采集外业》程序的 excel_loader._classify()
        硬编码 `if c_val == "0杆"` 来识别母杆并在该位置生成变压器；
        一旦改名，采集软件就认不出母杆，变压器会整个消失。

    返回 (ok, message)
    """
    name = os.path.basename(os.path.abspath(folder))
    files = find_android_xls(folder)
    if not files:
        return False, "文件夹内没有 *安卓.xls 文件"

    total = 0
    copied = 0
    for p in files:
        try:
            hits = find_zero_gan_cells(p)
            if not hits:
                continue
            if keep_collector_copy:
                # 先留一份 0杆 原样的副本给采集软件
                if make_collector_copy(p, name, root=root):
                    copied += 1
            n, _ = modify_android_xls(p, name, excel=excel)
            total += n
        except Exception as e:
            return False, "修改 %s 失败: %s" % (os.path.basename(p), e)
    if total == 0:
        return False, "未找到值为 '0杆' 的单元格"
    if keep_collector_copy:
        return True, "修改 %d 处，另存采集版 %d 份（0杆 保留原样）" % (total, copied)
    return True, "修改 %d 处" % total


# --------------------------------------------------------------------------
# 与《电力信息采集外业》程序的结合
# --------------------------------------------------------------------------
COLLECTOR_DIR = "_采集软件数据"
COLLECTOR_SUFFIX = "_采集版安卓.xls"


def make_collector_copy(src_path, folder_name, root=None):
    """把安卓表复制成「采集版」：0杆 原样保留，供采集软件使用。

    采集版不放在原台区目录里（会被采集软件当成第二个台区重复加载），
    而是集中放到 <根目录>\\_采集软件数据\\ 下，按台区分文件夹存放。

    返回副本路径；失败返回 None。
    """
    try:
        if root is None:
            root = os.path.dirname(os.path.dirname(src_path))
        dest_dir = os.path.join(root, COLLECTOR_DIR, folder_name)
        os.makedirs(dest_dir, exist_ok=True)
        base = os.path.basename(src_path)
        stem = base[:-4] if base.lower().endswith(".xls") else base
        dst = os.path.join(dest_dir, stem + ".xls")
        shutil.copyfile(src_path, dst)
        return dst
    except Exception:
        return None


def prepare_collector_data(root, log=None, should_stop=None):
    """为采集软件准备数据目录。

    采集软件（excel_loader.load_dir）递归扫描 "**/*安卓*.xls"，
    读每张表的「墙支架」sheet，把 C 列恰好等于 "0杆" 的那一行当作母杆，
    并在该坐标上额外生成一台变压器（名称取 A 列 = 台区名）。

    因此给采集软件的数据必须保留 "0杆" 字面量。

    返回统计 dict:
      {"total": 台区数, "copied": 复制数, "dest": 采集数据目录}
    """
    root = os.path.abspath(root)
    dest_root = os.path.join(root, COLLECTOR_DIR)
    stats = {"total": 0, "copied": 0, "dest": dest_root}

    folders = list_subfolders(root)
    stats["total"] = len(folders)
    if not folders:
        return stats

    for name, path in folders:
        if should_stop and should_stop():
            break
        files = find_android_xls(path)
        if not files:
            if log:
                log("[跳过] %s — 没有 *安卓.xls" % name)
            continue
        for src in files:
            dst = make_collector_copy(src, name, root=root)
            if dst:
                stats["copied"] += 1
                if log:
                    log("[复制] %s -> %s\\%s\\%s"
                        % (os.path.basename(src), COLLECTOR_DIR, name,
                           os.path.basename(dst)))
    return stats


# --------------------------------------------------------------------------
# 功能 3：提取整理 PDF —— 把根目录及其子文件夹内的全部 PDF 移到 打印 子目录
# --------------------------------------------------------------------------
PDF_SUBDIR = "打印"


def collect_pdfs(root, dest_name=PDF_SUBDIR, log=None, should_stop=None):
    """把 root 下（根目录本身 + 各级子文件夹）的全部 PDF 移到 root\\<dest_name>。

    - 只收集 PDF（.pdf，不区分大小写）
    - 递归所有层级子文件夹
    - 目标同名时自动加序号，不覆盖已有文件
    - 已存在于目标目录且内容相同的，视为已归位，跳过

    返回统计 dict:
      {"total": 发现数, "moved": 移动数, "skipped": 跳过数, "dup": 重名改数,
       "dest": 目标目录}
    """
    root = os.path.abspath(root)
    dest = os.path.join(root, dest_name)
    stats = {"total": 0, "moved": 0, "skipped": 0, "dup": 0, "dest": dest}

    # 收集所有 PDF（跳过目标目录本身，避免把已归位的又算一遍）
    pdfs = []
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.abspath(dirpath) == dest:
            dirnames[:] = []
            continue
        if os.path.abspath(dirpath).startswith(dest + os.sep):
            continue
        for f in filenames:
            if f.lower().endswith(".pdf"):
                pdfs.append(os.path.join(dirpath, f))

    stats["total"] = len(pdfs)
    if not pdfs:
        return stats

    os.makedirs(dest, exist_ok=True)

    for src in pdfs:
        if should_stop and should_stop():
            break
        base = os.path.basename(src)
        stem, ext = os.path.splitext(base)
        dst = os.path.join(dest, base)
        if os.path.abspath(src) == os.path.abspath(dst):
            stats["skipped"] += 1
            continue

        # 同名保护：加 (1)(2)... 序号，绝不覆盖
        if os.path.exists(dst):
            n = 1
            while True:
                cand = os.path.join(dest, "%s(%d)%s" % (stem, n, ext))
                if not os.path.exists(cand):
                    dst = cand
                    stats["dup"] += 1
                    break
                n += 1

        try:
            shutil.move(src, dst)
            stats["moved"] += 1
            if log:
                log("[移动] %s -> %s" % (os.path.relpath(src, root),
                                         os.path.relpath(dst, root)))
        except Exception as e:
            stats["skipped"] += 1
            if log:
                log("[失败] %s — %s" % (os.path.relpath(src, root), e))
    return stats


# --------------------------------------------------------------------------
# 批量调度（供 GUI 与 CLI 共用）
# --------------------------------------------------------------------------
def batch_generate(root, tpl_path=None, log=None, should_stop=None, excel=None,
                   skip_unmatched=True):
    """批量生成客户档案。返回统计 dict。

    tpl_path=None -> 使用程序内置模板（默认）。
    """
    stats = {"total": 0, "ok": 0, "fail": 0, "skipped": 0}
    folders = list_subfolders(root)
    stats["total"] = len(folders)
    if tpl_path is None:
        tpl_path = default_template()
    own = excel is None
    if own:
        import win32com.client as win32
        _co_init()
        excel = win32.gencache.EnsureDispatch("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
    try:
        for name, path in folders:
            if should_stop and should_stop():
                break
            ok, msg = generate_for_folder(path, tpl_path, excel=excel,
                                          skip_unmatched=skip_unmatched)
            if ok:
                stats["ok"] += 1
            else:
                stats["fail"] += 1
            if log:
                log("%s  %s — %s" % ("[成功]" if ok else "[失败]", name, msg))
    finally:
        if own:
            try:
                excel.Quit()
            except Exception:
                pass
    return stats


def batch_modify_android(root, log=None, should_stop=None,
                         keep_collector_copy=False):
    """批量修改安卓表。返回统计 dict。

    keep_collector_copy=True -> 同时为采集软件保存一份 0杆 原样的副本
                                到 <根目录>\\_采集软件数据\\ 下
    """
    import win32com.client as win32

    _co_init()
    stats = {"total": 0, "ok": 0, "fail": 0, "skipped": 0}
    folders = list_subfolders(root)
    stats["total"] = len(folders)

    # 全程复用一个 Excel 实例：每个文件都启一次 Excel 太慢
    excel = win32.gencache.EnsureDispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        for name, path in folders:
            if should_stop and should_stop():
                break
            files = find_android_xls(path)
            if not files:
                stats["skipped"] += 1
                if log:
                    log("[跳过] %s — 没有 *安卓.xls 文件" % name)
                continue
            ok, msg = modify_android_in_folder(
                path, excel=excel, keep_collector_copy=keep_collector_copy,
                root=root)
            if ok:
                stats["ok"] += 1
            else:
                stats["fail"] += 1
            if log:
                log("%s  %s — %s" % ("[成功]" if ok else "[失败]", name, msg))
    finally:
        try:
            excel.Quit()
        except Exception:
            pass
    return stats
