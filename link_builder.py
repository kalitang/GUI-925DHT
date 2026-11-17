"""电力设备连接关系构建（用户 2026-09-28 修订规则）

层级拓扑（每个台区内）：

    变压器 (A)  ← 无前端（作为树根）
       ↓
    杆塔   (B)  ← 前端由命名规则决定（见下）
       ↓
    接入点 (C)  ← 前端必须是距离最近的杆塔（多个接入点可共享同一杆塔）
       ↓
    计量箱 (D)  ← 前端必须是距离最近的接入点

杆塔命名解析规则（按用户 2026-09-28 修订）：
    1) "0杆"        → 母杆（顶层，无数字前端，作为 A→B 的最上级目标）
    2) 纯数字"A"     → 单主线：1杆前端=0杆，2杆前端=1杆，3杆前端=2杆（数字顺序串联）
                       注意：纯数字 "10杆" 也走纯数字规则
    3) 多主线"字母+数字"：
                       A1杆、B1杆、C1杆… 均连接 0杆（母杆）
                       A2杆前端=A1杆，A3前端=A2…
                       B2前端=B1杆，B3前端=B2…
                       （每条字母线内部按数字顺序串联到 0杆）
    4) 支线"数字-数字-…"：
                       1-1杆 前端=1杆
                       1-2杆 前端=1-1杆（数字顺序串联，但**所有 dash 派生杆塔都从同根数字起算**）
                       1-2-1杆 前端=1-2杆
                       1-2-1-1杆 前端=1-2-1杆
                       （从左到右找最近的"少一节 dash 的兄弟"，无兄弟则回溯到上一级数字）
    5) 支线在多主线内："A1-1杆" 走 A1 线的支线规则，"A1-2杆"前端=A1-1杆

约束：
    - 接入点（墙支架）前端必须有且只有一个最近的杆塔（多个接入点可共享同一杆塔）
    - 计量箱 前端必须有且只有一个最近的接入点
    - 变压器 无前端

返回：list of (parent_id, child_id, level)
    level: 1 = A→B 或 B→B
           2 = B→C
           3 = C→D
"""
from __future__ import annotations
import math
import re
from typing import List, Tuple, Dict, Optional

from excel_loader import (
    Device, TYPE_TRANSFORMER, TYPE_POLE, TYPE_ACCESS_POINT, TYPE_METER_BOX
)


# 真实地理距离换算
def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _nearest(src: Device, candidates: List[Device], max_dist_m: float = 500.0) -> Optional[Device]:
    if not candidates:
        return None
    best, best_d = None, max_dist_m + 1
    for c in candidates:
        d = _haversine_m(src.lon, src.lat, c.lon, c.lat)
        if d < best_d:
            best_d = d
            best = c
    return best


# ===========================================================================
# 杆塔命名解析
# ===========================================================================
# 杆塔名称形如：
#   "0杆" / "0号杆"        → 母杆
#   "1杆" / "1号杆"         → 纯数字（单主线）
#   "A1杆" / "A1号杆"       → 多主线（字母开头）
#   "A1-1杆" / "A1-2杆"     → 多主线内的支线
#   "1-1杆" / "1-2-1杆"     → 单主线内的支线
# 名称尾部常见的"杆" / "号杆" / "号电杆" 被剥离后再解析。

_NUMBER_TAIL_RE = re.compile(r'(杆|号杆|号电杆|号)$')

# 纯数字主干：例如 "1杆" → segments=['1']
# 多主线主干：例如 "A1杆" → segments=['A','1']
# 支线：例如 "1-1杆" → segments=['1','1']；"A1-2-1杆" → ['A','1','2','1']

