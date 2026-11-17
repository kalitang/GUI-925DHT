"""台区供电覆盖范围计算

对每个台区的设备坐标求凸包，再外扩缓冲区，生成供电覆盖多边形。
结果以 GeoJSON FeatureCollection 输出，由 Cesium 端渲染为半透明多边形。

算法：
  1. 按台区（line 字段）分组设备坐标
  2. 每组 ≥ 3 个点 → 凸包（ConvexHull）；2 个点 → 线段；1 个点 → 小圆
  3. 凸包外扩 buffer_radius_m（默认 80m，模拟台区供电半径）
  4. 可选：限制最大覆盖面积（避免超大台区淹没整片区域）
  5. 输出 GeoJSON FeatureCollection（每个台区一个 Feature）
"""
from __future__ import annotations

import json
import math
from typing import List, Dict, Optional

from excel_loader import Device, TYPE_TRANSFORMER, TYPE_POLE, TYPE_ACCESS_POINT, TYPE_METER_BOX

try:
    from shapely.geometry import MultiPoint, Point
    from shapely.ops import unary_union
    from shapely import STRtree
    HAS_SHAPLEY = True
except ImportError:
    HAS_SHAPLEY = False


# 默认参数
DEFAULT_BUFFER_M = 80          # 外扩半径（米）— 台区供电覆盖模拟
MAX_AREA_KM2 = 5.0             # 单台区最大覆盖面积（km²），超过则截断
MIN_AREA_KM2 = 0.001          # 最小覆盖面积（km²），低于此值放大 buffer


def _km2_to_m2(km2: float) -> float:
    return km2 * 1_000_000.0


def compute_coverage(
    devices: List[Device],
    buffer_m: float = DEFAULT_BUFFER_M,
    max_area_km2: float = MAX_AREA_KM2,
) -> Dict:
    """计算每个台区的供电覆盖范围。

    返回 GeoJSON 格式：
    {
      "type": "FeatureCollection",
      "features": [
        {
          "type": "Feature",
          "properties": {
            "line": "台区名",
            "device_count": N,
            "buffer_m": 80,
            "area_km2": X,
            "centroid": [lon, lat]
          },
          "geometry": {
            "type": "Polygon",
            "coordinates": [[[lon, lat], ...]]
          }
        },
        ...
      ]
    }
    """
    if not devices:
        return {"type": "FeatureCollection", "features": []}

    # 按台区分组
    by_line: Dict[str, List[Device]] = {}
    for d in devices:
        line = d.line or "_unknown"
        by_line.setdefault(line, []).append(d)

    features = []

    for line, devs in by_line.items():
        # 收集坐标（经度, 纬度）
        coords = [(d.lon, d.lat) for d in devs]
        if not coords:
            continue

        if HAS_SHAPLEY:
            geom = _build_coverage_polygon(coords, buffer_m, max_area_km2)
        else:
            # 无 shapely 时的简化回退：包围盒 + 圆
            geom = _build_bbox_fallback(coords, buffer_m)

        if geom is None:
            continue

        # 计算面积（近似：经纬度 → 米换算）
        area_km2 = _polygon_area_km2(geom)
        centroid_lon, centroid_lat = _polygon_centroid(geom, coords)

        feat = {
            "type": "Feature",
            "properties": {
                "line": line,
                "device_count": len(devs),
                "buffer_m": buffer_m,
                "area_km2": round(area_km2, 4),
                "centroid": [round(centroid_lon, 6), round(centroid_lat, 6)],
            },
            "geometry": geom,
        }
        features.append(feat)

    return {"type": "FeatureCollection", "features": features}


def _build_coverage_polygon(
    coords: List[tuple],
    buffer_m: float,
    max_area_km2: float,
) -> Optional[dict]:
    """shapely 版：凸包 + buffer → GeoJSON geometry dict。

    buffer 需要 UTM/度量投影，这里用近似方法：
    在经纬度空间直接 buffer 一个"米→度"换算后的半径。
    """
    # 中心纬度（用于 1° 经度 ≈ 111.32 * cos(lat) km 换算）
    center_lat = sum(c[1] for c in coords) / len(coords)
    lat_rad = math.radians(center_lat)
    m_per_deg_lon = 111_320.0 * math.cos(lat_rad)
    m_per_deg_lat = 110_574.0

    r_deg_lon = buffer_m / m_per_deg_lon
    r_deg_lat = buffer_m / m_per_deg_lat

    # 用圆近似 buffer（避免 UTM 投影）
    pts = [Point(c) for c in coords]
    if len(pts) == 1:
        p = pts[0]
        geom = p.buffer(r_deg_lon)  # 近似圆
    elif len(pts) == 2:
        from shapely.geometry import LineString
        line = LineString(pts)
        geom = line.buffer(r_deg_lon)
    else:
        hull = MultiPoint(pts).convex_hull
        # 用椭圆近似：经度方向 buffer r_deg_lon，纬度方向 buffer r_deg_lat
        # shapely 的 buffer 是各向同性的，所以取平均
        r_avg = (r_deg_lon + r_deg_lat) / 2.0
        geom = hull.buffer(r_avg, quadsegs=8)

    # 限制最大面积：如果太大，按比例缩小 buffer
    if geom and not geom.is_empty:
        area_deg2 = geom.area  # 单位：度²
        # 换算 km²：1°² ≈ 110.574 * 111.32 * cos(lat) km²（粗略）
        area_km2_approx = area_deg2 * (110.574 * 111.32 * math.cos(lat_rad))
        if area_km2_approx > max_area_km2:
            # 缩小 buffer
            scale = math.sqrt(max_area_km2 / area_km2_approx)
            new_r = r_avg * scale
            geom2 = MultiPoint(pts).convex_hull.buffer(new_r, quadsegs=8)
            if geom2 and not geom2.is_empty:
                geom = geom2

    if geom is None or geom.is_empty:
        return None

    # 转 GeoJSON（shapely → dict）
    import json as _json
    gi = geom.__geo_interface__
    if isinstance(gi, dict):
        return gi
    return _json.loads(gi)


