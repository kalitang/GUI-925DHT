"""电力设备挂接树构建器。

层级（自顶向下）：
  L0 台区（文件名 / A 列线路编号）
  L1 变压器（「0杆」所在行生成，名称取 A 列，即台区名）
  L2 杆塔（名称含「杆」；默认上级 = 该台区变压器）
  L3 接入点 / 墙支架（C 列纯数字；默认上级 = 地理距离最近的杆塔或接入点）
  L4 计量箱（单相/三相；默认上级 = 接入点）

自动挂接规则
------------
1. 变压器：前端，只能被「0杆」连接 → 0杆 默认挂变压器。
2. 杆塔默认按名称规则链自连（每个台区内）：
   - 序列 1..N：1杆→0杆，2杆→1杆，3杆→2杆 … 依次类推；0杆→变压器。
   - 序列 X（A/B/C…）：X1杆→0杆，X2杆→X1杆，X3杆→X2杆 …
   - 分支 X-Y（如 A1-1杆 / A1-A1杆 / A1-B1杆）：挂「X杆」（前缀序列杆）。
   - 规则链能找到的上级即自动挂；找不到的杆塔 → 默认挂该台区变压器。
3. 接入点（墙支架）：按地理位置挂到最近的一根杆塔；同台区内没有杆塔
   时挂最近的接入点（墙支架互连）；都没有 → 未分配。
4. 计量箱：末端，只能连接接入点 → 按地理位置挂最近的接入点；
   同台区没有接入点 → 未分配。
5. 未识别 / 无规则 / 无就近目标的设备 → 归入该台区「未分配设备」。

手动修改
--------
所有自动挂接都可以被手动覆盖：override 表按设备 id 存「父设备名 + 父层级」，
持久化到 JSON（cache/tree_overrides.json），下次加载优先用 override。
"""
from __future__ import annotations
import json
import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from excel_loader import (Device, TYPE_TRANSFORMER, TYPE_ACCESS_POINT,
                          TYPE_POLE, TYPE_OTHER)

METRO_TYPE = "计量箱"
TYPE_LABELS = {
    TYPE_TRANSFORMER: "变压器",
    TYPE_POLE: "杆塔",
    TYPE_ACCESS_POINT: "接入点",
    METRO_TYPE: "计量箱",
    TYPE_OTHER: "其他",
}

_OVERRIDE_FILE = None   # 运行时由 build_tree(devs, override_path) 设定


# ---------- 杆塔规则解析 ----------

# 序列 1..N 的「N杆」（不含前缀 X-Y）
_SEQ_N_RE = re.compile(r"^(\d+)杆$")
# 序列 X-Y：A1杆 / B2杆 …（X 可为多字母）
_SEQ_X_RE = re.compile(r"^([A-Za-z]+)(\d+)杆$")
# 分支 X-Y：A1-1杆 / A1-A1杆 / A1-B1杆 …
_BRANCH_RE = re.compile(r"^([A-Za-z]+\d+)-([A-Za-z]*\d+|\d+[A-Za-z]*)(杆|立杆)?$")
# 前缀杆：X-Y 的前缀 = X杆（序列 X 里的 N 杆）
_PREFIX_RE = re.compile(r"^([A-Za-z]+)(\d+)")


def _pole_parent_by_rule(pole_name: str, pole_names: set, has_pole0: bool):
    """按名称规则链推导杆塔的默认上级杆塔名；找不到返回 None。"""
    name = pole_name.strip()
    if name == "0杆":
        return None                       # 0杆 是序列根 → 挂变压器
    m = _SEQ_N_RE.match(name)
    if m:
        n = int(m.group(1))
        prev = f"{n-1}杆"
        return prev if prev in pole_names else None   # 前驱缺失 → None（挂变压器兜底）
    m = _SEQ_X_RE.match(name)
    if m:
        x, n = m.group(1), int(m.group(2))
        if n == 1:
            return "0杆" if has_pole0 else None        # X1 序列入口 → 0杆（存在时）
        prev = f"{x}{n-1}杆"
        return prev if prev in pole_names else None
    m = _BRANCH_RE.match(name)
    if m:
        m2 = _PREFIX_RE.match(name)
        if m2:
            seq_pole = f"{m2.group(1)}{m2.group(2)}杆"   # A1-1杆 → A1杆
            if seq_pole in pole_names:
                return seq_pole
    return None                                # 未知规则 → 变压器兜底


