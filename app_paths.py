"""统一路径解析（零硬编码盘符）。

所有路径都从本文件位置反推，程序目录被移动到任何盘符都能正常工作：

    APP_ROOT        = <本文件所在目录>            PowerCollector/ 程序根
    WORKSPACE_ROOT  = APP_ROOT.parent             工作区根（PowerCollector 的上级）
    CACHE_DIR       = APP_ROOT / "cache"          缓存根
    TILE_CACHE_DIR  = CACHE_DIR / "tiles"         瓦片缓存
    WEB_DIR         = APP_ROOT / "web"             Cesium 前端
    ASSETS_DIR      = APP_ROOT / "assets"          SVG 图标
    ICONS_DIR       = APP_ROOT / "icons"           兼容旧目录
    DATA_ROOT       = WORKSPACE_ROOT / "_peek"     默认数据目录（存在则用）

数据目录查找顺序（find_data_root）：
  1) 环境变量 POWERCOLLECTOR_DATA 指定的目录
  2) DATA_ROOT（工作区下的 _peek）
  3) WORKSPACE_ROOT 下含 *.xls / *.xlsx 的第一层子目录
  4) 都不存在 → 返回 None（GUI 提示用户手动选择）

设计约束：源码中不出现任何绝对盘符字面量（路径一律从本文件位置推导）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# === PyInstaller 单文件模式检测 ===
# onefile 下：__file__ 指向临时解压目录 _MEIPASS（只读、每次变）。
#   - 资源（web/icons）从 _MEIPASS 读（打进 exe 的 datas）
#   - 可写目录（cache）必须写到 exe 真实所在处（持久）
# 非打包（开发）下：两者都是 APP_ROOT，行为不变。
_IS_FROZEN = getattr(sys, 'frozen', False)          # PyInstaller 运行中
_MEIPASS = getattr(sys, '_MEIPASS', None)           # onefile 解压目录
_EXE_DIR = Path(sys.executable).parent if _IS_FROZEN else None

# === 资源根：打包=临时解压目录，开发=本文件目录 ===
RES_ROOT = Path(_MEIPASS) if _MEIPASS else Path(__file__).resolve().parent

# === 可写根：打包=exe 所在目录（持久），开发=本文件目录 ===
APP_ROOT = _EXE_DIR if _EXE_DIR else RES_ROOT

# === 工作区根 ===
# 开发模式：APP_ROOT.parent（工作区根，能扫到 _peek / 同级数据目录）
# 单文件 exe：exe 所在目录（投放到任意位置时，数据放 exe 同层可被识别）
if _IS_FROZEN:
    WORKSPACE_ROOT = APP_ROOT
else:
    WORKSPACE_ROOT = APP_ROOT.parent

# === 子目录 ===
# 资源（只读，从临时解压目录 _MEIPASS 或开发目录读）
WEB_DIR    = RES_ROOT / "web"
ASSETS_DIR = RES_ROOT / "assets"
ICONS_DIR  = RES_ROOT / "icons"
# 可写（持久，写到 exe 真实所在目录 / 开发目录）
CACHE_DIR    = APP_ROOT / "cache"
TILE_CACHE_DIR = CACHE_DIR / "tiles"
# 数据目录默认位置（开发下；单文件 exe 运行时 find_data_root 会扫 exe 同层）
DATA_ROOT    = WORKSPACE_ROOT / "_peek"

# === 运行时产物 ===
OVERRIDES_FILE = CACHE_DIR / "tree_overrides.json"
COVERAGE_SAMPLE = CACHE_DIR / "coverage_sample.json"
PROXY_INFO_FILE = WEB_DIR / "proxy_info.js"


def ensure_dirs() -> None:
    """确保运行时目录存在。"""
    for d in (CACHE_DIR, TILE_CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _has_data_files(root: Path) -> bool:
    """目录内（含一层子目录）是否含 xls/xlsx 数据表。"""
    if not root.is_dir():
        return False
    try:
        for p in root.iterdir():
            if p.is_file() and p.suffix.lower() in (".xls", ".xlsx"):
                return True
        for p in root.iterdir():
            if p.is_dir():
                for q in p.rglob("*"):
                    if q.is_file() and q.suffix.lower() in (".xls", ".xlsx"):
                        return True
    except OSError:
        return False
    return False


def find_data_root() -> Optional[Path]:
    """自动识别数据目录。找不到返回 None。

    优先级见模块文档。
    """
    env = os.environ.get("POWERCOLLECTOR_DATA")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p

    if _has_data_files(DATA_ROOT):
        return DATA_ROOT

    # 工作区下第一个含数据表的一层子目录
    try:
        for p in sorted(WORKSPACE_ROOT.iterdir()):
            if p.is_dir() and p.name != APP_ROOT.name and _has_data_files(p):
                return p
    except OSError:
        pass
    return None


def rel_to_workspace(path) -> str:
    """把绝对路径转成相对工作区根的显示串（便于日志/状态栏）。"""
    try:
        return str(Path(path).resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


if __name__ == "__main__":
    print(f"APP_ROOT        = {APP_ROOT}")
    print(f"WORKSPACE_ROOT  = {WORKSPACE_ROOT}")
    print(f"WEB_DIR         = {WEB_DIR}")
    print(f"CACHE_DIR       = {CACHE_DIR}")
    print(f"TILE_CACHE_DIR  = {TILE_CACHE_DIR}")
    print(f"DATA_ROOT       = {DATA_ROOT}  (存在={DATA_ROOT.is_dir()})")
    dr = find_data_root()
    print(f"find_data_root  = {dr}")
    print(f"rel_to_workspace(CACHE_DIR) = {rel_to_workspace(CACHE_DIR)}")
