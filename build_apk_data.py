#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 APK 生成内置数据 bundle（data/devices_bundle.json）。

直接从 Excel 解析（不依赖已删除的 data/devices.json），生成：
  devices: [{id, name, line, lon, lat, type, district, src, props, color?}, ...]
           —— 同坐标不同台区的杆塔带 color 字段（前端按色着色）
  links:   [[parent_id, child_id, level], ...]
  meta:    {device_count, link_count, colored_count, tx_count, source}

用法：
    python build_apk_data.py <Excel目录> [--out data/devices_bundle.json]

不传目录则用 app_paths.find_data_root() 自动识别。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import app_paths            # noqa: E402
from excel_loader import Device, load_dir  # noqa: E402
from link_builder import build_links  # noqa: E402
from pole_colors import assign_pole_colors  # noqa: E402
try:
    from coverage import build_coverage_data
    _HAS_COV = True
except Exception:
    _HAS_COV = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('data_dir', nargs='?', help='Excel 数据目录')
    ap.add_argument('--out', default=None, help='输出 bundle 路径')
    args = ap.parse_args()

    data_dir = args.data_dir or str(app_paths.find_data_root())
    print(f'[bundle] 数据源：{data_dir}')

    # 解析设备（递归扫描 + 跨文件去重，台区并入去重 key）
    devices = load_dir(data_dir)
    print(f'[bundle] 解析设备 {len(devices)} 个（数据源 {data_dir}）')

    # 同坐标不同台区着色
    colors = assign_pole_colors(devices)
    colored = [d.id for d in devices if d.id in colors]
    print(f'[bundle] 着色设备 {len(colored)} 个（同坐标异台区冲突簇）')

    # 连线
    links = build_links(devices)
    lv = {}
    for _, _, l in links:
        lv[l] = lv.get(l, 0) + 1
    print(f"[bundle] 连线 {len(links)} 条，层级分布 {lv}")

    # 组装 devices（含 color）
    devices_out = []
    for d in devices:
        rec = {
            'id': d.id, 'name': d.name, 'line': d.line,
            'lon': round(d.lon, 6), 'lat': round(d.lat, 6),
            'type': d.device_type, 'district': d.district,
            'src': d.src_file, 'props': d.props or {},
        }
        if d.id in colors:
            rec['color'] = colors[d.id]
        devices_out.append(rec)

    # 覆盖范围（可选）
    meta = {
        'device_count': len(devices_out),
        'link_count': len(links),
        'colored_count': len(colored),
        'tx_count': sum(1 for d in devices if '柱上变' in d.device_type),
        'source': Path(data_dir).name,
    }

    bundle = {'devices': devices_out, 'links': [list(l) for l in links], 'meta': meta}
    if _HAS_COV:
        try:
            cov = build_coverage_data(devices)
            if cov:
                bundle['coverage'] = cov
                meta['coverage_lines'] = len(cov)
        except Exception as e:
            print(f'[bundle] 覆盖范围生成失败（忽略）：{e}')

    out = Path(args.out) if args.out else ROOT / 'data' / 'devices_bundle.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f"[bundle] 输出 {out}（{out.stat().st_size//1024} KB）")

    # 同时生成 devices.json（前端 /data/devices.json 也用它）
    dj = out.parent / 'devices.json'
    dj.write_text(json.dumps(devices_out, ensure_ascii=False, separators=(',', ':')),
                  encoding='utf-8')
    print(f"[bundle] 输出 {dj}（{dj.stat().st_size//1024} KB）")


if __name__ == '__main__':
    main()