_MAINLINE_RE = re.compile(r'^([A-Z]+)(\d+)$')            # 多主线主干：字母 + 数字
_DIGIT_RE    = re.compile(r'^(\d+)$')                     # 纯数字主干：仅数字
# 支线：主干 + dash + 段（每段是 [纯数字] | [字母+数字] | [纯字母子线]）
# 例：A1-A-1 → trunk='A1' + rest='-A-1' → parts=['A-1']；A1-A-1-1 → trunk='A1' + rest='-A-1-1' → parts=['A-1-1']? 
# 但用户图中 1-2-A2-1 是 4 段，需要按"外层 dash"切。区分字母子线段内部的"-"和外层 dash 比较难。
# 简化设计：dash 后段**按外 dash 切**，每个段可以是：
#   - 纯数字 N（如 '1', '2', '10'）
#   - 字母子线 L（如 'A', 'B', 'C'）
#   - 字母子线 + 序号 L-N（如 'A-1', 'A-2', 'B-3'）—— 在段内按"-"二次切
_BRANCH_RE   = re.compile(r'^((?:[A-Z]+\d+|\d+))((?:-[A-Z0-9]+)+)$')  # 支线：主干 + dash + 段(≥1)
_LETTER_SUB_RE = re.compile(r'^([A-Z]+)$')                  # 字母子线段（纯字母）
_LETTER_SUB_NUM_RE = re.compile(r'^([A-Z]+)-(\d+)$')        # 字母子线 + 数字序号
_DIGIT_SUB_RE = re.compile(r'^(\d+)$')                       # 纯数字段
# 注意：用户 2026-09-28 规则 —— "A1-A1杆、A1-A2杆" 中 dash 后是数字 1/2 按数字顺序串联
# → dash 后必须是纯数字才能走"同级减 1"，所以 trunk 保持 A1（不拆字母）
# → 但 seg_to_pole 索引的 key 用 "A1" 而非 "A-1"
# 这导致：主干 A1 解析为 ['A1']（单段），而支线 A1-A1 解析为 ['A1','1']
# 为了一致性，主干 A1 也走 ['A1']，父查找时 key="A1" 命中。


def _parse_pole_name(raw_name: str) -> Optional[List[str]]:
    """解析杆塔名 → segments 列表。

    设计：
      - 主干（'A1' / '1'）按字母+数字完整拆段（统一的双段形式便于主干判断）
      - dash 后段按"外层 dash"切分；每个段内部如果是 'A-1' 形式（字母子线+序号），
        在段内进一步按 '-' 拆为 ['A','1'] 或保留为完整字符串（视段类型而定）

    返回格式统一为 [['L','N','A','1',...] ]，所有数字都独立成段，字母也是。

    例：
      '0杆'        → ['0']
      '1杆'        → ['1']
      'A1杆'       → ['A','1']
      'A1-1杆'     → ['A','1','1']
      'A1-A杆'     → ['A','1','A']           (A 子线根)
      'A1-A-1杆'   → ['A','1','A','1']       (A 子线 1 号)
      'A1-A-1-1杆' → ['A','1','A','1','1']   (A 子线 1 号下的 1 号)
      'A1-A-2杆'   → ['A','1','A','2']
      'A1-A1杆'    → ['A','1','A','1']       (同 A1-A-1)
      '1-1-A杆'    → ['1','1','A']
      '1-2-A2杆'   → ['1','2','A','2']
      'B1-C1杆'    → ['B','1','C','1']
      'A1-B1-A-1杆'→ ['A','1','B','1','A','1']

    前端查找（_parent_segments）的语义：
      - 单段 → 主干规则
      - 多段：末段若是纯数字 → 同级减 1；末段若是字母+数字混合 → 同级减 1；
              末段若是纯字母 → dash 数减 1（首字母子线根挂主干）
    """
    if not raw_name:
        return None
    n = str(raw_name).strip()
    n = _NUMBER_TAIL_RE.sub('', n)
    if not n:
        return None

    # 支线模式：主干 + dash + 段(≥1)
    m = _BRANCH_RE.match(n)
    if m:
        trunk_str = m.group(1)               # 'A1' / '1'
        rest = m.group(2)                    # '-1-1' / '-A1' / '-A-1-1' / '-A-1-2-1'
        # 按外层 dash 切
        outer_parts = rest[1:].split('-')    # ['1','1'] / ['A1'] / ['A','1','1'] / ['A','1','2','1']
        # 主干按字母+数字拆：A1 → ['A','1']；1 → ['1']
        trunk_segs = _split_alnum(trunk_str)
        # dash 后段：每个外段可能是：
        #   - 纯数字 → 拆成 ['N']（合并为单段数字）
        #   - 纯字母 → ['L']
        #   - 字母+数字 → 拆成 ['L','N']
        #   - 字母+数字+... （多次 dash 嵌套）→ 多次拆分（按"-"切）
        all_parts: List[str] = []
        for p in outer_parts:
            # 段内允许有 dash（如 'A-1'），按 '-' 切并按"字母+数字"拆
            inner_parts = p.split('-')
            for ip in inner_parts:
                all_parts.extend(_split_alnum(ip))
        return trunk_segs + all_parts

    # 多主线主干：A1 / B12
    if _MAINLINE_RE.match(n):
        m2 = _MAINLINE_RE.match(n)
        return _split_alnum(m2.group(1) + m2.group(2))   # A1 → ['A','1']

    # 纯数字主干：1 / 12
    if _DIGIT_RE.match(n):
        return _split_alnum(n)   # 12 → ['12']

    return None


