"""电力专业低压数据采集软件 - 主程序
- PySide6 主窗口（菜单 + 工具栏 + 数据表 + Cesium 3D 地球）
- 自动检查并安装依赖
- 自动启动本地瓦片代理（断网可用）
"""
from __future__ import annotations
import os
import sys
import json
import csv
import shutil
import subprocess
import importlib
import logging
import zipfile
from pathlib import Path

# === 应用根目录（全部由 app_paths 推导，无硬编码盘符） ===
from app_paths import (
    APP_ROOT, WORKSPACE_ROOT, WEB_DIR, CACHE_DIR, TILE_CACHE_DIR,
    ASSETS_DIR, DATA_ROOT, OVERRIDES_FILE, find_data_root, rel_to_workspace,
)

# === 0. 依赖自检 + 自动安装 ===
REQUIRED = {
    "PySide6": "PySide6>=6.6.0",
    "openpyxl": "openpyxl>=3.1.0",
    "requests": "requests>=2.31.0",
}


import math
import time
import json


def ensure_deps():
    """检查依赖。打包后（PyInstaller frozen）跳过自动 pip 安装——
    打包版本必须把所有依赖打进去（spec hiddenimports）。
    """
    if getattr(sys, 'frozen', False):
        # 打包版本：缺失依赖直接抛错（避免 frozen 进程调 pip 卡死）
        for mod, spec in REQUIRED.items():
            try:
                importlib.import_module(mod)
            except ImportError:
                raise SystemExit(
                    f"[setup] 打包版本缺少依赖 {spec}，请重打 spec 时把 {mod} 加入 hiddenimports")
        return
    missing = []
    for mod, spec in REQUIRED.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(spec)
    if not missing:
        return
    print(f"[setup] 缺少依赖: {missing}，开始自动安装…", flush=True)
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *missing]
    subprocess.check_call(cmd)
    print("[setup] 依赖安装完成", flush=True)


ensure_deps()

# 依赖安装后再 import Qt
from PySide6.QtCore import Qt, QUrl, QTimer, QObject, Signal, Slot, QSize, QThread, QPoint
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QBrush, QFont, QPolygon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QLabel, QFileDialog, QMessageBox, QProgressBar, QStatusBar, QToolBar,
    QMenuBar, QMenu, QListWidget, QListWidgetItem, QLineEdit, QGroupBox, QFormLayout,
    QCheckBox, QComboBox, QTreeWidget, QTreeWidgetItem
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage

from excel_loader import (load_dir, device_summary, Device,
                          ICON_MAP, TYPE_TRANSFORMER, TYPE_ACCESS_POINT,
                          TYPE_POLE, TYPE_METER_BOX, TYPE_OTHER)
from link_builder import build_links, link_summary
from link_overrides import apply_link_overrides, load_overrides
from tree_builder import build_tree, save_override, TYPE_LABELS, METRO_TYPE
from tile_proxy import TileProxyServer
from coverage import compute_coverage, coverage_summary, coverage_to_js
from pole_colors import assign_pole_colors, color_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True,
    stream=sys.stderr,   # 日志走 stderr（避免和 print 混到 stdout 乱序）
)
# 强制 stdout flush
import functools
print = functools.partial(print, flush=True)
log = logging.getLogger("power_main")

# ============== 数据桥（Python ↔ JS）==============
class Bridge(QObject):
    devicesLoaded = Signal(int)        # 加载完成，发射设备数
    fitAllRequested = Signal()
    typeFilterRequested = Signal(str)
    flyToRequested = Signal(float, float)
    cacheStatsUpdated = Signal(str)

    def __init__(self):
        super().__init__()
        self._pending_load = None  # (devices_json, callback)
        self._window = None

    def attach_window(self, window):
        self._window = window

    @Slot(str)
    def receive_devices_loaded(self, count_str):
        # JS 加载完成后回调
        log.info("前端已加载 %s 个设备", count_str)

    @Slot(str)
    def receive_cache_info(self, info):
        self.cacheStatsUpdated.emit(info)

    @Slot(str)
    def receive_type_filter(self, t):
        self.typeFilterRequested.emit(t)

    @Slot(float, float)
    def receive_fly_to(self, lon, lat):
        self.flyToRequested.emit(lon, lat)


# ============== 数据加载 Worker（线程化）==============
class LoadWorker(QThread):
    progress = Signal(int, int, str)        # done, total, msg
    finished_ok = Signal(list)              # [Device, ...]

    def __init__(self, root: str):
        super().__init__()
        self.root = root

    def run(self):
        import time, logging
        log = logging.getLogger("LoadWorker")
        t0 = time.time()
        try:
            log.info("start loading %s", self.root)
            devs = load_dir(self.root,
                            progress_cb=lambda d, t, m: self.progress.emit(d, t, m))
            log.info("load done: %d devices in %.2fs, emitting finished_ok", len(devs), time.time()-t0)
            # 同时把解析报告写到日志，方便排查
            try:
                from excel_loader import excel_last_report
                rep = excel_last_report()
                if rep:
                    log.info("report: scanned=%d matched=%d devices=%d",
                             rep.get("scanned_files",0), rep.get("matched_files",0), rep.get("devices",0))
                    for lf in rep.get("load_failed", [])[:5]:
                        log.warning("load_failed: %s", lf)
            except Exception:
                pass
            self.finished_ok.emit(devs)
            log.info("emit returned (queued)")
        except Exception as e:
            import traceback
            log.error("failed: %s\n%s", e, traceback.format_exc())
            self.finished_ok.emit([])


# ============== 表格填充 Worker（后台线程只做切片，表格操作回主线程）==============
class TableFillWorker(QThread):
    row_ready = Signal(list)   # 一批 [(row_idx, name, type, line, lon, lat), ...]
    all_done = Signal()

    def __init__(self, devs, batch=500):
        super().__init__()
        self._devs = devs
        self._batch = batch

    def run(self):
        n = len(self._devs)
        import time
        for i in range(0, n, self._batch):
            end = min(i + self._batch, n)
            rows = [(r, d.name, d.device_type, d.line,
                     f"{d.lon:.6f}", f"{d.lat:.6f}") for r, d in
                     enumerate(self._devs[i:end], i)]
            self.row_ready.emit(rows)
        self.all_done.emit()


