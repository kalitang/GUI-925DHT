# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PowerCollector (电力低压数据采集软件).

产物：单 exe + 资源目录（web/, icons/, assets/）
依赖：PySide6, xlrd, requests, PyInstaller 6.x
"""
import os
import pathlib

block_cipher = None
SPEC_DIR = pathlib.Path(SPECPATH).resolve()
PROJECT = SPEC_DIR  # 脚本就在 PowerCollector 下

a = Analysis(
    [str(SPEC_DIR / 'main.py')],
    pathex=[str(SPEC_DIR)],
    binaries=[],
    datas=[
        # web/ 前端目录（Cesium.js + index.html + icons/ + assets/...）
        ('web', 'web'),
        # assets/ 图标目录
        ('assets', 'assets'),
        # 户表批量工具：客户档案模板（onefile 会解压到 _MEIPASS，hutool_core 会探测）
        ('低压客户模板.xls', '.'),
    ],
    hiddenimports=[
        'app_paths',
        'excel_loader',
        'link_builder',
        'link_overrides',
        'tree_builder',
        'tile_proxy',
        'coverage',
        'pole_colors',
        'hutool_core',
        'hutool_dialog',
        'win32com',
        'win32com.client',
        'win32com.client.gencache',
        'pywintypes',
        'pythoncom',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngine',
        'PySide6.QtNetwork',
        'openpyxl',
        'requests',
        'urllib3',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'scipy', 'pandas', 'PIL.ImageQt',
        'shapely', 'shapely.geometry', 'shapely.ops',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets', 'PySide6.QtQuick', 'PySide6.QtQml',
        'PySide6.QtCharts',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ============ 版本信息（版权/公司，打进 exe 的 VersionResource）============
# version_info.py 定义了 VSVersionInfo `info`；PyInstaller 接受直接传入
import importlib.util as _ilu
_vinfo_spec = _ilu.spec_from_file_location('version_info', str(SPEC_DIR / 'version_info.py'))
_vinfo_mod = _ilu.module_from_spec(_vinfo_spec)
_vinfo_spec.loader.exec_module(_vinfo_mod)
_version = _vinfo_mod.info

# ============ ③ EXE 单文件（onefile）============
# 资源（web/assets）打进 exe，运行解压到 _MEIPASS 临时目录读取；
# 可写目录（cache）由 app_paths.py 写到 exe 真实所在处（持久）。
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=False,
    name='电力低压数据采集软件',
    debug=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    version=_version,    # 版权/公司信息（晨曦网络工作室）
    console=False,       # GUI 程序不弹黑窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SPEC_DIR / 'icon.ico') if (SPEC_DIR / 'icon.ico').exists() else None,
)

# onefile：资源都打进 exe，不再需要 COLLECT
coll = COLLECT(
    exe,
    [],
    strip=False,
    upx=False,
    upx_exclude=[],
    name='电力低压数据采集软件',
)