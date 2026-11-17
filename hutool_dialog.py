# -*- coding: utf-8 -*-
"""户表批量工具对话框 —— 与《户表批量工具》GUI 版（E:\\户表\\户表\\户表\\hutool_gui.py）功能对齐。

对齐项（逐条对应 GUI 版）：
  · 待处理根目录（浏览…）+ 子文件夹计数
  · 客户档案模板（内置，不可更改，显示路径）
  · 功能① 批量生成客户档案
  · 功能② 批量修改安卓表
  · 功能③ 提取整理 PDF
  · 功能④ 准备采集软件数据
  · 停止按钮（中断当前任务）
  · 清空日志按钮
  · 未匹配记录保留电能表条形码…（复选框，默认勾选）
  · 同时为《电力信息采集外业》采集软件保留「0杆」原样副本（复选框，默认勾选）
  · 运行日志区（逐条显示每个子文件夹的处理结果）
  · 状态文字 + 进度条 + 计数

本模块只用 PySide6（与 PowerCollector 一致），后台线程只做纯 Python/COM 操作，
所有 UI 更新通过 Qt 信号回主线程（跨线程直接操作控件会导致崩溃）。
"""
import os
import sys
import traceback

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSizePolicy, QVBoxLayout,
)

APP_TITLE = "户表批量工具"
BG = "#f4f6f8"
CARD_BG = "#ffffff"
FG = "#1c2833"
MUTED = "#7b8794"
BORDER = "#dde3e9"
ACCENT = "#f1c40f"
DANGER = "#e74c3c"
OK_GREEN = "#2ecc71"


def _core():
    """延迟导入 hutool_core（打包后由 hiddenimports 提供）。"""
    import hutool_core
    return hutool_core


