# -*- coding: utf-8 -*-
"""main_window.py — 主窗口（PySide6 版，对齐 v8.2.8 截图）

布局结构（v8.2.8 单栏布局）：
  标题栏 → 平台 tab → URL 输入 → 按钮行 → 结果区（可滚动）→ 状态栏
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QLineEdit, QScrollArea, QGridLayout, QSizePolicy, QMenu,
    QMessageBox, QButtonGroup,
)
from PySide6.QtGui import QColor, QDesktopServices, QIcon
from PySide6.QtCore import QUrl

import os
import sys

from .theme import Colors, PLATFORM_META
from .widgets.stream_card import StreamCard
from .controller import FetchWorker, LoginCheckWorker, ProxyStartWorker
from .transcode_dialog import TranscodeDialog


def _icon_path(relative: str) -> str:
    """解析图标绝对路径（兼容开发模式 + PyInstaller onefile）。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative)


class MainWindow(QMainWindow):
    """直播流获取工具主窗口（v8.2.8 单栏布局）。"""

    fetchRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("影视匠直播流获取工具 v8.2.8 · LONGSHAO")
        self.resize(1080, 800)
        self.setMinimumSize(960, 720)

        self._stream_cards = []
        self._all_streams = []
        self._result_platform = ""
        self._fetch_worker = None
        self._login_workers = []
        self._proxy_worker = None
        self._card_by_url = {}

        # 累计访问次数（mock 展示，真实环境可对接后端）
        self._visit_count = 105432
        self._start_date = "2024-06-25"

        self._build_ui()
        self.fetchRequested.connect(self._start_fetch)
        self._start_login_checks()

    # ═══════════════════════════════════════════════════
    # UI 构建（单栏布局）
    # ═══════════════════════════════════════════════════
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 0)
        root.setSpacing(10)

        # 1. 标题栏
        root.addWidget(self._build_title_bar())

        # 2. 平台选择 + URL 输入
        root.addLayout(self._build_platform_and_url())

        # 3. 按钮行
        root.addLayout(self._build_action_buttons())

        # 4. 结果区（可滚动）
        root.addWidget(self._build_result_section(), 1)

        # 5. 状态栏
        root.addWidget(self._build_status_bar())

    # ── 标题栏 ──
    def _build_title_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("titlebar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 6)
        h.setSpacing(8)

        title = QLabel("🎬 影视匠直播流获取工具 v8.2.8")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {Colors.TEXT_PRIMARY};"
            f"background: transparent;"
        )
        h.addWidget(title)
        h.addStretch(1)

        # 右侧 v8.2.8 徽章
        ver_badge = QLabel("v8.2.8")
        ver_badge.setStyleSheet(
            f"font-size: 11px; color: {Colors.TEXT_PRIMARY};"
            f"background: {Colors.BG_CARD}; border: 1px solid {Colors.BORDER};"
            f"border-radius: 4px; padding: 2px 8px;"
        )
        h.addWidget(ver_badge)

        # 窗口控制：最小化 + 关闭
        min_btn = QPushButton("—")
        min_btn.setObjectName("winCtrlBtn")
        min_btn.setFixedSize(28, 22)
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.clicked.connect(self.showMinimized)
        h.addWidget(min_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("winCloseBtn")
        close_btn.setFixedSize(28, 22)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        h.addWidget(close_btn)

        return bar

    # ── 平台 tab + URL 输入 ──
    def _build_platform_and_url(self) -> QVBoxLayout:
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # 平台 tab
        platform_row = QHBoxLayout()
        platform_row.setSpacing(8)

        platform_lbl = QLabel("平台：")
        platform_lbl.setStyleSheet(
            f"font-size: 13px; color: {Colors.TEXT_PRIMARY}; background: transparent;"
        )
        platform_row.addWidget(platform_lbl)

        self._platform_tabs = {}
        self._platform_btn_group = QButtonGroup(self)
        self._platform_btn_group.setExclusive(True)
        for key, meta in PLATFORM_META.items():
            btn = QPushButton(f"  {meta['short']}  ")
            btn.setObjectName("platformTab")
            btn.setCheckable(True)
            btn.setProperty("platform", key)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, k=key: self._on_platform_clicked(k))

            # 设置真实平台图标（来自用户提供的 PNG）
            icon_path = _icon_path(meta.get("icon_path", ""))
            if icon_path and os.path.exists(icon_path):
                icon = QIcon(icon_path)
                btn.setIcon(icon)
                from PySide6.QtCore import QSize
                btn.setIconSize(QSize(20, 20))

            self._platform_tabs[key] = btn
            self._platform_btn_group.addButton(btn)
            platform_row.addWidget(btn)
        platform_row.addStretch(1)
        outer.addLayout(platform_row)

        # URL 输入（带内置 X 清除按钮）
        self.url_input = QLineEdit()
        self.url_input.setObjectName("urlInput")
        self.url_input.setPlaceholderText("粘贴直播间网页地址 (URL) ...")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.setMinimumHeight(40)
        self.url_input.returnPressed.connect(self._on_fetch_clicked)
        outer.addWidget(self.url_input)

        return outer

    # ── 按钮行（6 个按钮，固定配色）──
    def _build_action_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        # 获取流链接（金色）
        self.fetch_btn = QPushButton("🔗 获取流链接")
        self.fetch_btn.setObjectName("fetchBtn")
        self.fetch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fetch_btn.setMinimumHeight(40)
        self.fetch_btn.clicked.connect(self._on_fetch_clicked)
        row.addWidget(self.fetch_btn)

        # HEVC 转码（紫色）
        self.transcode_top_btn = QPushButton("🔄 HEVC 转码")
        self.transcode_top_btn.setObjectName("transcodeTopBtn")
        self.transcode_top_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.transcode_top_btn.setMinimumHeight(40)
        self.transcode_top_btn.clicked.connect(self._on_transcode_clicked)
        row.addWidget(self.transcode_top_btn)

        # 代理设置（蓝色）
        self.proxy_btn = QPushButton("🌐 代理设置")
        self.proxy_btn.setObjectName("proxyBtn")
        self.proxy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.proxy_btn.setMinimumHeight(40)
        self.proxy_btn.clicked.connect(self._on_proxy_clicked)
        row.addWidget(self.proxy_btn)

        # 复制全部（蓝色）
        self.copy_all_btn = QPushButton("📋 复制全部")
        self.copy_all_btn.setObjectName("copyAllBtn")
        self.copy_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_all_btn.setMinimumHeight(40)
        self.copy_all_btn.clicked.connect(self._on_copy_all)
        row.addWidget(self.copy_all_btn)

        # 系统代理（青色）
        self.sys_proxy_btn = QPushButton("⚙️ 系统代理")
        self.sys_proxy_btn.setObjectName("sysProxyBtn")
        self.sys_proxy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sys_proxy_btn.setMinimumHeight(40)
        self.sys_proxy_btn.clicked.connect(self._on_system_proxy)
        row.addWidget(self.sys_proxy_btn)

        # 视频号工具（绿色）
        self.wechat_btn = QPushButton("💬 视频号工具")
        self.wechat_btn.setObjectName("wechatBtn")
        self.wechat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wechat_btn.setMinimumHeight(40)
        self.wechat_btn.clicked.connect(self._on_wechat_tool)
        row.addWidget(self.wechat_btn)

        row.addStretch(1)
        return row

    # ── 结果区 ──
    def _build_result_section(self) -> QWidget:
        wrap = QFrame()
        wrap.setObjectName("resultPanel")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)

        # 标题"解析结果展示区"
        title = QLabel("解析结果展示区")
        title.setObjectName("resultTitle")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {Colors.TEXT_PRIMARY};"
            f"background: transparent;"
        )
        lay.addWidget(title)

        # 平台信息行
        self.platform_info_label = QLabel("当前解析平台：")
        self.platform_info_label.setStyleSheet(
            f"font-size: 12px; color: {Colors.TEXT_SECONDARY}; background: transparent;"
        )
        lay.addWidget(self.platform_info_label)

        # 筛选行
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.filter_dim_label = QLabel("清晰度/格式")
        self.filter_dim_label.setStyleSheet(
            f"font-size: 12px; color: {Colors.TEXT_SECONDARY}; background: transparent;"
        )
        filter_row.addWidget(self.filter_dim_label)

        self.spec_chips_layout = QHBoxLayout()
        self.spec_chips_layout.setSpacing(8)
        filter_row.addLayout(self.spec_chips_layout)
        filter_row.addStretch(1)
        lay.addLayout(filter_row)

        # 结果容器（可滚动）
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setObjectName("resultScroll")

        self.result_container = QWidget()
        self.result_layout = QVBoxLayout(self.result_container)
        self.result_layout.setContentsMargins(0, 0, 0, 0)
        self.result_layout.setSpacing(6)
        self.scroll.setWidget(self.result_container)
        lay.addWidget(self.scroll, 1)

        # 默认空态
        self._show_placeholder()

        # 筛选状态
        self._filter_dimension = "quality"
        self._filter_val = "全部"
        return wrap

    # ── 状态栏 ──
    def _build_status_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("statusBar")
        bar.setFixedHeight(32)

        h = QHBoxLayout(bar)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(14)

        # 左侧：登录状态（"xxx 直播登录状态：已红绿"格式）
        self.status_login = QLabel("小红书直播登录状态：检测中...")
        self.status_login.setObjectName("statusLogin")
        self.status_login.setStyleSheet(
            f"font-size: 12px; color: {Colors.TEXT_SECONDARY}; background: transparent;"
        )
        h.addWidget(self.status_login)

        h.addStretch(1)

        # 右侧：日期 | 累计访问次数
        self.status_visit = QLabel(
            f"日期：{self._start_date} | 累计访问次数：{self._visit_count:,}"
        )
        self.status_visit.setObjectName("statusVisit")
        self.status_visit.setStyleSheet(
            f"font-size: 12px; color: {Colors.TEXT_SECONDARY}; background: transparent;"
        )
        h.addWidget(self.status_visit)

        return bar

    # ═══════════════════════════════════════════════════
    # 平台选择（点击 tab → 用内置 Chromium 打开平台 URL）
    # ═══════════════════════════════════════════════════
    def _on_platform_clicked(self, key: str):
        """点击平台 tab：用内置 persistent_context Chromium 打开平台 URL。
        与解析流时共享同一 data_dir，登录后 cookie 自动复用，无需重复登录。"""
        self._selected_platform = key
        meta = PLATFORM_META[key]
        self.status_login.setText(f"{meta['short']}直播登录状态：检测中...")

        target_url = meta.get("open_url")
        if not target_url:
            return

        try:
            from live_stream_fetcher import _open_platform_in_chromium
            ok = _open_platform_in_chromium(key, target_url)
            if ok:
                self.status_login.setText(f"{meta['short']}直播登录状态：已打开内置浏览器")
            else:
                self.status_login.setText(f"{meta['short']}：未支持的内置浏览器打开")
        except Exception as e:
            # 业务层调用失败时兜底：用系统浏览器
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(target_url))
            self.status_login.setText(f"内置浏览器启动失败，已用系统浏览器打开：{e}")

    # 平台官网 URL 映射
    _PLATFORM_URLS = {
        "dy": "https://live.douyin.com/",
        "ks": "https://live.kuaishou.com/",
        "xhs": "https://www.xiaohongshu.com/",
        "tb": "https://tbzb.taobao.com/",
        "yy": "https://www.yy.com/",
    }

    # ═══════════════════════════════════════════════════
    # 解析流程
    # ═══════════════════════════════════════════════════
    def _on_fetch_clicked(self):
        url = self.url_input.text().strip()
        if not url:
            self.platform_info_label.setText("⚠ 请输入直播间链接")
            return
        self.fetchRequested.emit(url)

    def _start_fetch(self, url: str):
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            self.platform_info_label.setText("⚠ 正在解析中，请稍候...")
            return

        self.platform_info_label.setText("⏳ 正在解析直播流...")
        self._clear_result_cards()

        worker = FetchWorker(url)
        worker.succeeded.connect(self._on_fetch_success)
        worker.failed.connect(self._on_fetch_error)
        worker.finished.connect(self._on_fetch_finished)
        self._fetch_worker = worker
        worker.start()

    def _on_fetch_success(self, result: dict):
        streams = result.get("streams", [])
        platform = result.get("platform", "")
        title = result.get("title", "")
        method = result.get("method_used", "")

        self.render_streams(streams, platform=platform)

        info = f"当前解析平台：{platform}"
        if title:
            info += f" - {title}"
        info += f" （{method}）"
        self.platform_info_label.setText(info)
        self.status_login.setText(f"{platform}登录状态：解析中...")

        # 淘宝/小红书自动启动本地代理
        if platform in ("淘宝直播", "小红书"):
            self._start_proxy(streams, platform)

    def _on_fetch_error(self, msg: str):
        self._show_placeholder()
        self.platform_info_label.setText("解析失败")
        QMessageBox.critical(self, "解析失败", msg)

    def _on_fetch_finished(self):
        self._fetch_worker = None

    # ═══════════════════════════════════════════════════
    # 登录态检测
    # ═══════════════════════════════════════════════════
    def _start_login_checks(self):
        for key, meta in PLATFORM_META.items():
            if not meta.get("login"):
                # 不需要登录的平台直接显示
                self._set_login_status_label(key, "未启用")
                continue
            worker = LoginCheckWorker(key)
            worker.statusReady.connect(self._on_login_status)
            self._login_workers.append(worker)
            worker.start()

    def _on_login_status(self, key: str, online: bool, expired: bool):
        if expired:
            status = "已失效"
        elif online:
            status = "已红绿"   # 截图里"已红绿"是"已登录"的口语化文案
        else:
            status = "未登录"
        self._set_login_status_label(key, status)

    def _set_login_status_label(self, key: str, status: str):
        meta = PLATFORM_META.get(key, {})
        short = meta.get("short", key)
        self.status_login.setText(f"{short}直播登录状态：{status}")

    # ═══════════════════════════════════════════════════
    # OBS 代理
    # ═══════════════════════════════════════════════════
    def _start_proxy(self, streams: list, platform: str):
        if self._proxy_worker is not None and self._proxy_worker.isRunning():
            return
        worker = ProxyStartWorker(streams, platform)
        worker.ready.connect(self._on_proxy_ready)
        worker.failed.connect(lambda msg: self.platform_info_label.setText(f"代理启动失败：{msg}"))
        self._proxy_worker = worker
        worker.start()

    def _on_proxy_ready(self, proxy_map: dict):
        for url, proxy_url in proxy_map.items():
            card = self._card_by_url.get(url)
            if card is not None:
                card.set_obs_ready(proxy_url)

    # ═══════════════════════════════════════════════════
    # 按钮事件
    # ═══════════════════════════════════════════════════
    def _on_copy_all(self):
        if not self._all_streams:
            self.platform_info_label.setText("暂无流可复制")
            return
        from PySide6.QtWidgets import QApplication
        all_urls = "\n".join(s.get("url", "") for s in self._all_streams)
        QApplication.clipboard().setText(all_urls)
        self.platform_info_label.setText(f"已复制全部 {len(self._all_streams)} 条流链接")

    def _on_transcode_clicked(self):
        dlg = TranscodeDialog(parent=self)
        dlg.exec()

    def _on_proxy_clicked(self):
        self.platform_info_label.setText("解析淘宝/小红书流后自动启动本地代理")

    def _on_system_proxy(self):
        try:
            from live_stream_fetcher import _is_system_proxy_on, _set_system_proxy, _clear_system_proxy
            if _is_system_proxy_on():
                _clear_system_proxy()
                self.platform_info_label.setText("系统代理已关闭")
            else:
                addr = _set_system_proxy(8080)
                self.platform_info_label.setText(f"系统代理已开启（{addr}）")
        except Exception as e:
            self.platform_info_label.setText(f"系统代理操作失败：{e}")

    def _on_wechat_tool(self):
        try:
            from live_stream_fetcher import _ensure_wechat_video_tool
            exe_path = _ensure_wechat_video_tool()
            if not exe_path:
                self.platform_info_label.setText("视频号工具未找到")
                return
            import subprocess, os
            subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path))
            self.platform_info_label.setText("视频号下载工具已启动")
        except Exception as e:
            self.platform_info_label.setText(f"视频号工具启动失败：{e}")

    # ═══════════════════════════════════════════════════
    # 结果渲染
    # ═══════════════════════════════════════════════════
    _GUIDE_DATA = [
        ("快手直播", "#FF6A00", [
            "粘贴快手直播链接，点击「解析直播流」",
            "等待浏览器自动弹出（Edge / Chrome），不要关闭",
            "如出现验证码，在弹出的浏览器中手动完成",
            "页面加载完成后工具自动提取直播流地址",
        ], "首次会自动打开快手二维码登录页，手机扫码后自动跳转解析"),
        ("淘宝直播", "#FF6A00", [
            "粘贴淘宝直播链接（支持 tbzb.taobao.com / live.taobao.com）",
            "等待浏览器自动弹出，如需登录请扫码淘宝账号",
            "浏览器自动监听网络请求，提取直播流地址",
            "提取完成后浏览器自动关闭，流链接显示在列表中",
        ], "需要浏览器自动化解析，首次使用需登录淘宝账号"),
        ("小红书直播", "#FE2C55", [
            "点击「解析直播流」会自动弹出浏览器",
            "首次使用需登录小红书账号（手机扫码）",
            "登录成功后自动跳转直播间解析",
            "提取完成后浏览器自动关闭",
        ], "需要浏览器自动化解析，首次使用需登录小红书账号"),
        ("抖音直播", "#FF6A00", [
            "粘贴抖音直播链接（live.douyin.com / douyin.com）",
            "点击状态栏登录点可提前扫码登录",
            "浏览器自动监听网络请求，提取直播流地址",
            "提取完成后浏览器自动关闭",
        ], "需要浏览器自动化解析，首次使用需登录抖音账号"),
    ]

    def _show_placeholder(self):
        self._clear_result_cards()
        ph = QLabel("📡  粘贴直播间链接，点击「获取流链接」开始解析")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet(
            f"font-size: 13px; color: {Colors.TEXT_MUTED}; background: transparent;"
            f"padding: 60px 0;"
        )
        self.result_layout.addWidget(ph)
        self.result_layout.addStretch(1)

    def _clear_result_cards(self):
        while self.result_layout.count():
            item = self.result_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._stream_cards = []
        self._card_by_url = {}

    def render_streams(self, streams: list, platform: str = ""):
        """渲染流列表。"""
        self._all_streams = streams
        self._result_platform = platform

        if not streams:
            self._clear_result_cards()
            self._show_placeholder()
            return

        self._build_spec_chips(streams)
        self._render_stream_cards(streams)

    def _build_spec_chips(self, streams: list):
        """按清晰度+规格生成 chips。"""
        # 清空
        while self.spec_chips_layout.count():
            item = self.spec_chips_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # 统计 quality_tag + quality
        from collections import Counter
        spec_counter = Counter(s.get("quality", "其他").strip() or "其他" for s in streams)
        for spec, count in spec_counter.most_common():
            chip = QPushButton(spec)
            chip.setObjectName("specChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setProperty("spec", spec)
            self.spec_chips_layout.addWidget(chip)

    def _render_stream_cards(self, streams: list):
        """渲染流卡片（单行布局）。"""
        self._clear_result_cards()
        for i, s in enumerate(streams):
            card = StreamCard(s, i, self._result_platform)
            card.copyClicked.connect(self._copy_url)
            card.obsClicked.connect(self._copy_url)
            card.transcodeClicked.connect(self._on_transcode_stream)
            self.result_layout.addWidget(card)
            self._stream_cards.append(card)
            self._card_by_url[s.get("url", "")] = card
        self.result_layout.addStretch(1)

    def _copy_url(self, url: str):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(url)
        self.platform_info_label.setText(f"已复制：{url[:60]}...")

    def _on_transcode_stream(self, url: str):
        dlg = TranscodeDialog(preset_url=url, parent=self)
        dlg.exec()