def _split_alnum(s: str) -> List[str]:
    """把 'A1' → ['A','1']；'12' → ['12']；'A' → ['A']；'AB12' → ['AB','12']。
    拆分规则：先找连续字母段（可能多个字母），再找连续数字段（可能多位数）。
    """
    if not s:
        return []
    m = re.match(r'^([A-Z]+)(\d+)$', s)
    if m:
        return [m.group(1), m.group(2)]
    if s.isdigit():
        return [s]
    if s.isalpha():
        return [s]
    return [s]


def _is_main_pole(segments: List[str]) -> bool:
    """是否是多主线主干（A1、B2…）或母杆（0）或单主线主干（1、2…）。
    注意：单主线主干 '1' 也算主干，但其父级是 '0'（母杆）。
    """
    return len(segments) == 1 or (len(segments) == 2 and segments[0].isalpha())


def _parent_segments(segments: List[str]) -> Optional[List[str]]:
    """根据命名规则返回该杆塔的"前端 segments"。

    用户 2026-09-28 规则（手写笔记图示）：
      - ['0']                  → 母杆，**无前端**
      - 纯数字主干 ['N']:
          * N=1 → ['0']         (1 杆前端是 0 杆)
          * N≥2 → ['N-1']       (N 杆前端是 (N-1) 杆)
      - 多主线主干 ['A','N']:    （字母主干）
          * N=1 → ['0']         (A1 杆前端是 0 杆)
          * N≥2 → ['A',str(N-1)] (AN 杆前端是 A(N-1) 杆)
      - 支线（dash 后 ≥1 段，每段按字母+数字完整拆）：
          规则：dash 数减 1（即剥离最后一段），直到末段是纯数字才做"同级数字减 1"
          * ['1','1']  → ['1']                    (1-1 父 = 1)
          * ['1','2']  → ['1','1']                (1-2 父 = 1-1)
          * ['2','1','2'] → ['2','1']             (2-1-2 父 = 2-1)
          * ['2','1']  → ['2']                    (2-1 父 = 2)
          * ['2','3']  → ['2','2']                (2-3 父 = 2-2)
          * ['A','1','1']  → ['A','1']            (A1-1 父 = A1)
          * ['A','1','2']  → ['A','1','1']        (A1-2 父 = A1-1)
          * ['A','1','A','1'] → ['A','1','A']?    (A1-A-1 父 = A1-A → 但 'A' 不是合法段)

    设计原则（按用户规则简化）：
      - 末段若是纯数字 N（N≥2）→ 同级数字减 1
      - 末段若是纯数字 1 → dash 数减 1（剥离该段）
      - 末段若是字母（无数字）→ dash 数减 1
      - 末段若是字母+数字混合 ['L', 'N'] → 整体视为一个"数字支线段"，按数字 N 减 1
        但这要求 dashes 数 >= 2（dash 数减 1 仍保留该字母数字段）
        例：['A','1','A','2'] 末段 ['A','2'] → 减 N=2→1 → ['A','1','A','1']
        例：['A','1','A','1'] 末段 ['A','1'] → N=1 → dash 数减 1 → ['A','1'] (父 = A1)
    """
    if not segments:
        return None
    if segments == ['0']:
        return None  # 母杆无前端

    # 单段
    if len(segments) == 1:
        first = segments[0]
        if first.isdigit():
            n = int(first)
            if n == 1:
                return ['0']
            if n >= 2:
                return [str(n - 1)]
        return None

    # 双段：字母主干 ['A','N'] 或 数字-数字支线 ['1','1'] / ['1','2']
    if len(segments) == 2:
        first, second = segments[0], segments[1]
        # 字母+数字 → 字母主线（如 A1, B12）
        if first.isalpha() and second.isdigit():
            n = int(second)
            if n == 1:
                return ['0']
            if n >= 2:
                return [first, str(n - 1)]
            return None
        # 数字+数字 → 数字主干 + 数字支线（如 1-1, 1-2）
        if first.isdigit() and second.isdigit():
            n = int(second)
            if n == 1:
                return [first]          # 1-1 父 = 1（首杆挂主干）
            if n >= 2:
                return [first, str(n - 1)]  # 1-2 父 = 1-1（同级减1）
            return None
        return None

    # 多段（≥3）：支线
    if len(segments) >= 3:
        # 末段是字母+数字对 ['L', 'N']（如 1-A-1、A1-A-1、B1-C2）：
        #   N=1  → 首杆直接挂主干（跳过字母子线根 L）→ segments[:-2]
        #   N≥2  → 父 = 上一同号 → segments[:-1] + [N-1]
        if segments[-2].isalpha() and segments[-1].isdigit():
            n = int(segments[-1])
            if n == 1:
                return segments[:-2]
            return segments[:-1] + [str(n - 1)]
        # 末段是纯数字（如 1-2、1-2-1）：
        #   N=1  → 父 = segments[:-1]（首杆挂上一级）
        #   N≥2  → 父 = segments[:-1] + [N-1]（同级数字减 1）
        if segments[-1].isdigit():
            n = int(segments[-1])
            if n == 1:
                return segments[:-1]
            return segments[:-1] + [str(n - 1)]
        # 末段是纯字母（如 1-1-A、A1-A，字母子线根）→ dash 数减 1
        return segments[:-1]

    return None


