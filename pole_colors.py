"""同坐标不同台区杆塔的颜色分配。

问题背景
--------
多个台区的杆塔可能落在完全相同的经纬度上（例如共用一根电杆的两个台区，
或不同台区数据录入时抄了同一坐标）。此时地图上它们叠在一起，
看不出有几种设备，必须靠颜色区分。

算法
----
1. 按坐标（经纬度各取 6 位小数）把所有设备聚成簇。
2. 若某簇内涉及多个台区（line 不同）→ 该簇为"冲突簇"。
3. 冲突台区全局编号：把所有冲突簇涉及的不同台区排成一队，
   按编号从色相环上取色 —— **同一个台区在所有冲突簇里颜色相同**。
   这样用户看到地图上两个不同颜色的点，能立刻知道
   "红点属于台区A、青点属于台区B"，而不是每簇各分一套颜色。
4. 非冲突簇的设备不参与配色（保持类型默认色），避免地图变成调色盘。

输出
----
    {device_id: "#RRGGBB"}   仅包含冲突簇内的设备

色相环
------
用 HSL 色相 0..360 均分取色，保证相邻编号的台区颜色差异最大；
亮度/饱和度固定，保证在卫星底图上都能看清。
"""
from __future__ import annotations

import colorsys
from typing import Dict, List, Tuple

from excel_loader import Device, TYPE_POLE

# 色相环起点（红）→ 依次黄绿青蓝品红
_HUE_START = 0.0
# 固定饱和度/亮度（HSL）
_SAT = 0.85
_LIGHT = 0.55


def _hue_to_hex(hue: float) -> str:
    """HSL 色相 → #RRGGBB（饱和度/亮度固定）。"""
    r, g, b = colorsys.hls_to_rgb((hue % 360.0) / 360.0, _LIGHT, _SAT)
    return "#{:02X}{:02X}{:02X}".format(int(round(r * 255)),
                                       int(round(g * 255)),
                                       int(round(b * 255)))


def find_conflict_clusters(devices: List[Device]) -> Dict[Tuple[float, float], List[Device]]:
    """找出含多个台区的坐标簇。

    返回 {坐标: [该簇内设备...]}，只含冲突簇（簇内 line 种类 > 1）。
    """
    buckets: Dict[Tuple[float, float], List[Device]] = {}
    for d in devices:
        key = (round(d.lon, 6), round(d.lat, 6))
        buckets.setdefault(key, []).append(d)

    conflicts = {}
    for key, devs in buckets.items():
        lines = {d.line or "" for d in devs}
        if len(lines) > 1:
            conflicts[key] = devs
    return conflicts


def assign_pole_colors(devices: List[Device],
                        only_poles: bool = False) -> Dict[int, str]:
    """为同坐标不同台区的设备分配不同颜色。

    同一台区在所有冲突簇里颜色相同（全局一致配色）。

    only_poles=False（默认）：冲突簇内所有设备都着色。
        实测冲突簇里既有杆塔也有接入点（墙支架）—— 都属于"电杆类"，
        一起着色才能让用户一眼看清同位置到底有几个台区的设备。
    only_poles=True：只给杆塔（TYPE_POLE）着色。

    返回 {device_id: "#RRGGBB"}。
    """
    conflicts = find_conflict_clusters(devices)
    if not conflicts:
        return {}

    # --- 收集所有冲突台区，按首次出现顺序全局编号 ---
    conflict_lines: List[str] = []
    seen_lines = set()
    for _key, devs in conflicts.items():
        for d in devs:
            line = d.line or ""
            if line not in seen_lines:
                seen_lines.add(line)
                conflict_lines.append(line)

    # --- 每个台区一个固定颜色 ---
    n = len(conflict_lines)
    line_color: Dict[str, str] = {}
    for idx, line in enumerate(conflict_lines):
        line_color[line] = _hue_to_hex(_HUE_START + (360.0 * idx / n))

    # --- 落到具体设备 ---
    out: Dict[int, str] = {}
    for _key, devs in conflicts.items():
        for d in devs:
            if only_poles and d.device_type != TYPE_POLE:
                continue
            col = line_color.get(d.line or "")
            if col:
                out[d.id] = col
    return out


def line_color_map(devices: List[Device]) -> Dict[str, str]:
    """返回 {台区名: 颜色} —— 仅含参与冲突簇的台区。

    前端可据此在台区列表/详情里显示同一个色块，方便对照地图。
    """
    conflicts = find_conflict_clusters(devices)
    conflict_lines: List[str] = []
    seen = set()
    for _key, devs in conflicts.items():
        for d in devs:
            line = d.line or ""
            if line not in seen:
                seen.add(line)
                conflict_lines.append(line)
    n = len(conflict_lines)
    return {line: _hue_to_hex(_HUE_START + (360.0 * idx / n))
            for idx, line in enumerate(conflict_lines)}


def color_summary(devices: List[Device]) -> dict:
    """诊断信息：冲突簇数量、涉及台区数、涉及设备数。"""
    conflicts = find_conflict_clusters(devices)
    lines = set()
    dev_count = 0
    for devs in conflicts.values():
        lines.update(d.line or "" for d in devs)
        dev_count += len(devs)
    return {
        "conflict_clusters": len(conflicts),
        "lines_involved": len(lines),
        "devices_involved": dev_count,
    }


if __name__ == "__main__":
    import sys
    from excel_loader import load_dir
    from app_paths import find_data_root, DATA_ROOT
    root = sys.argv[1] if len(sys.argv) > 1 else str(find_data_root() or DATA_ROOT)
    devs = load_dir(root)
    s = color_summary(devs)
    print(f"设备总数: {len(devs)}")
    print(f"冲突簇(同坐标多台区): {s['conflict_clusters']}")
    print(f"涉及台区: {s['lines_involved']}")
    print(f"涉及设备: {s['devices_involved']}")
    colors = assign_pole_colors(devs)
    print(f"着色杆塔数: {len(colors)}")
    # 抽样显示
    for i, (did, col) in enumerate(list(colors.items())[:10]):
        d = next(x for x in devs if x.id == did)
        print(f"  id={did} {col}  {d.name}  台区={d.line}")
