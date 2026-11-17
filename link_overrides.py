"""手动挂接 override → 连线重算（与 tree_builder 共用同一份 tree_overrides.json）。

核心约束（用户 2026-09-28）：
  - 任何设备的前端（上级）设备有且只能有 1 个；
  - 手动修改后必须同步到地图连线（build_links 结果 = 自动 + override 替换）；
  - 不允许跨台区（override 的父设备必须与子设备同一台区）。

override JSON 格式（tree_builder.save_override 写）：
    { "<line>|<type>|<name>": {"parent": <父设备名>, "level": <父层级>}, ... }

apply_link_overrides(devices, links, overrides) → (new_links, stats)
  - links: build_links 产出的 [(parent_id, child_id, level)]
  - 对每个有 override 的设备：删除所有指向它的自动连线，
    加一条 parent→child（若父设备与设备同台区且存在）；
    若删除后 child 仍无前端（父设备缺失/跨台区）→ 就近兜底同台区设备。
  - 每个 child 最终有且仅有一条入边。
"""
from __future__ import annotations
import json
import math
from typing import Dict, List, Optional, Tuple

from excel_loader import Device, TYPE_TRANSFORMER, TYPE_ACCESS_POINT, TYPE_POLE, TYPE_METER_BOX

# override 层级号 → 设备类型（与 tree_builder.TYPE_LABELS 一致）
LEVEL_TYPE = {
    1: TYPE_TRANSFORMER,
    2: TYPE_POLE,
    3: TYPE_ACCESS_POINT,
    4: TYPE_METER_BOX,
}


def load_overrides(path: Optional[str]) -> dict:
    if not path:
        return {}
    try:
        p = __import__('pathlib').Path(path)
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}


def _dist_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    dlat = abs(lat1 - lat2) * 110574
    dlon = abs(lon1 - lon2) * 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlon, dlat)


def apply_link_overrides(devices: List[Device],
                        links: List[Tuple[int, int, int]],
                        overrides: dict,
                        max_link_dist_m: float = 500.0
                        ) -> Tuple[List[Tuple[int, int, int]], dict]:
    """返回 (新连线, 统计)。统计: applied / invalid / unassigned / total。"""
    by_key: Dict[str, Device] = {}
    for d in devices:
        by_key[f"{d.line}|{d.device_type}|{d.name}"] = d
    by_id = {d.id: d for d in devices}

    # 设备 → override 条目（dev_key 格式与 tree_builder._dev_key 一致）
    ovr_by_child: Dict[int, dict] = {}
    invalid = 0
    for key, entry in (overrides or {}).items():
        d = by_key.get(key)
        if d is None:
            continue
        parent_name = (entry or {}).get('parent')
        plevel = (entry or {}).get('level')
        ptype = LEVEL_TYPE.get(plevel)
        # 找同台区的同类型父设备
        parent = None
        if parent_name and ptype:
            candidates = [x for x in devices
                          if x.line == d.line and x.device_type == ptype and x.name.strip() == parent_name.strip()]
            parent = candidates[0] if candidates else None
        if parent is None:
            invalid += 1
            continue
        if parent.id == d.id:
            invalid += 1
            continue
        ovr_by_child[d.id] = {'parent': parent, 'entry': entry, 'key': key}

    # 自动连线集
    auto = set((p, c, lvl) for p, c, lvl in links)
    # child 的自动入边
    auto_in: Dict[int, List[Tuple[int, int, int]]] = {}
    for p, c, lvl in links:
        auto_in.setdefault(c, []).append((p, c, lvl))

    new_links: List[Tuple[int, int, int]] = []
    used: set = set()

    # 1) 全部自动连线先入池
    for e in links:
        new_links.append(e)
        used.add(e)

    # 2) 对每个有 override 的 child：删它的全部自动入边，加一条 override 边
    unassigned = 0
    for cid, ov in ovr_by_child.items():
        p: Device = ov['parent']
        d = by_id.get(cid)
        if d is None:
            continue
        # 删该 child 的全部入边
        new_links = [e for e in new_links if e[1] != cid]
        lvl = 1 if p.device_type == TYPE_TRANSFORMER else (
            2 if p.device_type == TYPE_POLE else 3)
        e = (p.id, cid, lvl)
        if e not in used:
            new_links.append(e)
            used.add(e)
        else:
            # 与自动连线重合 → 保留（不重复计数）
            pass

    # 3) 保证每个 child 恰好一条入边（override 父无效 / 父设备缺失 → 就近兜底同台区）
    in_count: Dict[int, int] = {}
    for p, c, lvl in new_links:
        in_count[c] = in_count.get(c, 0) + 1
    for cid, ov in ovr_by_child.items():
        d = by_id.get(cid)
        if d is None:
            continue
        p: Device = ov['parent']
        if in_count.get(cid, 0) >= 1:
            continue
        # 无有效入边：同台区同类型父就近兜底
        pool = [x for x in devices
                if x.line == d.line and x.device_type == p.device_type and x.id != cid]
        best, best_d = None, None
        for x in pool:
            dd = _dist_m(d.lon, d.lat, x.lon, x.lat)
            if dd <= max_link_dist_m and (best_d is None or dd < best_d):
                best, best_d = x, dd
        if best is not None:
            lvl = 1 if best.device_type == TYPE_TRANSFORMER else (
                2 if best.device_type == TYPE_POLE else 3)
            e = (best.id, cid, lvl)
            new_links.append(e)
            in_count[cid] = 1
            used.add(e)
        else:
            unassigned += 1

    # 4) 去重 + 保证每个 child 唯一入边（多入边只留第一条：override 优先，其次自动）
    ovr_edges = {ov['parent'].id: cid for cid, ov in ovr_by_child.items()}
    seen_child: Dict[int, int] = {}
    final: List[Tuple[int, int, int]] = []
    # 先放 override 边，再放非 override 自动边
    ordered = [e for e in new_links if e[1] in ovr_edges or e[0] in ovr_edges]
    for e in new_links:
        if e not in ordered:
            ordered.append(e)
    for e in ordered:
        c = e[1]
        if c in seen_child:
            continue
        seen_child[c] = e[0]
        final.append(e)

    stats = {
        'applied': len(ovr_by_child),
        'invalid': invalid,
        'unassigned': unassigned,
        'total': len(final),
    }
    return final, stats