# ===========================================================================
# 主入口：build_links
# ===========================================================================
def build_links(devices: List[Device], max_link_dist_m: float = 500.0) -> List[Tuple[int, int, int]]:
    """返回连线列表 [(parent_id, child_id, level), ...]

    算法（按台区分组）：
      1. 按 line 字段分组
      2. 每台区内：
         - 变压器 → 最近的杆塔（level=1，A→B）
         - 杆塔    → 命名规则决定的前端杆塔（level=1，B→B / A→B）
                    * 命名无法解析的杆塔（无规则匹配）走"就近兜底"：找最近的其他杆塔
         - 接入点 → 距离最近的杆塔（level=2，B→C）
                    * 多个接入点可共享同一杆塔（用户 2026-09-28 允许）
                    * 每个接入点仍必须只能连 1 个最近的杆塔（不能同时连多个）
         - 计量箱 → 距离最近的接入点（level=3，C→D）
      3. 兜底：完全没有变压器时，杆塔之间就近串联
    """
    if not devices:
        return []

    # 按台区分组，再按类型分桶
    by_line: Dict[str, Dict[str, List[Device]]] = {}
    for d in devices:
        by_line.setdefault(d.line or "_unknown", {}).setdefault(d.device_type, []).append(d)

    # 台区 id 集合（跨区校验用：每条连线的两端必须属于同一台区）
    line_of: Dict[int, str] = {d.id: (d.line or "_unknown") for d in devices}

    links: List[Tuple[int, int, int]] = []
    seen_edges: set = set()
    cross_line_blocked = 0

    def _add(parent: Device, child: Device, level: int) -> None:
        nonlocal cross_line_blocked
        # 用户 2026-09-28：每个 Excel 文件的设备限定在同一台区，
        # 所有连线只允许连接本台区设备，不允许跨区连接
        if line_of.get(parent.id) != line_of.get(child.id):
            cross_line_blocked += 1
            return
        e = (parent.id, child.id, level)
        if e in seen_edges:
            return
        seen_edges.add(e)
        links.append(e)

    if cross_line_blocked == 0 and devices:
        pass
    # 跨区阻断计数在返回前统一打日志

    for line, by_type in by_line.items():
        transformers: List[Device] = by_type.get(TYPE_TRANSFORMER, [])
        poles:        List[Device] = list(by_type.get(TYPE_POLE, []))
        aps:          List[Device] = list(by_type.get(TYPE_ACCESS_POINT, []))
        boxes:        List[Device] = list(by_type.get(TYPE_METER_BOX, []))

        # 给每个杆塔建一个 segments 索引
        pole_segments: Dict[int, List[str]] = {}
        for p in poles:
            seg = _parse_pole_name(p.name or '')
            if seg is not None:
                pole_segments[p.id] = seg

        # 按 segments 元组建反向索引（用于按名查父杆塔）
        # 注意：'A1' 与 'A-1' 解析后 segments 相同（['A','1']），属同名冲突
        seg_to_pole: Dict[tuple, Device] = {}
        for p in poles:
            seg = pole_segments.get(p.id)
            if seg is not None:
                key = tuple(seg)
                # 同名冲突时：保留第一个（理想数据不应有同名杆塔）
                seg_to_pole.setdefault(key, p)

        # 1) 变压器 → 最近的杆塔（level=1，A→B；变压器唯一）
        for tx in transformers:
            pole = _nearest(tx, poles, max_link_dist_m)
            if pole:
                _add(tx, pole, 1)

        # 2) 杆塔 → 命名规则决定的前端杆塔（level=1，B→B / 母杆接收多线）
        #    命名能解析 → 按 segments 元组查父
        #    若兄弟杆塔不存在（同级减 1 后没找到）→ 回退到上一级 dash
        #    命名解析失败 → 就近兜底（找最近的其他杆塔）
        for pole in poles:
            seg = pole_segments.get(pole.id)
            parent_pole: Optional[Device] = None
            if seg is not None:
                parent_seg = _parent_segments(seg)
                if parent_seg is not None:
                    # 候选链：完整父级 → 逐级剥 dash（末段；字母+数字对剥 2 段）→ 主干
                    candidates: List[List[str]] = [parent_seg]
                    cur = list(parent_seg)
                    while len(cur) > 2:
                        # 字母+数字对（末两段 ['L','N']）整体剥除；纯数字段剥 1 段
                        if len(cur) >= 2 and cur[-2].isalpha() and cur[-1].isdigit():
                            nxt = cur[:-2]
                        else:
                            nxt = cur[:-1]
                        if not nxt or nxt in candidates:
                            break
                        candidates.append(nxt)
                        cur = nxt
                    for cand in candidates:
                        parent_pole = seg_to_pole.get(tuple(cand))
                        if parent_pole is not None:
                            break
            if parent_pole is None:
                # 兜底：找最近的其他杆塔
                others = [p for p in poles if p.id != pole.id]
                parent_pole = _nearest(pole, others, max_link_dist_m)
            if parent_pole:
                _add(parent_pole, pole, 1)

        # 3) 接入点 → 距离最近的杆塔（level=2，B→C）
        #    用户 2026-09-28 约束：每个接入点有且只能连 1 个最近的杆塔
        #                     但同一杆塔可被多个接入点作为前端连接
        #    实现：每个接入点独立找最近杆塔（不去重杆塔端）
        if poles and aps:
            for ap in aps:
                pole = _nearest(ap, poles, max_link_dist_m)
                if pole:
                    _add(pole, ap, 2)

        # 4) 计量箱 → 距离最近的接入点（level=3，C→D）
        if aps and boxes:
            for box in boxes:
                ap = _nearest(box, aps, max_link_dist_m)
                if ap:
                    _add(ap, box, 3)

    if cross_line_blocked:
        import logging
        logging.getLogger(__name__).warning(
            "build_links: 阻断 %d 条跨台区连线（用户 2026-09-28：连线只允许本台区）",
            cross_line_blocked)

    return links