class HutoolDialog(QDialog):
    """户表批量工具主对话框。"""

    # 后台线程 → 主线程
    logLine = Signal(str)        # 一行日志
    progress = Signal(int, int)  # done, total
    status = Signal(str)         # 状态文字
    done = Signal(str, dict, bool)  # 标题, 统计, 是否错误

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(880, 640)
        self.setStyleSheet(self._qss())

        self._stop_flag = False
        self.busy = False
        self._core_mod = None

        self.var_root = ""
        self.var_tpl = ""
        self.var_status = "就绪"
        self.var_progress = ""

        self._build_ui()
        self._connect_signals()

        QTimer.singleShot(0, self._refresh_template)

    # ------------------------------------------------------------------ 样式
    @staticmethod
    def _qss():
        return """
        QDialog { background:%(bg)s; }
        QLabel#hdrTitle { font-size:18px; font-weight:bold; color:%(fg)s; }
        QLabel#hdrSub   { font-size:11px; color:%(muted)s; }
        QLabel#cardTitle { font-size:13px; font-weight:bold; color:%(fg)s; }
        QLabel#muted    { font-size:11px; color:%(muted)s; }
        QLabel#status   { font-size:12px; color:%(fg)s; }
        QLabel#progTxt  { font-size:12px; color:%(muted)s; }
        QGroupBox {
            background:%(card)s; border:1px solid %(border)s; border-radius:6px;
            margin-top:12px; padding:10px;
        }
        QGroupBox::title {
            subcontrol-origin:margin; left:12px; padding:0 4px;
            color:%(fg)s; font-weight:bold;
        }
        QLineEdit, QPlainTextEdit {
            background:#fbfcfd; color:%(fg)s; border:1px solid %(border)s;
            border-radius:3px; padding:5px;
        }
        QPushButton {
            background:#e9eef3; color:%(fg)s; border:1px solid %(border)s;
            border-radius:4px; padding:7px 14px; font-size:12px;
        }
        QPushButton:hover  { background:#dde5ec; }
        QPushButton:disabled { background:#eef1f4; color:#aab4be; }
        QPushButton#accent { background:%(accent)s; color:%(fg)s; font-weight:bold; }
        QPushButton#accent:hover { background:#f6c90b; }
        QPushButton#danger { background:%(danger)s; color:#fff; font-weight:bold; }
        QPushButton#danger:hover { background:#c0392b; }
        QProgressBar {
            border:1px solid %(border)s; border-radius:4px; background:#eef1f4;
            height:16px; text-align:center; color:%(fg)s; font-size:10px;
        }
        QProgressBar::chunk { background:%(accent)s; border-radius:3px; }
        QCheckBox { color:%(fg)s; font-size:12px; spacing:6px; }
        QScrollBar:vertical { background:#f0f3f5; width:10px; }
        QScrollBar::handle:vertical { background:#c8d2da; border-radius:5px; min-height:24px; }
        """ % {"bg": BG, "card": CARD_BG, "fg": FG, "muted": MUTED,
               "border": BORDER, "accent": ACCENT, "danger": DANGER}

    # ------------------------------------------------------------------ 界面
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)

        # ---- 顶部标题 ----
        hdr = QHBoxLayout()
        t = QLabel(APP_TITLE)
        t.setObjectName("hdrTitle")
        s = QLabel("批量生成客户档案 / 批量修改安卓表 / 提取整理 PDF / 准备采集软件数据")
        s.setObjectName("hdrSub")
        s.setAlignment(Qt.AlignBottom)
        hdr.addWidget(t)
        hdr.addSpacing(12)
        hdr.addWidget(s)
        hdr.addStretch(1)
        root.addLayout(hdr)

        # ---- 卡片 1：路径设置 ----
        c1 = QGroupBox("路径设置")
        f1 = QFormLayout(c1)
        f1.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        f1.setSpacing(8)

        self.ent_root = QLineEdit()
        self.ent_root.setPlaceholderText("选择要批量处理的根目录（只处理它的第一层子文件夹）")
        self.btn_root = QPushButton("浏览…")
        self.btn_root.setFixedWidth(88)
        row_root = QHBoxLayout()
        row_root.addWidget(self.ent_root, 1)
        row_root.addWidget(self.btn_root)
        w_root = QFrame()
        w_root.setLayout(row_root)
        f1.addRow("待处理根目录", w_root)

        self.lbl_tpl = QLabel("—")
        self.lbl_tpl.setObjectName("muted")
        self.lbl_tpl.setWordWrap(True)
        f1.addRow("客户档案模板", self.lbl_tpl)

        m = QLabel("只处理根目录下的第一层子文件夹；客户档案模板已内置，不可更改")
        m.setObjectName("muted")
        f1.addRow("", m)
        root.addWidget(c1)

        # ---- 卡片 2：功能按钮 ----
        c2 = QGroupBox("功能")
        v2 = QVBoxLayout(c2)
        v2.setSpacing(8)

        grid = QHBoxLayout()
        grid.setSpacing(16)

        def func_col(title, desc, btn_text, slot):
            col = QVBoxLayout()
            col.setSpacing(4)
            lt = QLabel(title)
            lt.setObjectName("cardTitle")
            ld = QLabel(desc)
            ld.setObjectName("muted")
            ld.setWordWrap(True)
            b = QPushButton(btn_text)
            b.setObjectName("accent")
            b.setMinimumHeight(32)
            b.clicked.connect(slot)
            col.addWidget(lt)
            col.addWidget(ld)
            col.addSpacing(4)
            col.addWidget(b)
            col.addStretch(1)
            return col, b

        c, self.btn_gen = func_col(
            "功能 ①　批量生成客户档案",
            "按内置模板生成每个子文件夹的「<文件夹名>用户.xls」，数据取自同目录 .xlsx",
            "① 开始生成客户档案", self.on_generate)
        grid.addLayout(c, 1)
        c, self.btn_mod = func_col(
            "功能 ②　批量修改安卓表",
            "把每个子文件夹「*安卓.xls」中设备名称列的「0杆」改成文件夹名，原地保存",
            "② 开始修改安卓表", self.on_modify)
        grid.addLayout(c, 1)
        c, self.btn_pdf = func_col(
            "功能 ③　提取整理 PDF",
            "把根目录及各子文件夹内的全部 PDF 移到根目录下「打印」子目录",
            "③ 开始整理 PDF", self.on_collect_pdf)
        grid.addLayout(c, 1)
        c, self.btn_coll = func_col(
            "功能 ④　准备采集软件数据",
            "把各台区「*安卓.xls」原样复制到 _采集软件数据\\<台区名>\\，供采集软件读",
            "④ 准备采集软件数据", self.on_prepare_collector)
        grid.addLayout(c, 1)
        v2.addLayout(grid)

        # 停止 / 清空
        row_btn = QHBoxLayout()
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setFixedWidth(110)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_clear = QPushButton("清空日志")
        self.btn_clear.setFixedWidth(110)
        self.btn_clear.clicked.connect(self.on_clear)
        row_btn.addWidget(self.btn_stop)
        row_btn.addWidget(self.btn_clear)
        row_btn.addStretch(1)
        v2.addLayout(row_btn)

        # 选项
        self.chk_keep = QCheckBox(
            "未匹配记录保留电能表条形码、其余列留空，排在客户档案最后（不勾选则整条跳过）")
        self.chk_keep.setChecked(True)
        self.chk_collector = QCheckBox(
            "同时为《电力信息采集外业》采集软件保留一份「0杆」原样的副本"
            "（放到根目录 _采集软件数据\\，采集软件靠「0杆」识别母杆）")
        self.chk_collector.setChecked(True)
        v2.addWidget(self.chk_keep)
        v2.addWidget(self.chk_collector)
        root.addWidget(c2)

        # ---- 日志 + 状态栏（状态栏放最下，log 占据剩余空间）----
        bar = QHBoxLayout()
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setObjectName("status")
        self.lbl_prog = QLabel("")
        self.lbl_prog.setObjectName("progTxt")
        self.pb = QProgressBar()
        self.pb.setRange(0, 1)
        self.pb.setValue(0)
        self.pb.setFixedWidth(240)
        bar.addWidget(self.lbl_status)
        bar.addStretch(1)
        bar.addWidget(self.pb)
        bar.addWidget(self.lbl_prog)
        root.addLayout(bar)

        c3 = QGroupBox("运行日志")
        v3 = QVBoxLayout(c3)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        _f = QFont("Consolas", 9)
        _f.setStyleHint(QFont.Monospace)
        self.txt_log.setFont(_f)
        self.txt_log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v3.addWidget(self.txt_log)
        root.addWidget(c3, 1)

    def _connect_signals(self):
        self.logLine.connect(self._append_log)
        self.progress.connect(self._on_progress)
        self.status.connect(self._on_status)
        self.done.connect(self._on_done)
        self.btn_root.clicked.connect(self.choose_root)
        self.ent_root.textChanged.connect(self._on_root_changed)

    # ------------------------------------------------------------------ 辅助
    def _append_log(self, line):
        self.txt_log.appendPlainText(line)

    def _on_status(self, text):
        self.lbl_status.setText(text)

    def _on_progress(self, done, total):
        self.pb.setRange(0, max(total, 1))
        self.pb.setValue(done)
        self.lbl_prog.setText("%d / %d" % (done, total))

    def _on_root_changed(self, text):
        if not text.strip():
            self.lbl_status.setText("就绪")
            return
        try:
            n = len(_core().list_subfolders(text.strip()))
            self.lbl_status.setText("已选择：%s （%d 个子文件夹）" % (text.strip(), n))
        except Exception:
            self.lbl_status.setText("已选择：%s" % text.strip())

    def _refresh_template(self):
        try:
            core = _core()
            self._core_mod = core
            tpl = core.default_template()
            self.var_tpl = tpl
            ok = os.path.isfile(tpl)
            self.lbl_tpl.setText(
                "%s（内置，不可更改）%s" % (tpl, "" if ok else "  ⚠ 找不到模板文件！"))
            if not ok:
                self.lbl_tpl.setStyleSheet("color:#e74c3c; font-size:11px;")
            else:
                self.lbl_tpl.setStyleSheet("color:#7b8794; font-size:11px;")
        except Exception as e:
            self.lbl_tpl.setText("模板不可用：%s" % e)
            self.lbl_tpl.setStyleSheet("color:#e74c3c; font-size:11px;")

    # ------------------------------------------------------------------ 交互
    def choose_root(self):
        d = QFileDialog.getExistingDirectory(self, "选择待处理的根目录",
                                             self.ent_root.text().strip() or "")
        if d:
            self.ent_root.setText(d)

    def on_clear(self):
        self.txt_log.clear()

    def on_stop(self):
        self._stop_flag = True
        self.lbl_status.setText("正在停止…")

    def _set_busy(self, busy):
        self.busy = busy
        for b in (self.btn_gen, self.btn_mod, self.btn_pdf, self.btn_coll, self.btn_root):
            b.setEnabled(not busy)
        self.chk_keep.setEnabled(not busy)
        self.chk_collector.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.ent_root.setEnabled(not busy)

    def _root_or_warn(self):
        root = self.ent_root.text().strip()
        if not root or not os.path.isdir(root):
            QMessageBox.warning(self, "提示", "请选择有效的根目录。")
            return None
        return root

    def _confirm(self, text):
        return QMessageBox.question(self, "确认", text,
                                     QMessageBox.Yes | QMessageBox.No,
                                     QMessageBox.Yes) == QMessageBox.Yes

    # ------------------------------------------------------- 功能① 批量生成
    def on_generate(self):
        root = self._root_or_warn()
        if not root:
            return
        core = _core()
        try:
            tpl = core.default_template()
        except Exception as e:
            QMessageBox.warning(self, "提示", "找不到内置的客户档案模板：\n%s\n\n%s" % (e, e))
            return
        if not os.path.isfile(tpl):
            QMessageBox.warning(
                self, "提示",
                "找不到内置的客户档案模板：\n%s\n\n请把 低压客户模板.xls 放到程序所在目录。" % tpl)
            return
        if not self._confirm("将处理 %s 下的所有子文件夹，\n生成「<文件夹名>用户.xls」。\n\n继续吗？" % root):
            return

        self._stop_flag = False
        keep_unmatched = self.chk_keep.isChecked()
        self._set_busy(True)
        self.lbl_status.setText("正在生成客户档案…")
        self.pb.setRange(0, 1)
        self.pb.setValue(0)

        def work():
            try:
                folders = core.list_subfolders(root)
                total = len(folders)
                self.logLine.emit("=== 功能① 批量生成客户档案 ===")
                self.logLine.emit("根目录: %s" % root)
                self.logLine.emit("模板  : %s (内置)" % tpl)
                self.logLine.emit("子文件夹: %d 个" % total)
                self.logLine.emit("-" * 60)
                state = {"n": 0}

                def log(line):
                    self.logLine.emit(line)
                    state["n"] += 1
                    self.progress.emit(state["n"], total)

                self.progress.emit(0, total)
                stats = core.batch_generate(root, tpl, log=log,
                                            should_stop=lambda: self._stop_flag,
                                            skip_unmatched=not keep_unmatched)
                self.logLine.emit("-" * 60)
                self.logLine.emit("完成：成功 %d / 失败 %d / 共 %d"
                                  % (stats["ok"], stats["fail"], stats["total"]))
                self.done.emit("生成完成", stats, False)
            except Exception:
                self.logLine.emit("错误:\n" + traceback.format_exc())
                self.done.emit("生成失败", None, True)

        import threading
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------- 功能② 批量改安卓
    def on_modify(self):
        root = self._root_or_warn()
        if not root:
            return
        core = _core()
        if not self._confirm("将修改 %s 下子文件夹中的「*安卓.xls」，\n"
                             "把「0杆」改为文件夹名，原地保存。\n\n继续吗？" % root):
            return

        self._stop_flag = False
        keep_collector = self.chk_collector.isChecked()
        self._set_busy(True)
        self.lbl_status.setText("正在修改安卓表…")
        self.pb.setRange(0, 1)
        self.pb.setValue(0)

        def work():
            try:
                folders = core.list_subfolders(root)
                total = len(folders)
                self.logLine.emit("=== 功能② 批量修改安卓表 ===")
                self.logLine.emit("根目录: %s" % root)
                self.logLine.emit("子文件夹: %d 个" % total)
                self.logLine.emit("-" * 60)
                state = {"n": 0}

                def log(line):
                    self.logLine.emit(line)
                    state["n"] += 1
                    self.progress.emit(state["n"], total)

                self.progress.emit(0, total)
                stats = core.batch_modify_android(root, log=log,
                                                  should_stop=lambda: self._stop_flag,
                                                  keep_collector_copy=keep_collector)
                self.logLine.emit("-" * 60)
                self.logLine.emit("完成：成功 %d / 失败 %d / 跳过 %d / 共 %d"
                                  % (stats["ok"], stats["fail"], stats["skipped"], stats["total"]))
                if keep_collector:
                    dest = os.path.join(root, core.COLLECTOR_DIR)
                    self.logLine.emit("采集软件数据目录: %s" % dest)
                self.done.emit("修改完成", stats, False)
            except Exception:
                self.logLine.emit("错误:\n" + traceback.format_exc())
                self.done.emit("修改失败", None, True)

        import threading
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------- 功能③ 整理 PDF
    def on_collect_pdf(self):
        root = self._root_or_warn()
        if not root:
            return
        core = _core()
        if not self._confirm("将把 %s 及其子文件夹内的全部 PDF，\n"
                             "移动到 %s\\打印\\。\n\n继续吗？" % (root, root)):
            return

        self._stop_flag = False
        self._set_busy(True)
        self.lbl_status.setText("正在整理 PDF…")
        self.pb.setRange(0, 1)
        self.pb.setValue(0)

        def work():
            try:
                self.logLine.emit("=== 功能③ 提取整理 PDF ===")
                self.logLine.emit("根目录: %s" % root)
                self.logLine.emit("目标目录: %s" % os.path.join(root, core.PDF_SUBDIR))
                self.logLine.emit("-" * 60)
                state = {"n": 0}

                def log(line):
                    self.logLine.emit(line)
                    state["n"] += 1
                    self.progress.emit(state["n"], 100)

                self.progress.emit(0, 100)
                stats = core.collect_pdfs(root, log=log,
                                          should_stop=lambda: self._stop_flag)
                self.logLine.emit("-" * 60)
                self.logLine.emit("完成：移动 %d / 跳过 %d / 共 %d"
                                  % (stats["moved"], stats["skipped"], stats["total"]))
                self.done.emit("PDF 整理完成", stats, False)
            except Exception:
                self.logLine.emit("错误:\n" + traceback.format_exc())
                self.done.emit("PDF 整理失败", None, True)

        import threading
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------- 功能④ 采集数据
    def on_prepare_collector(self):
        root = self._root_or_warn()
        if not root:
            return
        core = _core()
        if not self._confirm("将把 %s 下各台区的「*安卓.xls」原样复制到\n"
                             "%s\\。\n\n继续吗？" % (root, os.path.join(root, core.COLLECTOR_DIR))):
            return

        self._stop_flag = False
        self._set_busy(True)
        self.lbl_status.setText("正在准备采集软件数据…")
        self.pb.setRange(0, 1)
        self.pb.setValue(0)

        def work():
            try:
                folders = core.list_subfolders(root)
                total = len(folders)
                self.logLine.emit("=== 功能④ 准备采集软件数据 ===")
                self.logLine.emit("根目录: %s" % root)
                self.logLine.emit("目标目录: %s" % os.path.join(root, core.COLLECTOR_DIR))
                self.logLine.emit("子文件夹: %d 个" % total)
                self.logLine.emit("-" * 60)
                state = {"n": 0}

                def log(line):
                    self.logLine.emit(line)
                    state["n"] += 1
                    self.progress.emit(state["n"], total)

                self.progress.emit(0, total)
                stats = core.prepare_collector_data(root, log=log,
                                                    should_stop=lambda: self._stop_flag)
                self.logLine.emit("-" * 60)
                self.logLine.emit("完成：台区 %d / 复制 %d 份"
                                  % (stats["total"], stats["copied"]))
                self.done.emit("采集软件数据准备完成", stats, False)
            except Exception:
                self.logLine.emit("错误:\n" + traceback.format_exc())
                self.done.emit("准备失败", None, True)

        import threading
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ 完成
    def _on_done(self, title, stats, error):
        self._set_busy(False)
        self.pb.setRange(0, 1)
        self.pb.setValue(0)
        self.lbl_prog.setText("")
        if error:
            self.lbl_status.setText(title)
            QMessageBox.critical(self, APP_TITLE, "%s\n详见运行日志。" % title)
        else:
            self.lbl_status.setText("%s — %s" % (title, self._stats_text(stats)))
            QMessageBox.information(self, APP_TITLE, "%s\n%s" % (title, self._stats_text(stats)))

    @staticmethod
    def _stats_text(stats):
        if not stats:
            return ""
        if "ok" in stats:
            return "成功 %d / 失败 %d / 共 %d" % (
                stats.get("ok", 0), stats.get("fail", 0), stats.get("total", 0))
        if "moved" in stats:
            return "移动 %d / 跳过 %d / 共 %d" % (
                stats.get("moved", 0), stats.get("skipped", 0), stats.get("total", 0))
        if "copied" in stats:
            return "台区 %d / 复制 %d 份" % (stats.get("total", 0), stats.get("copied", 0))
        return str(stats)