def _dist_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """近似距离（米），经纬度 1°≈111km，经度方向按纬度压缩。"""
    dlat = abs(lat1 - lat2) * 110574
    dlon = abs(lon1 - lon2) * 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlon, dlat)


# ---------- 节点 ----------

@dataclass
class TreeNode:
    device_id: int          # Device.id（台区/未分配节点 = -1 或负数）
    name: str
    level: int             # 0 台区 / 1 变压器 / 2 杆塔 / 3 接入点 / 4 计量箱 / 9 未分配
    lon: Optional[float] = None
    lat: Optional[float] = None
    auto_parent: Optional[str] = None    # 自动推导的父节点名（None = 挂台区根）
    override_parent: Optional[str] = None  # 手动指定的父节点名
    is_transformer: bool = False
    children: List["TreeNode"] = None

    def __post_init__(self):
        if self.children is None:
            self.children = []

    @property
    def parent(self) -> Optional[str]:
        return self.override_parent or self.auto_parent

    @property
    def label(self) -> str:
        """显示文字：手动挂接的设备加 ◆ 标记。"""
        mark = "◆ " if self.override_parent else ""
        return f"{mark}{self.name}"


def _dev_key(d: Device) -> str:
    """设备唯一 key（字符串格式，可直接写入 JSON）。"""
    return f"{d.line}|{d.device_type}|{d.name}"


# ---------- 构建 ----------

def _classify_type(d: Device) -> str:
    if d.device_type == TYPE_TRANSFORMER:
        return "transformer"
    if d.device_type == TYPE_POLE:
        return "pole"
    if d.device_type == TYPE_ACCESS_POINT:
        return "access"
    # 计量箱：仅显式类型名；其余一律未分配
    if d.device_type in (METRO_TYPE, "计量箱（单相）", "计量箱（三相）", "单相", "三相"):
        return "meter"
    return "unassigned"


def _is_0pole(d: Device) -> bool:
    return d.device_type == TYPE_POLE and d.name.strip() == "0杆"


def _build_line_pole_map(pole_nodes: Dict[str, TreeNode]) -> Dict[str, Optional[str]]:
    """每个台区的杆塔 → 默认上级杆塔名（规则链推导）。"""
    result = {}
    for line, pole_names in _group_pole_names(pole_nodes).items():
        has_pole0 = "0杆" in pole_names
        for pn in pole_names:
            result[(line, pn)] = _pole_parent_by_rule(pn, pole_names, has_pole0)
    return result


def _group_pole_names(pole_nodes: Dict[str, TreeNode]) -> Dict[str, set]:
    out = {}
    for key, node in pole_nodes.items():
        out.setdefault(node_line_of(key), set()).add(key[1])
    return out


def node_line_of(key: tuple) -> str:
    return key[0]


def build_tree(devices: List[Device],
               override_path: Optional[str] = None,
               overrides: Optional[dict] = None) -> List[dict]:
    """构建台区层级树。返回 JSON 可序列化的树结构列表。

    返回:
      [ {台区树 dict}, ... ]
      节点 dict: {id, name, type, level, lon, lat, auto_parent, override_parent,
                  is_transformer, connected: bool, children: [节点, ...]}
    """
    global _OVERRIDE_FILE
    _OVERRIDE_FILE = override_path
    ovr = _load_overrides(override_path, overrides)

    lines: Dict[str, list] = {}
    for d in devices:
        lines.setdefault(d.line or "未知", []).append(d)

    result = []
    for line_no, devs in lines.items():
        result.append(_build_line_tree(line_no, devs, ovr))
    return result