def link_summary(links: List[Tuple[int, int, int]]) -> Dict[str, int]:
    s = {"total": 0, "AB": 0, "BC": 0, "CD": 0}
    for _, _, lvl in links:
        s["total"] += 1
        if lvl == 1:
            s["AB"] += 1
        elif lvl == 2:
            s["BC"] += 1
        elif lvl == 3:
            s["CD"] += 1
    return s


# ===========================================================================
# 单元测试（演示命名规则解析 + 连线结果）
# ===========================================================================
def pole_segments_str(d: Device) -> str:
    seg = _parse_pole_name(d.name or '')
    return '-'.join(seg) if seg else '???'


def parent_str(d: Device) -> str:
    seg = _parse_pole_name(d.name or '')
    if not seg:
        return '?'
    parent_seg = _parent_segments(seg)
    return '-'.join(parent_seg) if parent_seg else '(母杆/根)'


if __name__ == "__main__":
    # 测试 1：纯数字主干 + 支线
    print("=== 测试 1：纯数字主干 + 支线 ===")
    ds1 = [
        Device(id=1, lon=119.5, lat=29.5, name='0杆', line='T1', device_type=TYPE_POLE),
        Device(id=2, lon=119.5001, lat=29.5001, name='1杆', line='T1', device_type=TYPE_POLE),
        Device(id=3, lon=119.5002, lat=29.5002, name='2杆', line='T1', device_type=TYPE_POLE),
        Device(id=4, lon=119.5003, lat=29.5003, name='3杆', line='T1', device_type=TYPE_POLE),
        Device(id=5, lon=119.50010, lat=29.50010, name='1-1杆', line='T1', device_type=TYPE_POLE),
        Device(id=6, lon=119.50011, lat=29.50011, name='1-2杆', line='T1', device_type=TYPE_POLE),
        Device(id=7, lon=119.50012, lat=29.50012, name='1-2-1杆', line='T1', device_type=TYPE_POLE),
        Device(id=8, lon=119.50013, lat=29.50013, name='1-2-2杆', line='T1', device_type=TYPE_POLE),
        Device(id=9, lon=119.50014, lat=29.50014, name='1-2-1-1杆', line='T1', device_type=TYPE_POLE),
    ]
    for d in ds1:
        print(f"  {d.name:10s} seg={pole_segments_str(d):10s}  parent={parent_str(d)}")
    links = build_links(ds1, max_link_dist_m=10000)
    print("  连线:")
    for l in sorted(links):
        p_name = next((d.name for d in ds1 if d.id == l[0]), '?')
        c_name = next((d.name for d in ds1 if d.id == l[1]), '?')
        print(f"    {p_name:8s} → {c_name:8s} (level={l[2]})")
    print("  summary:", link_summary(links))

    # 测试 5：完整多主线支线（按用户原规则：A1-A2 父=A1-A1）
    print("\n=== 测试 5：A1-A2 父=A1-A1（同 dash 数、按数字顺序串联）===")
    ds5 = [
        Device(id=1, lon=119.5, lat=29.5, name='0杆', line='T1', device_type=TYPE_POLE),
        Device(id=2, lon=119.5001, lat=29.5001, name='A1杆', line='T1', device_type=TYPE_POLE),
        Device(id=3, lon=119.5002, lat=29.5002, name='A1-A1杆', line='T1', device_type=TYPE_POLE),
        Device(id=4, lon=119.5003, lat=29.5003, name='A1-A2杆', line='T1', device_type=TYPE_POLE),     # 期望父=A1-A1
        Device(id=5, lon=119.5004, lat=29.5004, name='A1-A2-1杆', line='T1', device_type=TYPE_POLE),  # 期望父=A1-A2
        Device(id=6, lon=119.5005, lat=29.5005, name='A1-A2-1-1杆', line='T1', device_type=TYPE_POLE),  # 期望父=A1-A2-1
        Device(id=7, lon=119.5006, lat=29.5006, name='A1-A2-2杆', line='T1', device_type=TYPE_POLE),  # 期望父=A1-A2-1（同级减1，A1-A2-1 存在）
        Device(id=8, lon=119.5007, lat=29.5007, name='A1-A3杆', line='T1', device_type=TYPE_POLE),    # 期望父=A1-A2
    ]
    for d in ds5:
        print(f"  {d.name:18s} seg={pole_segments_str(d):24s}  parent={parent_str(d)}")
    links = build_links(ds5, max_link_dist_m=10000)
    print("  连线:")
    for l in sorted(links):
        p_name = next((d.name for d in ds5 if d.id == l[0]), '?')
        c_name = next((d.name for d in ds5 if d.id == l[1]), '?')
        print(f"    {p_name:14s} → {c_name:18s} (level={l[2]})")
    print("  summary:", link_summary(links))

    # 测试 4：多主线内支线（dash 后可以是字母或数字）
    print("\n=== 测试 4：多主线内支线（A1-A1 / A1-A2 / A1-A2-A1）===")
    ds4 = [
        Device(id=1, lon=119.5, lat=29.5, name='0杆', line='T1', device_type=TYPE_POLE),
        Device(id=2, lon=119.5001, lat=29.5001, name='A1杆', line='T1', device_type=TYPE_POLE),
        Device(id=3, lon=119.5002, lat=29.5002, name='A1-A1杆', line='T1', device_type=TYPE_POLE),
        Device(id=4, lon=119.5003, lat=29.5003, name='A1-A2杆', line='T1', device_type=TYPE_POLE),
        Device(id=5, lon=119.5004, lat=29.5004, name='A1-A2-1杆', line='T1', device_type=TYPE_POLE),
        Device(id=6, lon=119.5005, lat=29.5005, name='A1-A2-A1杆', line='T1', device_type=TYPE_POLE),
        Device(id=7, lon=119.5006, lat=29.5006, name='B1杆', line='T1', device_type=TYPE_POLE),
        Device(id=8, lon=119.5007, lat=29.5007, name='B1-B1杆', line='T1', device_type=TYPE_POLE),
    ]
    for d in ds4:
        print(f"  {d.name:14s} seg={pole_segments_str(d):20s}  parent={parent_str(d)}")
    links = build_links(ds4, max_link_dist_m=10000)
    print("  连线:")
    for l in sorted(links):
        p_name = next((d.name for d in ds4 if d.id == l[0]), '?')
        c_name = next((d.name for d in ds4 if d.id == l[1]), '?')
        print(f"    {p_name:14s} → {c_name:14s} (level={l[2]})")
    print("  summary:", link_summary(links))

    # 测试 3：完整场景（含变压器 + 接入点 + 计量箱 + 多主线支线）
    print("\n=== 测试 3：完整场景 ===")
    ds3 = [
        # 变压器
        Device(id=1, lon=119.500, lat=29.500, name='TX-1', line='T1', device_type=TYPE_TRANSFORMER),
        # 母杆 + 主线 + 支线
        Device(id=2, lon=119.5001, lat=29.5001, name='0杆', line='T1', device_type=TYPE_POLE),
        Device(id=3, lon=119.5002, lat=29.5002, name='A1杆', line='T1', device_type=TYPE_POLE),
        Device(id=4, lon=119.5003, lat=29.5003, name='A2杆', line='T1', device_type=TYPE_POLE),
        Device(id=5, lon=119.5004, lat=29.5004, name='B1杆', line='T1', device_type=TYPE_POLE),
        Device(id=6, lon=119.5005, lat=29.5005, name='B2杆', line='T1', device_type=TYPE_POLE),
        Device(id=7, lon=119.5006, lat=29.5006, name='A1-1杆', line='T1', device_type=TYPE_POLE),
        Device(id=8, lon=119.5007, lat=29.5007, name='A1-2杆', line='T1', device_type=TYPE_POLE),
        # 接入点（验证：同一杆塔可被多个接入点连；接入点只连最近的杆塔）
        Device(id=9,  lon=119.50020, lat=29.50020, name='AP-1', line='T1', device_type=TYPE_ACCESS_POINT),
        Device(id=10, lon=119.50021, lat=29.50021, name='AP-2', line='T1', device_type=TYPE_ACCESS_POINT),  # 离 A2 最近
        Device(id=11, lon=119.50060, lat=29.50060, name='AP-3', line='T1', device_type=TYPE_ACCESS_POINT),
        Device(id=12, lon=119.50061, lat=29.50061, name='AP-4', line='T1', device_type=TYPE_ACCESS_POINT),  # 离 A1-1 最近
        # 计量箱（每个连最近的接入点）
        Device(id=13, lon=119.500200, lat=29.500200, name='Box-1', line='T1', device_type=TYPE_METER_BOX),
        Device(id=14, lon=119.500210, lat=29.500210, name='Box-2', line='T1', device_type=TYPE_METER_BOX),
        Device(id=15, lon=119.500600, lat=29.500600, name='Box-3', line='T1', device_type=TYPE_METER_BOX),
    ]
    for d in ds3:
        if d.device_type == TYPE_POLE:
            print(f"  {d.name:8s} seg={pole_segments_str(d):12s}  parent={parent_str(d)}")
    links = build_links(ds3, max_link_dist_m=10000)
    print("  连线:")
    for l in sorted(links):
        p_name = next((d.name for d in ds3 if d.id == l[0]), '?')
        c_name = next((d.name for d in ds3 if d.id == l[1]), '?')
        print(f"    {p_name:8s} → {c_name:8s} (level={l[2]})")
    print("  summary:", link_summary(links))
    ds2 = [
        Device(id=1, lon=119.5, lat=29.5, name='0杆', line='T1', device_type=TYPE_POLE),
        Device(id=2, lon=119.5001, lat=29.5001, name='A1杆', line='T1', device_type=TYPE_POLE),
        Device(id=3, lon=119.5002, lat=29.5002, name='A2杆', line='T1', device_type=TYPE_POLE),
        Device(id=4, lon=119.5003, lat=29.5003, name='B1杆', line='T1', device_type=TYPE_POLE),
        Device(id=5, lon=119.5004, lat=29.5004, name='B2杆', line='T1', device_type=TYPE_POLE),
        Device(id=6, lon=119.5005, lat=29.5005, name='C1杆', line='T1', device_type=TYPE_POLE),
        Device(id=7, lon=119.5006, lat=29.5006, name='A1-1杆', line='T1', device_type=TYPE_POLE),
        Device(id=8, lon=119.5007, lat=29.5007, name='A1-2杆', line='T1', device_type=TYPE_POLE),
        Device(id=9, lon=119.5008, lat=29.5008, name='A2-1杆', line='T1', device_type=TYPE_POLE),
    ]
    for d in ds2:
        print(f"  {d.name:10s} seg={pole_segments_str(d):10s}  parent={parent_str(d)}")
    links = build_links(ds2, max_link_dist_m=10000)
    print("  连线:")
    for l in sorted(links):
        p_name = next((d.name for d in ds2 if d.id == l[0]), '?')
        c_name = next((d.name for d in ds2 if d.id == l[1]), '?')
        print(f"    {p_name:8s} → {c_name:8s} (level={l[2]})")
    print("  summary:", link_summary(links))