# ============== 主窗口 ==============
class MainWindow(QMainWindow):
    _hutoolResult = Signal(str)   # 户表批量后台线程 → 主线程 toast
    _hutool_done = Signal()       # 户表批量后台线程 → 主线程（解除忙碌状态）

    def __init__(self):
        super().__init__()
        self.setWindowTitle("电力专业低压数据采集软件  v1.1.1")
        self.resize(1400, 850)
        self.setWindowIcon(self._make_app_icon())

        self.bridge = Bridge()
        self.bridge.attach_window(self)

        # 启动瓦片代理（仅本机回环 127.0.0.1，单机桌面工具不对外暴露）
        TILE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.tile_proxy = TileProxyServer(str(TILE_CACHE_DIR), port=8765)
        self.tile_port = self.tile_proxy.start()
        log.info("tile proxy on 127.0.0.1:%s", self.tile_port)
        self._write_proxy_info()

        # 数据状态
        self.devices: list[Device] = []
        self.current_root: str = ""
        self._worker: LoadWorker | None = None

        # 户表批量：先初始化状态（_build_menubar 会往 _hutool_actions 里填 4 个 QAction）
        self._hutool_running = False
        self._hutool_actions = []

        self._build_ui()
        self._build_menubar()
        self._build_toolbar()

        # 户表批量后台线程 → 主线程 toast（Qt 信号跨线程安全）
        self._hutoolResult.connect(self._on_hutool_result)
        self._hutool_done.connect(lambda: self._hutool_set_busy(False))

        # 定时刷新瓦片代理状态（10 秒一次，避免每次全量遍历目录卡 I/O）
        self._stats_timer = QTimer()
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(10000)
        self._last_size_mb = -1   # 用于避免重复写 label

        # 启动后自动加载 _peek（如果存在）— 方便无人工点击也能验证
        self._auto_load()

        # JS Bridge signals
        self.bridge.typeFilterRequested.connect(self._on_type_filter_from_map)
        self.bridge.flyToRequested.connect(self._on_fly_to_from_map)
        self.bridge.cacheStatsUpdated.connect(lambda s: self.stats_label.setText(f"瓦片缓存：{s}"))

        self.statusBar().showMessage("就绪")

    def _auto_load(self):
        """程序启动后自动识别数据目录并加载（无需人工点击）。"""
        root = find_data_root()
        if root is not None:
            n_xls = sum(1 for _ in root.rglob("*.xls"))
            n_xlsx = sum(1 for _ in root.rglob("*.xlsx"))
            log.info("auto-load %s (%d xls, %d xlsx)", root, n_xls, n_xlsx)
            QTimer.singleShot(2000, lambda: self._load_root(str(root)))
        else:
            log.info("未自动识别到数据目录（工作区 %s 下无 xls/xlsx），请用「打开目录」选择",
                     WORKSPACE_ROOT)

    # ---------- UI 构建 ----------
    def _write_proxy_info(self):
        """写 web/proxy_info.js，让前端 Cesium 知道本地代理地址。

        本程序为单机桌面工具：代理只监听 127.0.0.1，前端同源访问。
        同时注入 PC 的网络出口经纬度（IP 定位，桌面端功能）。
        """
        host = "127.0.0.1"
        pc_loc = self._detect_pc_location()
        loc_js = (
            f"window.PC_LOCATION = {{lon: {pc_loc['lon']}, lat: {pc_loc['lat']}, "
            f"ip: '{pc_loc['ip']}', city: '{pc_loc['city']}'}};\n"
        ) if pc_loc else "window.PC_LOCATION = null;\n"
        info_js = (
            f"window.PROXY_BASE = 'http://{host}:{self.tile_port}/proxy/cdn.jsdelivr.net/npm/cesium@1.131.0/Build/Cesium/';\n"
            f"window.TILE_PROXY = 'http://{host}:{self.tile_port}/tiles';\n"
            f"{loc_js}"
        )
        try:
            (WEB_DIR / "proxy_info.js").write_text(info_js, encoding="utf-8")
            log.info("proxy_info.js 写入完成：%s", info_js.strip().replace("\n", " | "))
        except Exception as e:
            log.warning("写 proxy_info.js 失败：%s", e)

    def _detect_pc_location(self) -> dict | None:
        """探测 PC 的网络出口经纬度（IP 定位）。

        优先级：
          1) api.ip.sb/geoip  （IPv6 + 国内出口信息较准）
          2) ipwho.is/        （兜底，IPv4/IPv6 通用）

        返回 {lon, lat, ip, city}；失败返回 None。
        """
        import requests
        sources = [
            ("https://api.ip.sb/geoip/", "longitude", "latitude", "ip", "city"),
            ("https://ipwho.is/", "longitude", "latitude", "ip", "city"),
        ]
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        for url, lon_k, lat_k, ip_k, city_k in sources:
            try:
                r = requests.get(url, timeout=4, headers=headers)
                if r.status_code != 200:
                    continue
                j = r.json()
                lon = j.get(lon_k)
                lat = j.get(lat_k)
                if lon is None or lat is None:
                    continue
                return {
                    "lon": float(lon),
                    "lat": float(lat),
                    "ip": j.get(ip_k, ""),
                    "city": j.get(city_k, ""),
                }
            except Exception as e:
                log.debug("detect pc location %s failed: %s", url, e)
                continue
        return None

    def _make_app_icon(self):
        # 程序图标：闪电 + 柱上变
        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        # 红圆
        p.setBrush(QBrush(QColor("#c0392b")))
        p.setPen(QColor("#7b241c"))
        p.drawEllipse(2, 2, 60, 60)
        # 闪电
        p.setBrush(QBrush(QColor("#fff")))
        p.setPen(Qt.NoPen)
        poly = QPolygon([QPoint(36, 10), QPoint(22, 34), QPoint(30, 34), QPoint(26, 54), QPoint(44, 28), QPoint(34, 28), QPoint(40, 10)])
        p.drawPolygon(poly)
        p.end()
        return QIcon(pix)

    def _build_menubar(self):
        mb = self.menuBar()
        # 文件
        m_file = mb.addMenu("文件(&F)")
        a_open_dir = QAction("打开数据目录(&O)…", self)
        a_open_dir.setShortcut("Ctrl+O")
        a_open_dir.triggered.connect(self.action_open_dir)
        m_file.addAction(a_open_dir)

        a_open_zip = QAction("从 ZIP 导入(&Z)…", self)
        a_open_zip.setShortcut("Ctrl+Z")
        a_open_zip.triggered.connect(self.action_open_zip)
        m_file.addAction(a_open_zip)

        m_file.addSeparator()
        a_clear_cache = QAction("清空瓦片缓存", self)
        a_clear_cache.triggered.connect(self.action_clear_cache)
        m_file.addAction(a_clear_cache)

        m_file.addSeparator()
        a_exit = QAction("退出(&X)", self)
        a_exit.setShortcut("Ctrl+Q")
        a_exit.triggered.connect(self.close)
        m_file.addAction(a_exit)

        # 视图
        m_view = mb.addMenu("视图(&V)")
        a_fit = QAction("聚焦所有设备", self)
        a_fit.setShortcut("Ctrl+F")
        a_fit.triggered.connect(self.action_fit_all)
        m_view.addAction(a_fit)

        # 户表批量（与《户表批量工具》GUI 版对齐）
        m_hu = mb.addMenu("户表批量(&U)")
        a_hu_open = QAction("打开户表批量工具(&O)…", self)
        a_hu_open.setShortcut("Ctrl+U")
        a_hu_open.triggered.connect(self.action_hutool_open)
        m_hu.addAction(a_hu_open)
        m_hu.addSeparator()
        a_hu_gen = QAction("① 批量生成客户档案", self)
        a_hu_gen.triggered.connect(self.action_hutool_generate)
        m_hu.addAction(a_hu_gen)
        a_hu_mod = QAction("② 批量修改安卓表", self)
        a_hu_mod.triggered.connect(self.action_hutool_modify)
        m_hu.addAction(a_hu_mod)
        a_hu_pdf = QAction("③ 提取整理 PDF", self)
        a_hu_pdf.triggered.connect(self.action_hutool_pdf)
        m_hu.addAction(a_hu_pdf)
        a_hu_coll = QAction("④ 准备采集软件数据", self)
        a_hu_coll.triggered.connect(self.action_hutool_collector)
        m_hu.addAction(a_hu_coll)
        self._hutool_actions = [a_hu_gen, a_hu_mod, a_hu_pdf, a_hu_coll]

        # 帮助
        m_help = mb.addMenu("帮助(&H)")
        a_about = QAction("关于", self)
        a_about.triggered.connect(self._show_about)
        m_help.addAction(a_about)

    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)

        tb.addAction("📁 打开目录", self.action_open_dir)
        tb.addAction("📦 导入 ZIP", self.action_open_zip)
        tb.addSeparator()
        tb.addAction("🗺 聚焦全部", self.action_fit_all)
        tb.addSeparator()
        tb.addAction("🧹 清瓦片缓存", self.action_clear_cache)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        # === 左侧：数据面板 ===
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(6, 6, 6, 6)
        splitter.addWidget(left)

        # 摘要
        self.summary_label = QLabel("尚未加载数据。点击「打开目录」或「导入 ZIP」开始。")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("padding:8px; background:#1c2833; color:#f1c40f; border-radius:4px;")
        left_layout.addWidget(self.summary_label)

        # 类型筛选
        type_box = QGroupBox("设备类型筛选")
        tb_lay = QVBoxLayout(type_box)
        self.type_checks = {}
        for t in [TYPE_TRANSFORMER, TYPE_ACCESS_POINT, TYPE_POLE, TYPE_METER_BOX, TYPE_OTHER]:
            cb = QCheckBox(f"{t} (0)")
            cb.setChecked(True)
            cb.stateChanged.connect(lambda _, tp=t: self._on_type_check(tp))
            self.type_checks[t] = cb
            tb_lay.addWidget(cb)
        left_layout.addWidget(type_box)

        # 设备列表（树状折叠：台区→变压器→杆塔→接入点→计量箱→未分配）
        list_box = QGroupBox("设备列表（点击节点飞行 / 右键改挂接）")
        lb_lay = QVBoxLayout(list_box)
        self.search = QLineEdit()
        self.search.setPlaceholderText("按名称/台区搜索…")
        self.search.textChanged.connect(self._filter_tree)
        lb_lay.addWidget(self.search)

        # 行头操作提示
        self.tree_hint = QLabel(
            "左键：飞行到该节点  ·  右键：手动改挂接（地图连线同步更新）  ·  ▶ 展开/折叠\n"
            "每个设备只显示 1 条上游连线（链式到变压器）；◆ = 手动改过挂接")
        self.tree_hint.setStyleSheet("color:#889; font-size:11px; padding:2px 4px;")
        self.tree_hint.setWordWrap(True)
        lb_lay.addWidget(self.tree_hint)

        # 上游连线列（设备 → 父 → … → 变压器 的完整路径，直观看到挂接关系）
        self.col_link = QPushButton("🔗 显示上游连线")
        self.col_link.setCheckable(True)
        self.col_link.setChecked(True)   # 默认开（用户 2026-09-28：要直观看到挂接层级）
        self.col_link.setToolTip("在每个设备节点下方显示其完整上游链路（父设备 → 祖父设备 → … → 变压器）\n"
                                 "手动改挂接后链路实时刷新；勾选/取消可显示/隐藏")
        self.col_link.toggled.connect(self._on_show_upstream_toggled)
        lb_lay.addWidget(self.col_link)
        self._upstream_visible = True

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setHeaderLabels(["设备"])
        self.tree.setIndentation(18)
        self.tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context)
        self.tree.currentItemChanged.connect(self._on_tree_item_changed)
        # 「上游连线」链路子行不可被右键菜单当父节点（walk 时跳过 ⤴ 行）
        lb_lay.addWidget(self.tree, 1)
        left_layout.addWidget(list_box, 1)

        # === 右侧：Cesium ===
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(right)

        # 地图工具栏（叠加在地图上方）
        map_toolbar = QHBoxLayout()
        self.btn_fit = QPushButton("聚焦全部")
        self.btn_fit.clicked.connect(self.action_fit_all)
        self.btn_clear = QPushButton("关闭弹窗")
        self.btn_clear.clicked.connect(lambda: self._run_js("PowerAPI.closeInfoBox()"))
        self.chk_labels = QCheckBox("显示名称")
        self.chk_labels.setChecked(True)   # 默认开（用户 2026-09-26 要求）
        self.chk_labels.toggled.connect(lambda v: self._run_js(f"PowerAPI.setLabelVisible({str(bool(v)).lower()})"))
        # 连线显示开关（黄色高亮 A→B→C/D）— 11000+ 条，关闭可显著降低 GPU 压力
        self.chk_links = QCheckBox("显示连线")
        self.chk_links.setChecked(True)
        self.chk_links.setToolTip("黄色高亮显示层级连线（A变压器 → B杆塔 → C接入点/D计量箱）\n取消勾选可隐藏 11000+ 条 polyline（提升性能）")
        self.chk_links.toggled.connect(self._on_links_toggled)
        # 供电覆盖范围开关（半透明凸包多边形）
        self.chk_coverage = QCheckBox("覆盖范围")
        self.chk_coverage.setChecked(True)
        self.chk_coverage.setToolTip("显示各台区供电覆盖范围（凸包 + 80m 缓冲）\\n半透明彩色多边形，悬停可查看台区名")
        self.chk_coverage.toggled.connect(self._on_coverage_toggled)
        # 设备图标默认显示（流畅性已通过分批推送+lazy 机制保障）
        self.btn_show_devices = QPushButton("📍 设备")
        self.btn_show_devices.setCheckable(True)
        self.btn_show_devices.setChecked(True)
        self.btn_show_devices.setToolTip("设备图标（默认显示）\n取消勾选则隐藏所有设备")
        self.btn_show_devices.toggled.connect(self._on_show_devices_toggled)
        # 按类型筛选（点击按钮 = 仅显示该类型，再点取消；默认全部）
        from PySide6.QtWidgets import QButtonGroup
        self.type_filter_group = QButtonGroup(self)
        self.type_filter_group.setExclusive(False)
        for type_name, color in [
            ("柱上变（变压器）", "#e74c3c"),
            ("接入点（墙支架）", "#e53935"),     # 红色（40cm 正方形框）
            ("杆塔", "#9E9E9E"),                # 灰色（40cm 直径圆）
            ("计量箱", "#1565C0"),               # 蓝色（20×40cm 长方形）
            ("其他", "#e67e22"),
        ]:
            btn = QPushButton(f"{type_name[:4]}")
            btn.setCheckable(True)
            btn.setToolTip(f"仅显示「{type_name}」（多选可叠加）")
            btn.setStyleSheet(f"QPushButton:checked {{ background:{color}; color:white; border:1px solid {color}; }}")
            btn.toggled.connect(lambda checked, tn=type_name: self._on_type_filter_toggled(tn, checked))
            self.type_filter_group.addButton(btn)
            setattr(self, f"_btn_type_{type_name}", btn)
        # 类型统计标签（实时显示总设备数 / 各类型数）
        self.lbl_stats = QLabel("总数 0")
        self.lbl_stats.setStyleSheet("color:#f1c40f; font-size:12px; padding:2px 6px; background:rgba(20,30,48,0.6); border-radius:4px;")
        # 导出 CSV
        self.btn_export = QPushButton("📥 导出")
        self.btn_export.setToolTip("导出当前可见（搜索过滤后）的设备列表为 CSV（GBK 编码，Excel 直接打开）")
        self.btn_export.clicked.connect(self._on_export_csv)
        # 性能档位选择（自动探测显卡 → 默认选对应档）
        self.cmb_perf = QComboBox()
        self.cmb_perf.addItems(["省资源（核显）", "平衡", "全效（独显）"])
        self.cmb_perf.setCurrentIndex(1)   # 占位，网页加载后会被 getPerfInfo 纠正
        self.cmb_perf.setToolTip(
            "自动按显卡选择档位：\n"
            "· 省资源（核显）：关所有特效，分辨率 0.6，老旧电脑流畅\n"
            "· 平衡：保留基础光照，分辨率 0.85\n"
            "· 全效（独显）：开抗锯齿/大气/阴影，分辨率 1.0\n"
            "瓦片实时加载 + 本地自动缓存，离线可用。"
        )
        self.cmb_perf.currentIndexChanged.connect(self._on_perf_changed)
        for b in (self.btn_fit, self.btn_clear):
            map_toolbar.addWidget(b)
        map_toolbar.addWidget(self.btn_show_devices)
        map_toolbar.addWidget(self.chk_labels)
        map_toolbar.addWidget(self.chk_links)
        map_toolbar.addWidget(self.chk_coverage)
        map_toolbar.addWidget(self.btn_export)
        map_toolbar.addWidget(QLabel("  性能:"))
        map_toolbar.addWidget(self.cmb_perf)
        map_toolbar.addStretch()
        right_layout.addLayout(map_toolbar)

        # WebEngine
        class LoggingPage(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, msg, source, line, column=0):
                # PySide6 5 参数签名；旧 4 参数签名会导致该 hook 不被调用
                log.info("[JS %s] %s (%s:%s)", level, msg, source, line)
            def certificateError(self, err):
                log.warning("cert err: %s", err.errorDescription())
                return True   # 忽略自签证书
        self.web = QWebEngineView()
        self.web.setPage(LoggingPage(self.web))
        # 允许本地资源 + 任意跨域
        settings = self.web.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        # === WebGL 必须保留（Cesium 强制依赖 WebGL）；GPU 加速由 Chromium 参数 --use-gl=swiftshader
        # 在无 GPU 设备上自动用软件光栅化（性能低但能跑）。有独显则自动走硬件 GL。
        settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.PluginsEnabled, True)
        # 限制 WebEngine 缓存大小（默认 1GB）— 避免低内存设备被缓存吃光
        try:
            from PySide6.QtWebEngineCore import QWebEngineProfile
            prof = self.web.page().profile()
            prof.setHttpCacheMaximumSize(64 * 1024 * 1024)   # 64MB
            prof.setCachePath(str(CACHE_DIR / "webengine"))
        except Exception as e:
            log.debug("set webengine cache: %s", e)
        # 加载本地 HTML（通过本地 HTTP 服务加载，避免 file:// 跨域限制）
        # URL 带启动时间戳 → 强制每次回源取最新前端（否则改了 index.html 用户还看到旧版）
        url = QUrl(f"http://127.0.0.1:{self.tile_port}/web/index.html?t={int(time.time())}")
        # 网页加载完成后 → 探测显卡并同步性能档位
        self.web.loadFinished.connect(self._sync_perf_from_web)
        self.web.load(url)
        right_layout.addWidget(self.web, 1)

        splitter.setSizes([420, 980])

        # 状态栏
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.stats_label = QLabel("瓦片缓存：加载中…")
        sb.addPermanentWidget(self.stats_label)
        # 显卡 / 性能档位 状态标签
        self.gpu_label = QLabel("显卡：探测中…")
        self.gpu_label.setStyleSheet("padding:0 8px; color:#aaa; font-size:12px;")
        sb.addPermanentWidget(self.gpu_label)

    def _sync_perf_from_web(self, ok):
        """网页加载完成后，从 JS 端取显卡型号 + 自动档位，同步到 UI。"""
        if not ok:
            return
        try:
            def _apply(result):
                if not isinstance(result, dict):
                    return
                gpu = result.get("gpu", "未知")
                cur_level = result.get("perfLevel", 1)
                # 显示显卡型号 + 当前档位
                labels = ["省资源", "平衡", "全效"]
                lv_text = labels[cur_level] if 0 <= cur_level <= 2 else "平衡"
                self.gpu_label.setText(f"显卡：{gpu[:32]}  ·  当前档：{lv_text}")
                # 同步 QComboBox（只有用户没手动改过才纠正到自动档）
                if self.cmb_perf.currentIndex() == 1 and cur_level != 1:
                    self.cmb_perf.blockSignals(True)
                    self.cmb_perf.setCurrentIndex(cur_level)
                    self.cmb_perf.blockSignals(False)
            # PySide6: runJavaScript(js, callback)
            self.web.page().runJavaScript("getPerfInfo()", _apply)
        except Exception as e:
            log.debug("sync perf failed: %s", e)

    # ---------- 动作 ----------
    def action_open_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择数据目录（含 xls/xlsx）",
                                             str(WORKSPACE_ROOT))
        if d:
            self._load_root(d)

    def action_open_zip(self):
        fp, _ = QFileDialog.getOpenFileName(self, "选择 ZIP 数据包", str(WORKSPACE_ROOT),
                                            "ZIP 文件 (*.zip)")
        if not fp:
            return
        # 解压到 cache/unzipped/<zip名>
        target = CACHE_DIR / "unzipped" / Path(fp).stem
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        try:
            with zipfile.ZipFile(fp) as z:
                z.extractall(target)
            self.statusBar().showMessage(f"已解压到 {target}")
            self._load_root(str(target))
        except Exception as e:
            QMessageBox.critical(self, "解压失败", str(e))

    def action_clear_cache(self):
        if QMessageBox.question(self, "确认", "清空所有已缓存的卫星瓦片？") != QMessageBox.Yes:
            return
        n = 0
        for p in TILE_CACHE_DIR.rglob("*"):
            if p.is_file() and not p.name.endswith(".tmp"):
                p.unlink()
                n += 1
        QMessageBox.information(self, "完成", f"已清空 {n} 个瓦片文件")
        self._refresh_stats()

    def action_fit_all(self):
        self._run_js("PowerAPI.fitAll()")

    # ==================== 户表批量工具（合并自 hutool_core）====================
    def _hutool_open_dialog(self):
        """打开户表批量工具对话框（与《户表批量工具》GUI 版功能对齐）。"""
        from hutool_dialog import HutoolDialog
        dlg = getattr(self, "_hutool_dlg", None)
        if dlg is None:
            dlg = HutoolDialog(self)
            self._hutool_dlg = dlg
        # 沿用上次选的根目录
        prev = getattr(self, "_hutool_root", "")
        if prev and not dlg.ent_root.text().strip():
            dlg.ent_root.setText(prev)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def action_hutool_open(self):
        self._hutool_open_dialog()

    def _hutool_busy(self):
        """是否已有户表任务在跑 —— 防止重复点击导致多个 Excel COM 实例冲突。"""
        return getattr(self, "_hutool_running", False)

    def _hutool_set_busy(self, busy: bool):
        self._hutool_running = busy
        for act in getattr(self, "_hutool_actions", []):
            act.setEnabled(not busy)

    def _on_hutool_result(self, msg: str):
        """主线程槽：后台线程通过信号回传结果，在此安全调 _run_js。

        同时弹 Qt 消息框兜底 —— 前端 toast 只在 Cesium 页面加载完成后可见，
        万一页面未就绪，用户仍能通过消息框看到结果。
        """
        from PySide6.QtWidgets import QMessageBox
        safe = msg.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
        try:
            self._run_js(
                "if (window.PowerAPI && typeof window.PowerAPI.toast === 'function') "
                f"{{ window.PowerAPI.toast('{safe}'); }} "
                f"else {{ console.log('[户表] {safe}'); }}")
        except Exception:
            pass
        QMessageBox.information(self, "户表批量工具", msg)

    def _hutool_pick_dir(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "选择待处理的根目录")
        if not d:
            return None
        self._hutool_root = d
        return d

    def _hutool_run(self, label, fn):
        """统一的后台任务启动器：busy 检查 → 后台线程 → 信号回传 → 解除 busy。

        fn() 必须返回 (消息字符串)；异常会被捕获并回传错误信息。
        """
        if self._hutool_busy():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "户表批量工具", "已有任务正在运行，请等待完成。")
            return
        import threading

        def work():
            try:
                msg = fn()
            except Exception as e:
                msg = f"{label}错误：{e}"
            self._hutoolResult.emit(msg)
            self._hutool_done.emit()

        self._hutool_set_busy(True)
        threading.Thread(target=work, daemon=True).start()

    def action_hutool_generate(self):
        root = self._hutool_pick_dir()
        if not root:
            return
        import hutool_core
        from PySide6.QtWidgets import QMessageBox
        r = QMessageBox.question(self, "未匹配记录", "未匹配的表码保留条形码、其余列留空（不勾选则整条跳过）？",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        skip_unmatched = r == QMessageBox.No
        self._hutool_run("户表·生成客户档案", lambda: (
            lambda st: f"户表·生成客户档案：成功{st['ok']} 失败{st['fail']} 共{st['total']}")(
            hutool_core.batch_generate(root, log=lambda m: None, should_stop=lambda: False,
                                       skip_unmatched=skip_unmatched)))

    def action_hutool_modify(self):
        root = self._hutool_pick_dir()
        if not root:
            return
        import hutool_core
        from PySide6.QtWidgets import QMessageBox
        r = QMessageBox.question(self, "保留采集版副本", "同时为《电力信息采集外业》保留「0杆」原样副本？",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        keep_copy = r == QMessageBox.Yes
        self._hutool_run("户表·修改安卓表", lambda: (
            lambda st: f"户表·修改安卓表：成功{st['ok']} 失败{st['fail']} 跳过{st['skipped']} 共{st['total']}")(
            hutool_core.batch_modify_android(root, log=lambda m: None, should_stop=lambda: False,
                                              keep_collector_copy=keep_copy)))

    def action_hutool_pdf(self):
        root = self._hutool_pick_dir()
        if not root:
            return
        import hutool_core
        self._hutool_run("户表·PDF整理", lambda: (
            lambda st: f"户表·PDF整理：移动{st['moved']} 跳过{st['skipped']} 共{st['total']}")(
            hutool_core.collect_pdfs(root, log=lambda m: None)))

    def action_hutool_collector(self):
        root = self._hutool_pick_dir()
        if not root:
            return
        import hutool_core
        self._hutool_run("户表·采集软件数据", lambda: (
            lambda st: f"户表·采集软件数据：台区{st['total']} 复制{st['copied']}份")(
            hutool_core.prepare_collector_data(root, log=lambda m: None)))

    def _show_about(self):
        QMessageBox.about(self, "关于",
            "<h3>电力专业低压数据采集软件 v1.1.1</h3>"
            "<p>3D 地球 + ArcGIS 卫星影像 + 本地瓦片缓存</p>"
            "<p>技术栈：PySide6 + CesiumJS + ArcGIS World Imagery</p>"
            "<p>支持：WGS84 / 缩放至 19 级 / 3D 旋转 / 真实星空 / 太阳跟随时区</p>"
            f"<p>瓦片代理：127.0.0.1:{self.tile_port}（仅本机）</p>"
            f"<p>程序目录：{APP_ROOT}</p>"
            f"<p>缓存目录：{TILE_CACHE_DIR}</p>"
            "<hr/>"
            "<b>版权与出版</b>"
            "<p>© 晨曦网络工作室 · 重庆亦静怡动电子商务有限公司</p>"
            "<p>All rights reserved.</p>"
        )

    # ---------- 数据加载 ----------
    def _load_root(self, root: str):
        self.current_root = root
        self.summary_label.setText("正在扫描目录…")
        self.statusBar().showMessage(f"扫描 {root}")

        if self._worker and self._worker.isRunning():
            return
        self._worker = LoadWorker(root)
        self._worker.progress.connect(
            lambda d, t, m: self.statusBar().showMessage(f"[{d}/{t}] {m}"))
        self._worker.finished_ok.connect(self._on_data_loaded)
        self._worker.start()

    def _on_data_loaded(self, devs: list):
        log.info("_on_data_loaded called with %d devs", len(devs))
        self.devices = devs
        s = device_summary(devs)
        total = len(devs)
        lines = [f"<b>{total}</b> 个设备，来自 {len(set(d.src_file for d in devs))} 个数据文件"]
        for t in [TYPE_TRANSFORMER, TYPE_ACCESS_POINT, TYPE_POLE, TYPE_METER_BOX, TYPE_OTHER]:
            lines.append(f"&nbsp;&nbsp;{t}：<b>{s.get(t, 0)}</b>")
        self.summary_label.setText("<br>".join(lines))
        self._update_type_counts(s)
        self.statusBar().showMessage(f"已加载 {total} 个设备")
        log.info("已加载 %d 个设备", total)

        # 不立即 push 设备到 JS（低内存 / 无 GPU 设备首次启动会卡），
        # 等用户点 "显示设备" 按钮才推送渲染
        # device_js = json.dumps([...])  # 仍保留 self._pending_devices 列表
        # self.web.page().runJavaScript(f"window.__queueDevices({device_js});", ...)

        if devs:
            # 设备数据就绪：存入待渲染队列
            self._pending_devices = devs
            self._all_devices = devs   # 供搜索/导出/统计复用
            self._devices_rendered = False
            # 同坐标不同台区杆塔 → 分配不同颜色（前端按颜色着色图标）
            try:
                pcm = assign_pole_colors(devs)
                self._pole_colors = pcm
                cs = color_summary(devs)
                self._pole_color_clusters = cs["conflict_clusters"]
                log.info("pole colors: %d 设备着色, %d 个冲突簇, 涉及 %d 个台区",
                         len(pcm), cs["conflict_clusters"], cs["lines_involved"])
            except Exception as e:
                log.warning("pole color assign failed: %s", e)
                self._pole_colors = {}
                self._pole_color_clusters = 0
            # 默认勾选了「设备」→ 自动触发分批推送渲染
            # 3 秒后自动飞到数据范围（让用户看到瓦片加载在自己数据的位置）
            d0 = devs[0]
            QTimer.singleShot(3000, lambda: self._run_js(
                f"PowerAPI.flyTo({d0.lon},{d0.lat}, 25000)"))
            # 立即触发设备渲染（等效于用户点击"显示设备"按钮）
            self._on_show_devices_toggled(True)
            self._refresh_stats()   # 工具栏实时统计

            # 计算供电覆盖范围（凸包 + 缓冲），推送到 JS 端渲染
            try:
                cov = compute_coverage(devs, buffer_m=80)
                cov_stats = coverage_summary(cov)
                log.info("coverage: %d zones, %d devices covered, max=%.3f km², min=%.3f km²",
                         cov_stats['zones'], cov_stats['devices_covered'],
                         cov_stats['max_area_km2'], cov_stats['min_area_km2'])
                self._pending_coverage = cov
            except Exception as e:
                log.warning("coverage compute failed: %s", e)
                self._pending_coverage = None

            # 计算连线（A→B→C/D 拓扑）— 自动规则 + 手动 override 覆盖，
            # 缓存到 self._pending_links，推完设备后再发送
            try:
                auto_links = build_links(devs)
                ovr = load_overrides(str(OVERRIDES_FILE))
                if ovr:
                    links, ovr_stats = apply_link_overrides(devs, auto_links, ovr)
                    log.info("override links: applied=%d invalid=%d unassigned=%d total=%d (auto=%d)",
                             ovr_stats['applied'], ovr_stats['invalid'],
                             ovr_stats['unassigned'], ovr_stats['total'], len(auto_links))
                else:
                    links = auto_links
                ls = link_summary(links)
                log.info("computed %d links (A→B: %d, B→C: %d)", ls["total"], ls["AB"], ls["BC"])
                self._pending_links = links
            except Exception as e:
                log.exception("link build failed: %s", e)
                self._pending_links = []
        else:
            self._pending_devices = []
            self._all_devices = []

        # 表格异步填充（后台线程切片，主线程批量 setItem，避免 3013 行卡死 UI）
        self._populate_tree(devs)

    def _update_type_counts(self, summary: dict):
        for t, cb in self.type_checks.items():
            cb.setText(f"{t} ({summary.get(t, 0)})")

    @Slot(list)
    def _table_fill_row(self, rows):
        # 主线程安全填表：每批 500 行
        for r, name, dtype, line, lon, lat in rows:
            self.table.setItem(r, 0, QTableWidgetItem(name))
            self.table.setItem(r, 1, QTableWidgetItem(dtype))
            self.table.setItem(r, 2, QTableWidgetItem(line))
            self.table.setItem(r, 3, QTableWidgetItem(lon))
            self.table.setItem(r, 4, QTableWidgetItem(lat))

    @Slot()
    def _table_fill_done(self):
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        log.info("table fill done: %d rows", self.table.rowCount())

    def _populate_table(self, devs):
        """按「台区→变压器→杆塔→接入点→计量箱」层级填充表格。

        每行 = 一个台区，列里列出该台区下各类型的设备名称（逗号分隔，最多 6 个，超出省略）。
        同时建一个内部索引 self._line_to_devs 供飞行使用。
        """
        # 按台区分组
        from collections import OrderedDict
        lines = OrderedDict()   # line_no -> {device_type -> [names]}
        for d in devs:
            line_key = d.line or d.district or "未知"
            bucket = lines.setdefault(line_key, {"__coords": [], "设备": []})
            bucket["设备"].append(d)

        self._line_to_devs = {k: v["设备"] for k, v in lines.items()}

        self.table.setRowCount(len(lines))
        self.table.setUpdatesEnabled(False)
        BATCH = 200
        for start in range(0, len(lines), BATCH):
            end = min(start + BATCH, len(lines))
            for r, (line_no, bucket) in enumerate(list(lines.items())[start:end], start):
                devs_in = bucket["设备"]
                # 各类型名称收集
                by_type = {"变压器": [], "杆塔": [], "接入点（墙支架）": [], "计量箱": []}
                for d in devs_in:
                    t = d.device_type
                    if t == TYPE_TRANSFORMER:
                        by_type["变压器"].append(d.name)
                    elif t == TYPE_POLE:
                        by_type["杆塔"].append(d.name)
                    elif t == TYPE_ACCESS_POINT:
                        by_type["接入点（墙支架）"].append(d.name)
                    else:
                        by_type["计量箱"].append(d.name)

                # 台区行
                it_line = QTableWidgetItem(line_no)
                it_line.setData(Qt.UserRole, {"kind": "line", "line": line_no})
                self.table.setItem(r, 0, it_line)

                # 各设备列
                def mk(kind, names, limit=6):
                    text = "、".join(names[:limit]) + (f" 等{len(names)}个" if len(names) > limit else "")
                    it = QTableWidgetItem(text)
                    it.setData(Qt.UserRole, {"kind": kind, "line": line_no, "names": names})
                    return it
                self.table.setItem(r, 1, mk("变压器", by_type["变压器"]))
                self.table.setItem(r, 2, mk("杆塔", by_type["杆塔"]))
                self.table.setItem(r, 3, mk("接入点（墙支架）", by_type["接入点（墙支架）"]))
                self.table.setItem(r, 4, mk("计量箱", by_type["计量箱"]))

                # 经纬度列：取该台区中心
                if devs_in:
                    c_lon = sum(d.lon for d in devs_in) / len(devs_in)
                    c_lat = sum(d.lat for d in devs_in) / len(devs_in)
                    self.table.setItem(r, 5, QTableWidgetItem(f"{c_lon:.5f}, {c_lat:.5f}"))
            QApplication.processEvents()
        self.table.setUpdatesEnabled(True)
        log.info("table fill done: %d rows", self.table.rowCount())

    def _filter_table(self, txt):
        txt = txt.strip().lower()
        for r in range(self.table.rowCount()):
            visible = (not txt) or any(
                txt in (self.table.item(r, c).text().lower() if self.table.item(r, c) else "")
                for c in range(6)
            )
            self.table.setRowHidden(r, not visible)

    # ---------- 设备列表（树状折叠） ----------
    def _populate_tree(self, devs: list):
        """按「台区→变压器→杆塔→接入点→计量箱」层级填充树状列表。

        手动改挂接：右键节点 → 选择父节点 → 持久化到 cache/tree_overrides.json。
        勾选「显示上游连线」后每个设备节点下方插入灰色链路行。
        """
        from tree_builder import build_tree, save_override
        ovr_path = OVERRIDES_FILE
        trees = build_tree(devs, override_path=str(ovr_path))
        self._last_trees = trees
        self._build_name_index()

        # 设备索引：(line, type, name) → Device（用于飞行的坐标）
        self._dev_index = {}
        for d in devs:
            self._dev_index[(d.line, d.device_type, d.name)] = d
        # 台区中心坐标（用于台区根节点飞行）
        self._line_centers = {}
        for d in devs:
            self._line_centers.setdefault(d.line or d.district or "未知", []).append((d.lon, d.lat))

        self.tree.clear()
        self.tree.setUpdatesEnabled(False)
        self._tree_node_data = {}   # QTreeWidgetItem → node dict
        for t in trees:
            self.tree.addTopLevelItem(self._make_tree_item(t, ovr_path))
        self.tree.setUpdatesEnabled(True)
        # 默认折叠所有节点（避免 1048 个 item 一次渲染卡死 UI）；
        # 用户点 ▶ 自己展开。台区一级保持展开以便看到台区名。
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setExpanded(True)   # 台区名一级展开
            for j in range(top.childCount()):
                top.child(j).setExpanded(False)   # 子节点（变压器/未分配桶）折叠
        # 勾选「显示上游连线」时注入灰色链路子行
        for i in range(self.tree.topLevelItemCount()):
            self._inject_upstream_items(self.tree.topLevelItem(i))
        log.info("tree fill done: %d 台区", self.tree.topLevelItemCount())

    def _make_tree_item(self, node: dict, ovr_path, line_no: str = "") -> QTreeWidgetItem:
        lvl = node["level"]
        name = node["name"]
        # 注入台区名（台区根自己的 name 即台区；子节点继承父台区）
        if lvl == 0:
            node["_line"] = name
        else:
            node["_line"] = line_no
        # 显示文字：层级图标 + 名称 + （未连接标记）
        icons = {0: "📁", 1: "⚡", 2: "🏁", 3: "📍", 4: "📦", 9: "⚠️"}
        prefix = icons.get(lvl, "")
        mark = "◆" if node.get("override_parent") else ""
        conn = "" if node.get("connected") else "（未连接）"
        item = QTreeWidgetItem([f"{prefix} {mark}{name}{conn}"])
        item.setText(0, f"{prefix} {mark}{name}{conn}")
        # 存节点数据
        item.setData(0, Qt.UserRole, node)
        self._tree_node_data[id(item)] = node
        # 子节点
        for c in node["children"]:
            item.addChild(self._make_tree_item(c, ovr_path, line_no or name))
        # 样式
        if lvl == 0:
            item.setFont(0, QFont("Microsoft YaHei", 11, QFont.Bold))
            item.setForeground(0, QColor("#f1c40f"))
        elif lvl == 9:
            item.setForeground(0, QColor("#aaa"))
        elif not node.get("connected"):
            item.setForeground(0, QColor("#e74c3c"))   # 未连接设备红色
        elif mark:
            item.setForeground(0, QColor("#2ecc71"))     # 手动挂接绿色
        return item

    def _filter_tree(self, txt: str):
        """搜索框：按节点名 / 台区 过滤。"""
        txt = txt.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            self._filter_tree_item(top, txt)
            top.setHidden(not self._tree_item_visible(top))

    def _filter_tree_item(self, item, txt):
        """递归：节点自身匹配则显示并展开；否则看子节点。

        匹配范围（txt 非空时）：
        - 节点名（text(0)）
        - 节点对应 device 的 deviceProps 所有字段值（如变压器编号、电压等级、设备型号等）
        """
        visible = False
        own = item.text(0).lower()
        if not txt or txt in own:
            visible = True
        # 节点 → device 索引（用于匹配 deviceProps）
        if txt and not visible:
            node = item.data(0, Qt.UserRole)
            if node:
                key = self._dev_key_of_node(node)
                if key:
                    d = self._dev_index.get(key)
                    if d and d.props:
                        # 把 props 所有字符串值串起来一次性搜
                        props_blob = " ".join(str(v) for v in d.props.values()).lower()
                        if txt in props_blob:
                            visible = True
        for i in range(item.childCount()):
            child = item.child(i)
            if child.text(0).startswith('⤴'):
                continue   # 上游链路行不参与搜索/飞行
            self._filter_tree_item(child, txt)
            if not child.isHidden():
                visible = True
        if txt and visible:
            item.setExpanded(True)
        item.setData(0, Qt.UserRole + 1, visible)   # 自身是否应显示
        # 仅当自身匹配时隐藏/显示（子节点独立控制）
        if txt:
            item.setHidden(not visible)

    def _tree_item_visible(self, item):
        return bool(item.data(0, Qt.UserRole + 1)) or not item.isHidden()

    def _on_tree_item_changed(self, cur, prev):
        """选中节点 → 飞行到该节点坐标（台区/变压器 → 覆盖范围；设备 → 该点）。"""
        if cur is None:
            return
        node = cur.data(0, Qt.UserRole)
        if not node:
            return
        name = node["name"]
        level = node["level"]
        # 台区根：飞覆盖范围
        if level == 0:
            self._fly_to_line_bounds(node)
            return
        # 未分配桶：无坐标，不飞
        if level == 9:
            return
        # 设备节点：按 (line, type, name) 查坐标
        key = self._dev_key_of_node(node)
        d = self._dev_index.get(key)
        if d:
            self._run_js(f"PowerAPI.flyTo({d.lon},{d.lat}, 2000)")

    def _dev_key_of_node(self, node: dict):
        """从树节点反查 Device 索引 key（(line, type, name)）。"""
        lvl = node["level"]
        line = node.get("_line", "")
        if lvl == 1:
            return (line, TYPE_TRANSFORMER, node["name"])
        if lvl == 2:
            return (line, TYPE_POLE, node["name"])
        if lvl == 3:
            return (line, TYPE_ACCESS_POINT, node["name"])
        if lvl == 4:
            return (line, METRO_TYPE, node["name"])
        return None

    def _find_line_for(self, node):
        # 向上找台区根
        return node.get("_line", "")

    def _fly_to_line_bounds(self, node: dict):
        """台区根节点 → 飞完整覆盖范围（所有设备包围盒）。"""
        line_no = node["name"]
        # 优先用 JS 端 flyToLine（按台区名过滤 deviceData，最准确）
        self._run_js(f"PowerAPI.flyToLine({line_no!r})")

    # ---------- 上游连线显示（设备列表中直观看到挂接层级） ----------
    def _build_name_index(self):
        """台区树 → 名称索引（上溯 parent 用；台区内名称唯一）。"""
        self._name_index = {}
        for t in getattr(self, '_last_trees', []):
            def walk(n, line_no):
                key = (n.get('name'), line_no)
                if key not in self._name_index:
                    self._name_index[key] = n
                for c in n.get('children', []):
                    walk(c, line_no)
            walk(t, t.get('name', ''))

    def _upstream_chain(self, node: dict) -> list:
        """node → [父, 祖父, …, 变压器]（沿 parent 字段上溯，防环）。"""
        chain = []
        seen = set()
        line_no = node.get('_line', '')
        cur = node.get('parent')
        while cur and cur not in ('未分配',) and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            nxt = self._name_index.get((cur, line_no))
            if nxt is None:
                break
            cur = nxt.get('parent')
        return chain

    def _on_show_upstream_toggled(self, on: bool):
        """勾选「显示上游连线」：在每个设备节点下插入灰色链路行。"""
        self._upstream_visible = bool(on)
        devs = getattr(self, 'devices', [])
        if devs:
            self._populate_tree(devs)   # 内部会注入链路行（若已勾选）

    def _inject_upstream_items(self, top_item):
        """递归遍历树，给每个设备节点（level 1-4）注入「上游：X → Y → …」子行。"""
        if not getattr(self, '_upstream_visible', False):
            return
        def walk(item):
            node = item.data(0, Qt.UserRole)
            if node and node.get('level') in (1, 2, 3, 4):
                chain = self._upstream_chain(node)
                # 移除旧的链路子行（文本以「⤴」开头）
                for j in range(item.childCount() - 1, -1, -1):
                    ch = item.child(j)
                    if ch.text(0).startswith('⤴'):
                        item.removeChild(ch)
                if chain:
                    txt = ' → '.join(chain)
                    ui = QTreeWidgetItem([f'⤴ 上游连线：{txt}'])
                    ui.setForeground(0, QColor('#8899aa'))
                    ui.setFont(0, QFont('Microsoft YaHei', 9))
                    ui.setFlags(Qt.ItemIsEnabled)   # 不可选中/不可右键
                    item.addChild(ui)
            for i in range(item.childCount()):
                ch = item.child(i)
                if not ch.text(0).startswith('⤴'):
                    walk(ch)
        walk(top_item)

    # ---------- 右键手动改挂接 ----------
    def _on_tree_context(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        node = item.data(0, Qt.UserRole)
        if not node:
            return
        lvl = node["level"]
        # 台区根 / 未分配桶：不能改挂接
        if lvl in (0, 9):
            return
        # 收集该台区下所有节点作为可选父节点（排除当前设备自身的子树，防挂到下游）
        line_root = self._line_root_of(item)
        candidates = self._collect_node_candidates(line_root, exclude_subtree=item)
        # 过滤：不能选自己或自己的子节点
        my_key = self._dev_key_of_node(node)
        cur_parent = node.get("override_parent") or node.get("auto_parent")
        menu = QMenu(self)
        menu.setTitle(f"手动改挂接「{node['name']}」")
        for c in candidates:
            if c["name"] == node["name"]:
                continue
            act = menu.addAction(c["label"] or c["name"])
            act.setData(c)
        menu.addSeparator()
        act_clear = menu.addAction("❌ 取消手动挂接（恢复自动）")
        act_clear.setData(None)
        chosen = menu.exec(self.tree.mapToGlobal(pos))
        if chosen is None:
            return
        new_parent = chosen.data()
        ovr_path = OVERRIDES_FILE
        key = self._dev_key_of_node(node)
        if key:
            if new_parent is None:
                # 取消：删掉 override
                save_override(str(ovr_path), key, None, lvl)
            else:
                save_override(str(ovr_path), key, new_parent["name"], new_parent["level"])
        log.info("override %s → %s", key, new_parent["name"] if new_parent else None)
        # 重新构建树 + 同步重算地图连线（手动改挂接 → 地图连线即时更新）
        if hasattr(self, "devices") and self.devices:
            self._populate_tree(self.devices)
            self._recompute_and_push_links()

    def _line_root_of(self, item):
        """向上找到台区根（一级）。"""
        while item.parent() is not None:
            item = item.parent()
        return item

    def _collect_node_candidates(self, line_root, exclude_subtree=None) -> list:
        """收集台区下所有可作为父节点的树节点（排除 exclude_subtree 子树内的节点）。"""
        out = []
        # 先收集被排除子树的所有节点名称（不能挂到自己的下游）
        excluded = set()
        def collect_excl(n):
            d = n.data(0, Qt.UserRole)
            if d:
                excluded.add(d.get('name', ''))
            for i in range(n.childCount()):
                collect_excl(n.child(i))
        if exclude_subtree:
            collect_excl(exclude_subtree)
        def walk(n):
            d = n.data(0, Qt.UserRole)
            if d and d["level"] in (1, 2, 3, 4) and d.get('name') not in excluded:
                out.append(d)
            for i in range(n.childCount()):
                walk(n.child(i))
        if line_root:
            walk(line_root)
        return out

    # ---------- 旧表格方法（已替换为树，保留兼容占位） ----------
    def _filter_table(self, txt):
        self._filter_tree(txt)

    def _on_table_click(self, item):
        pass

    def _on_table_dblclick(self, idx):
        pass

    def _populate_table(self, devs):
        self._populate_tree(devs)

    def _on_type_check(self, t):
        # 至少留一个勾上
        checked = [k for k, cb in self.type_checks.items() if cb.isChecked()]
        if not checked:
            self.sender().setChecked(True)
            return
        js_checked = ",".join(json.dumps(t) for t in checked)
        # 让 JS 端按当前勾选状态显示/隐藏
        self._run_js(f"""
            (function(){{
                const arr = new Set([{js_checked}]);
                const vals = (typeof viewer.entities.values === 'function') ? viewer.entities.values() : Array.from(viewer.entities.values);
                const ents = vals.filter(e => e.properties && e.properties.devType);
                for (const e of ents) {{
                    const tp = (typeof e.properties.devType.getValue === 'function') ? e.properties.devType.getValue() : e.properties.devType;
                    e.show = arr.size === 6 || arr.has(tp);
                }}
                window.__deviceEntities = ents;
            }})()
        """)

    def _on_type_filter_from_map(self, t):
        log.info("filter from map: %s", t)
        for k, cb in self.type_checks.items():
            cb.setChecked(k == t)

    def _on_fly_to_from_map(self, lon, lat):
        pass

    # ---------- 工具 ----------
    def _run_js(self, code: str, callback=None, log_tag: str = ""):
        """执行 JS；callback(result) 在 JS 完成后调用；log_tag 非空时打印诊断日志。"""
        if log_tag:
            log.debug("JS [%s]: %s", log_tag, code[:200])
        if callback:
            self.web.page().runJavaScript(code, callback)
        else:
            self.web.page().runJavaScript(code)

    def _on_perf_changed(self, idx: int):
        """切换性能档位"""
        self._run_js(f"window.setPerfLevel({idx})")
        log.info("perf level → %d", idx)

    def _on_show_devices_toggled(self, on: bool):
        """勾选/取消「显示设备」：lazy 推送 devices 到 JS 渲染。

        低内存设备分批推送：每批 100 个、间隔 200ms，避免一次推送 1020 个
        JSON 让 JS 主线程卡 1-2 秒。
        """
        if not hasattr(self, "_pending_devices"):
            return
        if on:
            if not self._pending_devices:
                return
            devs = self._pending_devices
            BATCH = 500
            DELAY_MS = 50
            # 同坐标不同台区杆塔的颜色表（仅冲突簇内有值）
            pole_color_map = getattr(self, "_pole_colors", {}) or {}
            if pole_color_map:
                log.info("pole color map: %d 设备着色（%d 个冲突簇）",
                         len(pole_color_map),
                         getattr(self, "_pole_color_clusters", 0))
            log.info("lazy push %d devices to JS in batches of %d", len(devs), BATCH)

            def push_batch(start):
                end = min(start + BATCH, len(devs))
                if start >= end:
                    # 全部推完 → 最后统一飞行到设备范围 + 强制重绘 + 推设备索引 + 推连线
                    self._run_js("if (window.PowerAPI) { PowerAPI.fitAll(); viewer.scene.requestRender(); }")
                    # 设备全部推送完毕后：先推一个轻量"设备索引"（只 ID + 坐标），
                    # 这样 JS 端的 deviceById 立即完整；再推连线，连线端点能全部命中。
                    try:
                        idx_js = json.dumps(
                            {d.id: {"lon": round(d.lon, 6), "lat": round(d.lat, 6),
                                    "line": d.line or "", "type": d.device_type}
                             for d in self._all_devices},
                            ensure_ascii=False)
                        self._run_js(f"window.__setDeviceIndex({idx_js});")
                        # 推供电覆盖范围（凸包 + 缓冲多边形）
                        cov = getattr(self, "_pending_coverage", None)
                        if cov and cov.get("features"):
                            cov_js = coverage_to_js(cov)
                            self._run_js(cov_js)
                            log.info("coverage push dispatched: %d zones", len(cov["features"]))
                        # 然后推连线
                        links = getattr(self, "_pending_links", []) or build_links(self._all_devices)
                        if links:
                            links_js = json.dumps([[int(p), int(c), int(l)] for (p, c, l) in links], ensure_ascii=False)
                            self._run_js(f"window.__queueLinks({links_js});")
                            log.info("link push dispatched: %d edges", len(links))
                            # 诊断：连线配色与 4cm 真实线宽是否生效（渲染 ~4s 后采样，
                            # 结果走 JS console.log，由 LoggingPage 捕获为 [LINKWIDTH] 日志）
                            QTimer.singleShot(4000, lambda: self._run_js(
                                "getLinkWidthDebug()"))
                    except Exception as e:
                        log.exception("link push failed: %s", e)
                    return
                batch_js = json.dumps([
                    {"id": d.id, "lon": round(d.lon, 6), "lat": round(d.lat, 6),
                     "name": d.name, "line": d.line, "type": d.device_type,
                     "district": d.district, "src": d.src_file,
                     "color": pole_color_map.get(d.id),
                     "props": {k: str(v) for k, v in (d.props or {}).items()}}
                    for d in devs[start:end]
                ], ensure_ascii=False)
                self.web.page().runJavaScript(
                    f"if (typeof window.__queueDevices === 'function') {{ window.__queueDevices({batch_js}); }}")
                QTimer.singleShot(DELAY_MS, lambda: push_batch(end))

            push_batch(0)
            self._devices_rendered = True
        else:
            self._run_js("if (window.PowerAPI) PowerAPI.clearDevices();")
            self._devices_rendered = False

    # ---------- 连线显示 ----------
    def _recompute_and_push_links(self):
        """手动改挂接后：重算自动+override 连线并立即推送到地图（无需重新加载数据）。"""
        devs = getattr(self, "devices", [])
        if not devs:
            return
        auto_links = build_links(devs)
        ovr = load_overrides(str(OVERRIDES_FILE))
        if ovr:
            links, st = apply_link_overrides(devs, auto_links, ovr)
            log.info("recompute override links: applied=%d invalid=%d unassigned=%d total=%d (auto=%d)",
                     st['applied'], st['invalid'], st['unassigned'], st['total'], len(auto_links))
        else:
            links = auto_links
        self._pending_links = links
        # 设备已渲染过 → 直接全量重推连线（JS 端 renderLinks 会先清旧 datasource）
        if getattr(self, "_devices_rendered", False):
            links_js = json.dumps([[int(p), int(c), int(l)] for (p, c, l) in links], ensure_ascii=False)
            self._run_js(f"if (typeof window.__queueLinks === 'function') {{ window.__queueLinks({links_js}); }}")
            log.info("link push dispatched (override recompute): %d edges", len(links))

    def _on_links_toggled(self, on: bool):
        """勾选/取消「显示连线」：显示或隐藏 11000+ 条黄色高亮 Polyline。"""
        if on:
            if not hasattr(self, "_pending_links") or not self._pending_links:
                return
            links_js = json.dumps([[int(p), int(c), int(l)] for (p, c, l) in self._pending_links], ensure_ascii=False)
            self._run_js(f"window.__queueLinks({links_js});")
        else:
            self._run_js("if (window.PowerAPI) PowerAPI.clearLinks();")

    def _on_coverage_toggled(self, on: bool):
        """勾选/取消「覆盖范围」：显示或隐藏台区供电覆盖多边形。"""
        if on:
            cov = getattr(self, "_pending_coverage", None)
            if cov and cov.get("features"):
                cov_js = coverage_to_js(cov)
                self._run_js(cov_js)
                log.info("coverage re-shown: %d zones", len(cov["features"]))
        else:
            self._run_js("if (window.PowerAPI) PowerAPI.clearCoverage();")

    # ---------- 类型筛选 ----------
    def _active_type_filters(self):
        """当前勾选中的类型集合（空 = 不过滤）。"""
        active = []
        for type_name, _ in [
            ("柱上变（变压器）", "#e74c3c"),
            ("接入点（墙支架）", "#e53935"),
            ("杆塔", "#9E9E9E"),
            ("计量箱", "#1565C0"),
            ("其他", "#e67e22"),
        ]:
            btn = getattr(self, f"_btn_type_{type_name}", None)
            if btn and btn.isChecked():
                active.append(type_name)
        return active

    def _on_type_filter_toggled(self, type_name: str, checked: bool):
        """类型按钮勾选/取消：调 JS PowerAPI.setTypeFilter 过滤渲染。"""
        active = self._active_type_filters()
        # JS 端 setTypeFilter 期望 Set 或 Array<string>（null = 全部显示）
        if not active:
            self._run_js("PowerAPI.setTypeFilter(null)")
        else:
            import json as _json
            self._run_js(f"PowerAPI.setTypeFilter(new Set({_json.dumps(active)}))")
        # 状态栏实时反映筛选后数量
        self._refresh_stats()

    def _refresh_stats(self):
        """刷新设备总数 + 各类型计数（工具栏 lbl_stats）。"""
        if not hasattr(self, "_all_devices"):
            self.lbl_stats.setText("总数 0")
            return
        active = set(self._active_type_filters())
        if active:
            total = sum(1 for d in self._all_devices if d.device_type in active)
        else:
            total = len(self._all_devices)
        # 各类型计数
        counts = {}
        for d in self._all_devices:
            counts[d.device_type] = counts.get(d.device_type, 0) + 1
        parts = [f"总数 {total}"]
        # 只显示当前有数据的类型
        order = [
            ("柱上变（变压器）", "柱"),
            ("接入点（墙支架）", "接"),
            ("杆塔", "杆"),
            ("计量箱", "计"),
            ("其他", "其"),
        ]
        for tn, short in order:
            if counts.get(tn):
                # 筛选激活时显示「筛后数/总数」，否则只显示「总数」
                if active and tn in active:
                    parts.append(f"{short}{counts[tn]}")
                elif not active:
                    parts.append(f"{short}{counts[tn]}")
        self.lbl_stats.setText(" · ".join(parts))

    # ---------- 导出 CSV ----------
    def _on_export_csv(self):
        """导出当前可见（搜索过滤后 + 类型筛选后）的设备列表为 CSV。

        CSV 字段：编号、名称、类型、台区、镇街、经度、纬度、来源文件、备注属性...
        """
        if not hasattr(self, "_all_devices") or not self._all_devices:
            QMessageBox.warning(self, "导出失败", "暂无可导出的设备（请先加载数据）")
            return
        # 搜索词 + 类型筛选 → 计算可见设备
        search_txt = self.search.text().strip() if hasattr(self, "search") else ""
        active_types = set(self._active_type_filters())
        visible = []
        for d in self._all_devices:
            # 类型筛选
            if active_types and d.device_type not in active_types:
                continue
            # 搜索词（节点名 / 台区 / props 任一命中即保留）
            if search_txt:
                t = search_txt.lower()
                hay = " ".join([
                    d.name or "",
                    d.line or "",
                    " ".join(str(v) for v in (d.props or {}).values()),
                ]).lower()
                if t not in hay:
                    continue
            visible.append(d)
        if not visible:
            QMessageBox.information(self, "无数据", "当前筛选条件下没有可见设备")
            return
        # 保存对话框（默认文件名带时间戳 + 筛选标记）
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = ""
        if active_types:
            suffix += "_" + "+".join(t[:4] for t in sorted(active_types))
        if search_txt:
            suffix += "_搜索"
        default_name = f"设备导出_{ts}{suffix}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "导出设备清单为 CSV", default_name,
                                              "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            # 收集所有 props 字段作为列
            prop_keys = []
            seen = set()
            for d in visible:
                for k in (d.props or {}):
                    if k not in seen:
                        seen.add(k)
                        prop_keys.append(k)
            cols = ["编号", "名称", "类型", "台区", "镇街", "经度", "纬度",
                    "来源文件"] + [f"属性_{k}" for k in prop_keys]
            with open(path, "w", encoding="gbk", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(cols)
                for d in visible:
                    row = [
                        d.id, d.name, d.device_type, d.line, d.district or "",
                        f"{d.lon:.6f}", f"{d.lat:.6f}",
                        d.src_file or "",
                    ]
                    for k in prop_keys:
                        row.append((d.props or {}).get(k, ""))
                    writer.writerow(row)
            QMessageBox.information(
                self, "导出完成",
                f"已导出 {len(visible)} 条设备到：\n{path}\n\n（GBK 编码，Excel/WPS 直接打开）"
            )
        except Exception as e:
            log.exception("export CSV failed")
            QMessageBox.critical(self, "导出失败", str(e))

    def _warmup_tiles(self, devs: list):
        """预热瓦片缓存：按每个台区中心拉 z=14 的 4x4 瓦片块。"""
        from concurrent.futures import ThreadPoolExecutor

        # 按台区去重
        groups = {}
        for d in devs:
            key = (round(d.lon, 3), round(d.lat, 3))
            groups.setdefault(key, (d.lon, d.lat))
        centers = list(groups.values())
        log.info("tile warmup: %d 台区中心", len(centers))

        # Web Mercator → tile coords
        def lonlat_to_tile(lon, lat, z):
            n = 2 ** z
            x = int((lon + 180.0) / 360.0 * n)
            lat_rad = math.radians(lat)
            y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
            return x, y

        # 每个中心 z=14 的 4x4 范围
        z = 14
        tiles = set()
        for lon, lat in centers:
            cx, cy = lonlat_to_tile(lon, lat, z)
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    tiles.add((z, cx + dx, cy + dy))
        log.info("tile warmup: %d 瓦片 z=%d", len(tiles), z)

        # 并发拉（走代理 server 自己）
        port = self.tile_proxy.port

        def fetch_one(t):
            z, x, y = t
            import requests
            try:
                requests.get(f"http://127.0.0.1:{port}/tiles/imagery/{z}/{x}/{y}", timeout=8)
            except Exception:
                pass
            # 注记层已移除，不再拉 labels
            return t

        ok = 0
        # 低内存/无 GPU 设备：并发数从 8 降到 2，避免抢占主线程
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = [ex.submit(fetch_one, t) for t in tiles]
            for f in futs:
                if f.result():
                    ok += 1
        log.info("tile warmup done: %d tiles fetched", ok)

    def _refresh_stats(self):
        h, m, total = self.tile_proxy.stats()
        size_mb = self._last_size_mb
        if self._last_size_mb < 0:
            try:
                sz = 0
                for p in TILE_CACHE_DIR.rglob("*"):
                    if p.is_file():
                        sz += p.stat().st_size
                size_mb = sz / (1024 * 1024)
                self._last_size_mb = size_mb
            except Exception:
                size_mb = 0
        # 状态栏
        self.stats_label.setText(f"瓦片：{total}请求 命中 {h}  未命中 {m}  占用 {size_mb:.1f}MB")
        # 诊断：前端把状态写进 document.title
        # 多种格式：PC|dev=...|atlas=Y|...（正常）/ PC|renderError=N|...（渲染错误）
        t = self.web.title()
        if t.startswith("PC|renderError="):
            log.warning("[render-error] %s", t)
        elif t.startswith("PC|"):
            log.debug("[diagnose] %s", t)

    def closeEvent(self, e):
        try:
            self.tile_proxy.stop()
        except Exception:
            pass
        super().closeEvent(e)


# 一个简单的 Signal 包装（跨线程用）
# 已删除，统一用 QThread 子类


# ============== 入口 ==============
def _inject_chromium_args():
    """给 WebEngine 注入 Chromium 启动参数，必须在 QApplication 前调用。

    关键策略：保留 WebGL（Cesium 必须），但强制 Chromium 用软件光栅化（swiftshader）
    —— 无 GPU 设备也能 WebGL 渲染，只是走 CPU，性能低但能跑。

    参数：
      --use-gl=swiftshader：强制软件 GL（无 GPU 设备必备）
      --enable-unsafe-swiftshader：允许 swiftshader（新版 Chromium 默认禁用）
      --js-flags=--max-old-space-size=512：JS 堆上限 512MB（默认无上限）
      --no-sandbox：关沙箱（嵌入式 WebEngine 必要）
      --disable-features=VizDisplayCompositor：避免 GPU 合成器
    """
    need = [
        "--use-gl=swiftshader",
        "--enable-unsafe-swiftshader",
        "--js-flags=--max-old-space-size=512",
        "--no-sandbox",
        "--disable-features=VizDisplayCompositor,Vulkan",
        "--disable-gpu-driver-bug-workarounds",
    ]
    have = set(sys.argv[1:])
    for a in need:
        prefix = a.split("=")[0]
        if a in have or prefix in have:
            continue
        sys.argv.append(a)


def main():
    _inject_chromium_args()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("电力低压数据采集")
    app.setOrganizationName("PowerCollector")

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()