def _build_line_tree(line_no: str, devs: list, ovr: dict) -> dict:
    buckets = {"transformer": [], "pole": [], "access": [], "meter": [], "unassigned": []}
    for d in devs:
        buckets[_classify_type(d)].append(d)

    # 台区中心
    c_lon = sum(d.lon for d in devs) / len(devs) if devs else 0
    c_lat = sum(d.lat for d in devs) / len(devs) if devs else 0

    # L1 变压器（0杆 所在行生成）
    tx_dev = next((d for d in devs if d.device_type == TYPE_TRANSFORMER), None)
    tx_node = None
    if tx_dev:
        tx_name = tx_dev.name.strip()
        tx_node = _make_node(
            line_no, tx_name, 1, tx_dev,
            auto_parent=None, is_transformer=True, ovr=ovr,
            dev_key=_dev_key(tx_dev))

    # L2 杆塔（含 0杆）
    poles = buckets["pole"]
    pole_nodes: Dict[tuple, TreeNode] = {}
    pole_names = {p.name.strip() for p in poles}
    for d in poles:
        pn = d.name.strip()
        key = (line_no, pn)
        if pn == "0杆":
            # 0杆 挂变压器（变压器前端）；没有变压器则挂台区
            auto = tx_name if tx_dev else None
        else:
            auto = _pole_parent_by_rule(pn, pole_names, "0杆" in pole_names)
            if auto is None:
                auto = tx_name if tx_dev else "未分配"
        node = _make_node(line_no, pn, 2, d, auto_parent=auto, ovr=ovr,
                          dev_key=_dev_key(d))
        pole_nodes[key] = node

    # L3 接入点：就近杆塔（同台区），无杆塔就就近接入点，都没有 → 未分配
    pole_pos = [(p.name.strip(), p.lon, p.lat) for p in poles]
    access_pos = [(a.name.strip(), a.lon, a.lat) for a in buckets["access"]]
    access_nodes = []
    for d in buckets["access"]:
        parent = _nearest_name(d.lon, d.lat, pole_pos)
        if parent is None:
            parent = _nearest_name(d.lon, d.lat, access_pos, exclude=d.name.strip())
        if parent is None:
            parent = "未分配"
        node = _make_node(line_no, d.name.strip(), 3, d, auto_parent=parent,
                          ovr=ovr, dev_key=_dev_key(d))
        access_nodes.append(node)

    # L4 计量箱：末端，只能挂接入点；就近接入点，没有 → 未分配
    meter_nodes = []
    for d in buckets["meter"]:
        parent = _nearest_name(d.lon, d.lat, access_pos)
        if parent is None:
            parent = "未分配"
        node = _make_node(line_no, d.name.strip(), 4, d, auto_parent=parent,
                          ovr=ovr, dev_key=_dev_key(d))
        meter_nodes.append(node)

    # 未识别设备（其他类型）
    unassigned_nodes = []
    for d in buckets["unassigned"]:
        node = _make_node(line_no, d.name.strip(), 9, d, auto_parent="未分配",
                          ovr=ovr, dev_key=_dev_key(d))
        unassigned_nodes.append(node)

    # 组装树
    line_dict = _to_dict(_make_node(
        line_no, line_no, 0,
        Device(id=-1, name=line_no, line=line_no, lon=c_lon, lat=c_lat,
               device_type="", district=line_no, src_file=""),
        auto_parent=None, ovr=ovr,
        center=(c_lon, c_lat)))

    # 台区下固定放一个「未分配设备」占位节点（无坐标 → 不可飞行）
    unassigned_bucket = {
        "id": -(abs(hash(line_no + "UA")) % 100000), "name": "未分配",
        "level": 9, "lon": None, "lat": None,
        "auto_parent": None, "override_parent": None, "parent": None,
        "is_transformer": False, "label": "未分配设备", "connected": False,
        "children": [],
    }
    line_dict["children"].append(unassigned_bucket)

    def mount(parent_dict, child_dict):
        pname = child_dict.get("override_parent") or child_dict.get("auto_parent")
        if pname in (None, "", "未分配"):
            unassigned_bucket["children"].append(child_dict)
        else:
            target = _find_node_by_name(line_dict, pname)
            if target is not None and target is not line_dict:
                target["children"].append(child_dict)
            else:
                unassigned_bucket["children"].append(child_dict)

    # 变压器先挂（0杆 的父）；变压器自身 auto_parent=None → 挂台区根（不进未分配桶）
    if tx_node:
        td = _to_dict(tx_node)
        line_dict["children"].insert(0, td)   # 变压器排台区直接子（最前）
    for pn in pole_nodes.values():
        mount(line_dict, _to_dict(pn))
    for an in access_nodes:
        mount(line_dict, _to_dict(an))
    for mn in meter_nodes:
        mount(line_dict, _to_dict(mn))
    for un in unassigned_nodes:
        mount(line_dict, _to_dict(un))

    # 排序：同层先按名称（数字序列排前），未分配桶内的子节点也要排
    def _rank(n: dict):
        lvl = n.get("level", 9)
        name = n.get("name", "")
        m = re.match(r"^([A-Za-z]*)(\d+)", name)
        key_num = int(m.group(2)) if m else 999
        return (lvl, key_num, name)

    def _rec(n):
        n["children"].sort(key=_rank)
        for c in n["children"]:
            _rec(c)

    _rec(line_dict)
    return line_dict