def _build_bbox_fallback(
    coords: List[tuple],
    buffer_m: float,
) -> Optional[dict]:
    """无 shapely 时的简化：包围盒外扩。"""
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    center_lat = sum(lats) / len(lats)
    lat_rad = math.radians(center_lat)
    m_per_deg_lon = 111_320.0 * math.cos(lat_rad)
    m_per_deg_lat = 110_574.0

    pad_lon = buffer_m / m_per_deg_lon
    pad_lat = buffer_m / m_per_deg_lat

    min_lon, max_lon = min(lons) - pad_lon, max(lons) + pad_lon
    min_lat, max_lat = min(lats) - pad_lat, max(lats) + pad_lat

    # 简单矩形（GeoJSON 要求逆时针）
    ring = [
        [round(min_lon, 8), round(min_lat, 8)],
        [round(max_lon, 8), round(min_lat, 8)],
        [round(max_lon, 8), round(max_lat, 8)],
        [round(min_lon, 8), round(max_lat, 8)],
        [round(min_lon, 8), round(min_lat, 8)],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _polygon_area_km2(geom: dict) -> float:
    """GeoJSON polygon → 近似面积（km²）。"""
    if not geom or geom.get("type") != "Polygon":
        return 0.0
    ring = geom["coordinates"][0] if geom["coordinates"] else []
    if len(ring) < 3:
        return 0.0

    # 中心纬度
    c_lat = sum(p[1] for p in ring) / len(ring)
    lat_rad = math.radians(c_lat)
    m_per_deg_lon = 111_320.0 * math.cos(lat_rad)
    m_per_deg_lat = 110_574.0

    # Shoelace 公式（度空间）
    area_deg2 = 0.0
    n = len(ring) - 1  # 闭合点去掉
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        area_deg2 += x1 * y2 - x2 * y1
    area_deg2 = abs(area_deg2) / 2.0

    # 度² → 米²
    area_m2 = area_deg2 * m_per_deg_lon * m_per_deg_lat
    return area_m2 / 1_000_000.0


def _polygon_centroid(geom: dict, coords: List[tuple]) -> tuple:
    """多边形近似中心（取所有坐标的几何中心）。"""
    if not coords:
        return (0, 0)
    c_lon = sum(c[0] for c in coords) / len(coords)
    c_lat = sum(c[1] for c in coords) / len(coords)
    return c_lon, c_lat


def coverage_to_js(coverage: Dict) -> str:
    """把 coverage dict 序列化为 JS 端可直接执行的代码字符串。

    返回形如：
      if (window.PowerAPI) PowerAPI.setCoverage(<json>);
    """
    js_data = json.dumps(coverage, ensure_ascii=False)
    return f"if (window.PowerAPI) {{ PowerAPI.setCoverage({js_data}); }}"


def coverage_summary(coverage: Dict) -> Dict[str, int]:
    """简要统计。"""
    features = coverage.get("features", [])
    total_dev = sum(f["properties"]["device_count"] for f in features)
    return {
        "zones": len(features),
        "devices_covered": total_dev,
        "max_area_km2": max((f["properties"]["area_km2"] for f in features), default=0),
        "min_area_km2": min((f["properties"]["area_km2"] for f in features), default=0),
    }


if __name__ == "__main__":
    import sys
    from excel_loader import load_dir
    from app_paths import find_data_root, DATA_ROOT, COVERAGE_SAMPLE
    root = sys.argv[1] if len(sys.argv) > 1 else str(find_data_root() or DATA_ROOT)
    devs = load_dir(root)
    print(f"loaded {len(devs)} devices")
    cov = compute_coverage(devs, buffer_m=80)
    s = coverage_summary(cov)
    print(f"coverage: {s['zones']} zones, {s['devices_covered']} devices, "
          f"max_area={s['max_area_km2']:.4f} km², min_area={s['min_area_km2']:.4f} km²")
    # 写一个示例到 cache/coverage_sample.json
    out = COVERAGE_SAMPLE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"sample written to {out}")
