#!/usr/bin/env python3
"""EIAS 版本号自动同步（每次修改/深化后运行）

用法：
    python tools/bump_version.py            # +1 修订号（1.2.0 -> 1.2.1）
    python tools/bump_version.py 1.3.0      # 指定版本
    python tools/bump_version.py minor      # +1 minor（1.2.0 -> 1.3.0）
    python tools/bump_version.py major     # +1 major（1.2.0 -> 2.0.0）

同步：VERSION / main.py / version_info.py / packaging.spec / build.gradle / web
"""
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def read_ver():
    for ln in (ROOT / "VERSION").read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and re.match(r"^\d+\.\d+\.\d+$", ln):
            return ln
    raise SystemExit("VERSION 未找到版本号")

def bump(cur, level):
    a, b, c = map(int, cur.split("."))
    if level == "major":
        return f"{a+1}.0.0"
    if level == "minor":
        return f"{a}.{b+1}.0"
    return f"{a}.{b}.{c+1}"

def main():
    cur = read_ver()
    arg = sys.argv[1] if len(sys.argv) > 1 else "patch"
    new = arg if re.match(r"^\d+\.\d+\.\d+$", arg) else bump(cur, arg)
    print(f"[bump] {cur} -> {new}")

    def replace_in(path, old, new, expect=1):
        p = ROOT / path
        s = p.read_text(encoding="utf-8")
        n = s.count(old)
        if n == 0:
            print(f"  [skip] {path}: '{old}' 未命中")
            return
        s = s.replace(old, new)
        p.write_text(s, encoding="utf-8")
        print(f"  [ok] {path}: '{old}' -> '{new}' (x{n})")

    replace_in("VERSION", cur, new)
    replace_in("main.py", f"v{cur}", f"v{new}")
    # version_info.py 元数据
    a, b, c = map(int, cur.split(".")); na, nb, nc = map(int, new.split("."))
    replace_in("version_info.py", f"'{cur}.0'", f"'{new}.0'")   # FileVersion
    replace_in("version_info.py", f"'{cur}.0'", f"'{new}.0'")   # ProductVersion（已改过则跳过）
    replace_in("version_info.py", f"filevers=({a}, {b}, {c}, 0)", f"filevers=({na}, {nb}, {nc}, 0)")
    replace_in("version_info.py", f"prodvers=({a}, {b}, {c}, 0)", f"prodvers=({na}, {nb}, {nc}, 0)")
    # build.gradle
    replace_in("apk/app/build.gradle", f'versionName "{cur}"', f'versionName "{new}"')
    # versionCode +1
    p = ROOT / "apk/app/build.gradle"
    s = p.read_text(encoding="utf-8")
    m = re.search(r"versionCode\s+(\d+)", s)
    if m:
        vc = int(m.group(1)) + 1
        p.write_text(s.replace(f"versionCode {m.group(1)}", f"versionCode {vc}"), encoding="utf-8")
        print(f"  [ok] build.gradle versionCode {m.group(1)} -> {vc}")

    print(f"[done] EIAS {new} 已同步到所有文件")

if __name__ == "__main__":
    main()