def _find_node_by_name(root: dict, name: str):
    if root.get("name") == name:
        return root
    for c in root.get("children", []):
        r = _find_node_by_name(c, name)
        if r is not None:
            return r
    return None


def _sort_children(node: dict):
    def _rank(n: dict):
        lvl = n.get("level", 9)
        name = n.get("name", "")
        # 数字序列排前：按解析数字
        m = re.match(r"^([A-Za-z]*)(\d+)", name)
        key_num = int(m.group(2)) if m else 999
        return (lvl, key_num, name)
    node["children"].sort(key=_rank)
    for c in node["children"]:
        _sort_children(c)


def _nearest_name(lon: float, lat: float, pos_list: list, exclude: Optional[str] = None) -> Optional[str]:
    """pos_list: [(name, lon, lat), ...] → 最近者名称（排除 exclude）。"""
    best, best_d = None, None
    for name, plon, plat in pos_list:
        if name == exclude:
            continue
        d = _dist_m(lon, lat, plon, plat)
        if best_d is None or d < best_d:
            best, best_d = name, d
    return best


def _make_node(line_no: str, name: str, level: int,
               d: Optional[Device], auto_parent: Optional[str],
               ovr: dict, is_transformer: bool = False,
               dev_key: Optional[str] = None,
               center: Optional[tuple] = None) -> TreeNode:
    key = dev_key or (f"{line_no}|L{level}|{name}" if level in (0, 1) else _dev_key(d))
    node = TreeNode(
        device_id=d.id if d and d.id > 0 else -(abs(hash(line_no)) % 100000),
        name=name, level=level,
        lon=(center[0] if center else (d.lon if d else 0.0)) if d or center else None,
        lat=(center[1] if center else (d.lat if d else 0.0)) if d or center else None,
        auto_parent=auto_parent, is_transformer=is_transformer,
    )
    ov = ovr.get(key)
    if ov:
        node.override_parent = ov.get("parent")
    return node


def _to_dict(n: TreeNode) -> dict:
    return {
        "id": n.device_id, "name": n.name, "level": n.level,
        "lon": n.lon, "lat": n.lat,
        "auto_parent": n.auto_parent, "override_parent": n.override_parent,
        "parent": n.parent,
        "is_transformer": n.is_transformer,
        "label": n.label,
        "connected": n.parent not in (None, "未分配"),
        "children": [_to_dict(c) for c in n.children],
    }


# ---------- 手动挂接 override ----------

def _load_overrides(path: Optional[str], inline: Optional[dict]) -> dict:
    if inline:
        return inline
    if path:
        try:
            import pathlib
            p = pathlib.Path(path)
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_override(override_path: str, dev_key, parent_name: Optional[str],
                  parent_level: int) -> None:
    """持久化一条手动挂接：dev_key → parent。

    dev_key 接受 tuple/list/str — 内部统一转 string key 写入 JSON（JSON 不支持 tuple-key）。
    """
    import pathlib
    if isinstance(dev_key, (tuple, list)):
        dev_key = "|".join(str(x) for x in dev_key)
    p = pathlib.Path(override_path)
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if parent_name is None:
        data.pop(dev_key, None)
    else:
        data[dev_key] = {"parent": parent_name, "level": parent_level}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
