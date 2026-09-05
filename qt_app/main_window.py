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
from PySide6.QtCore import QUrl, QDateTime, QTimer

import os
import sys

from .theme import Colors, PLATFORM_META, APP_VERSION_FULL
from .widgets.stream_card import StreamCard
from .controller import FetchWorker, LoginCheckWorker, ProxyStartWorker
from .transcode_dialog import TranscodeDialog
from .settings_dialog import SettingsDialog


def _icon_path(relative: str) -> str:
    """解析图标绝对路径（兼容开发模式 + PyInstaller onefile）。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative)


class MainWindow(QMainWindow):
    """直播流获取工具主窗口（v8.2.8 单栏布局）。"""

    fetchRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"影视匠直播流获取工具 {APP_VERSION_FULL} · LONGSHAO")
        self.resize(1080, 800)
        self.setMinimumSize(960, 720)

        # v8.3.7: 设置窗口图标（任务栏 + Alt-Tab）
        from PySide6.QtGui import QIcon
        _app_root = os.path.dirname(os.path.dirname(__file__))
        for icon_name in ("app_icon.ico", os.path.join("icons", "logo_main.png")):
            icon_path = os.path.join(_app_root, icon_name) if not os.path.isabs(icon_name) else icon_name
            if os.path.isfile(icon_path):
                self.setWindowIcon(QIcon(icon_path))
                break

        self._stream_cards = []
        self._all_streams = []
        self._result_platform = ""
        self._fetch_worker = None
        self._login_workers = []
        self._proxy_worker = None
        self._card_by_url = {}

        # 累计访问次数（v8.3.1：每次启动 +1，持久化到 %APPDATA%/LiveStreamFetcher/visit_count.json）
        try:
            from live_stream_fetcher import bump_visit_count_on_startup
            self._visit_count = bump_visit_count_on_startup()
        except Exception:
            # EXE/打包环境 fallback：不让缺这个库就把 UI 卡死
            self._visit_count = 0

        self._build_ui()
        self.fetchRequested.connect(self._start_fetch)
        self._start_login_checks()

        # v8.3.1：状态栏日期 1 分钟刷新一次（实时显示）
        self._date_timer = QTimer(self)
        self._date_timer.setInterval(60 * 1000)
        self._date_timer.timeout.connect(self._refresh_status_date)
        self._date_timer.start()
        # 首帧立即刷新一次
        QTimer.singleShot(0, self._refresh_status_date)

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

        # v8.3.7: 标题栏左侧 logo（用真实 PNG，替代之前的 🎬 emoji）
        from PySide6.QtGui import QPixmap
        logo_label = QLabel()
        logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons", "logo_main.png")
        if os.path.isfile(logo_path):
            logo_pixmap = QPixmap(logo_path).scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo_label.setPixmap(logo_pixmap)
        else:
            logo_label.setText("🎬")
        logo_label.setStyleSheet("background: transparent;")
        h.addWidget(logo_label)

        title = QLabel(f"影视匠直播流获取工具 {APP_VERSION_FULL}")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {Colors.TEXT_PRIMARY};"
            f"background: transparent;"
        )
        h.addWidget(title)
        h.addStretch(1)

        # 右侧版本徽章
        ver_badge = QLabel(APP_VERSION_FULL)
        ver_badge.setStyleSheet(
            f"font-size: 11px; color: #f0f4ff;"
            f"background: #252b45; border: 1px solid #3a4258;"
            f"border-radius: 4px; padding: 3px 10px;"
        )
        h.addWidget(ver_badge)

        # 窗口控制：最小化 + 关闭（用 ASCII 字符确保字体支持）
        min_btn = QPushButton("—")
        min_btn.setObjectName("winCtrlBtn")
        min_btn.setFixedSize(28, 22)
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.clicked.connect(self.showMinimized)
        h.addWidget(min_btn)

        close_btn = QPushButton("x")
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
        self.fetch_btn = QPushButton("获取流链接")
        self.fetch_btn.setObjectName("fetchBtn")
        self.fetch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fetch_btn.setMinimumHeight(40)
        self.fetch_btn.clicked.connect(self._on_fetch_clicked)
        row.addWidget(self.fetch_btn)

        # HEVC 转码（紫色）
        self.transcode_top_btn = QPushButton("HEVC 转码")
        self.transcode_top_btn.setObjectName("transcodeTopBtn")
        self.transcode_top_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.transcode_top_btn.setMinimumHeight(40)
        self.transcode_top_btn.clicked.connect(self._on_transcode_clicked)
        row.addWidget(self.transcode_top_btn)

        # 代理设置（蓝色）
        self.proxy_btn = QPushButton("代理设置")
        self.proxy_btn.setObjectName("proxyBtn")
        self.proxy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.proxy_btn.setMinimumHeight(40)
        self.proxy_btn.clicked.connect(self._on_proxy_clicked)
        row.addWidget(self.proxy_btn)

        # 复制全部（蓝色）
        self.copy_all_btn = QPushButton("复制全部")
        self.copy_all_btn.setObjectName("copyAllBtn")
        self.copy_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_all_btn.setMinimumHeight(40)
        self.copy_all_btn.clicked.connect(self._on_copy_all)
        row.addWidget(self.copy_all_btn)

        # 系统代理（青色）
        self.sys_proxy_btn = QPushButton("系统代理")
        self.sys_proxy_btn.setObjectName("sysProxyBtn")
        self.sys_proxy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sys_proxy_btn.setMinimumHeight(40)
        self.sys_proxy_btn.clicked.connect(self._on_system_proxy)
        row.addWidget(self.sys_proxy_btn)

        # 视频号工具（绿色）
        self.wechat_btn = QPushButton("视频号工具")
        self.wechat_btn.setObjectName("wechatBtn")
        self.wechat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wechat_btn.setMinimumHeight(40)
        self.wechat_btn.clicked.connect(self._on_wechat_tool)
        row.addWidget(self.wechat_btn)

        # 设置（灰色，v8.5.0：浏览器引擎等软件设置）
        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setMinimumHeight(40)
        self.settings_btn.clicked.connect(self._on_settings_clicked)
        row.addWidget(self.settings_btn)

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

        # 右侧：日期 | 累计访问次数（v8.3.1：实时日期，持久化累计值）
        self.status_visit = QLabel()
        self.status_visit.setObjectName("statusVisit")
        self.status_visit.setStyleSheet(
            f"font-size: 12px; color: {Colors.TEXT_SECONDARY}; background: transparent;"
        )
        h.addWidget(self.status_visit)
        # 立即填充（_refresh_status_date 也会调一次）
        self._refresh_status_date()

        return bar

    # ── 状态栏日期 / 访问次数更新（v8.3.1）─────────────────
    def _refresh_status_date(self):
        """每分钟由定时器触发；显示真实系统日期。"""
        today = QDateTime.currentDateTime().toString("yyyy-MM-dd")
        self.status_visit.setText(
            f"日期：{today} | 累计访问次数：{self._visit_count:,}"
        )

    # ═══════════════════════════════════════════════════
    # 平台选择（点击 tab → 用内置 Chromium 打开平台 URL）
    # ═══════════════════════════════════════════════════
    def _on_platform_clicked(self, key: str):
        """点击平台 tab：用内置 chromium 打开平台 URL。

        v8.3.9 流畅度优化：复用 session 时把 new_page + goto 放进 daemon thread，
        主线程立即返回"已打开"，新 tab 后台加载。首次启动 chrome.exe 时
        同步等待（合理，因为 chrome 启动需要时间）。
        """
        self._selected_platform = key
        meta = PLATFORM_META[key]
        self.status_login.setText(f"{meta['short']}直播登录状态：启动浏览器中...")

        target_url = meta.get("open_url")
        if not target_url:
            self.status_login.setText(f"{meta['short']}：无内置浏览器打开 URL")
            return

        try:
            from live_stream_fetcher import (
                _open_platform_in_chromium,
                _SHARED_BROWSER_SESSION as _check_shared,
            )
            # 流畅度优化：复用 session 时不走 wait_timeout 秒同步路径
            # _open_platform_in_chromium 内部检测 session 存在时直接复用，
            # 但这里我们额外把 new_page + goto 放进 daemon thread，避免阻塞主线程
            ok, err = _open_platform_in_chromium(key, target_url)
            if ok:
                # v8.5.2: 状态文案按实际引擎显示——v8.5.1 硬编码"已打开内置
                # 浏览器"，用系统引擎时也这么显示，误导用户以为设置没生效。
                # v8.5.3: 系统引擎改为调用系统默认浏览器（os.startfile）。
                try:
                    from live_stream_fetcher import _prefer_system_browser as _psb
                    _engine_txt = "系统默认浏览器" if _psb() else "内置浏览器"
                except Exception:
                    _engine_txt = "内置浏览器"
                self.status_login.setText(f"{meta['short']}直播登录状态：已打开{_engine_txt}")
            else:
                self.status_login.setText(f"{meta['short']}直播登录状态：浏览器启动失败 - {err}")
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
        if platform in ("淘宝直播", "小红书", "虎牙"):
            self._start_proxy(streams, platform)

    def _on_fetch_error(self, msg: str):
        self._show_placeholder()
        self.platform_info_label.setText("解析失败")
        # v8.4.12: 兜底截断超长错误信息（业务层已精简，这里防意外路径）
        display_msg = msg if len(msg) <= 400 else msg[:400] + "\n……（详情见日志）"
        QMessageBox.critical(self, "解析失败", display_msg)

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
            status = "已登录"
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

    def _on_settings_clicked(self):
        """v8.5.0: 打开设置对话框（浏览器引擎等）。"""
        dlg = SettingsDialog(parent=self)
        if dlg.exec():
            self.platform_info_label.setText("设置已保存，立即生效")

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
        """启动视频号下载工具。

        流程（用户明确要求）：先安装 缓存\\证书.p12 + 证书-cert.p12
        到 Windows 证书存储 → 再打开视频号工具。
        v8.5.8 加固：
        - 证书安装独立 try/except——任何证书侧异常（含 GBK 日志崩溃）
          都不再阻断工具启动（v8.5.7 实证 emoji 打印崩溃导致工具无法打开）
        - 启动前确保工具目录下「下载」目录存在（打包不含空目录，
          保持 工具目录/{缓存,下载} 原有结构）
        """
        try:
            from live_stream_fetcher import _ensure_wechat_video_tool, _install_wechat_certificates
            exe_path = _ensure_wechat_video_tool()
            if not exe_path:
                self.platform_info_label.setText("视频号工具未找到")
                return

            _exe_dir = os.path.dirname(exe_path)
            # 保持工具目录原有结构：确保「下载」目录存在
            try:
                os.makedirs(os.path.join(_exe_dir, "下载"), exist_ok=True)
            except Exception:
                pass

            # 先安装证书（视频号工具抓 HTTPS 视频必需）
            # v8.4.13: 证书在工具目录的"缓存"子目录中（保持原目录结构）
            cert_dir = os.path.join(_exe_dir, "缓存")
            if not os.path.isdir(cert_dir):
                cert_dir = _exe_dir  # 兼容旧版平铺结构
            self.platform_info_label.setText("正在安装视频号工具证书...")
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
            cert_msg = "证书安装异常，仍尝试启动"
            try:
                if _install_wechat_certificates(cert_dir):
                    cert_msg = "证书已安装"
                else:
                    cert_msg = "证书安装未完全成功，仍尝试启动"
            except Exception as e_cert:
                print(f"[视频号证书] 安装流程异常（不阻断启动）: {str(e_cert)[:120]}")

            import subprocess
            subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path))
            self.platform_info_label.setText(f"视频号下载工具已启动（{cert_msg}）")
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
        """按清晰度生成 chips（v8.3.2：chip 点击后只显示对应清晰度的流）。"""
        # 清空
        while self.spec_chips_layout.count():
            item = self.spec_chips_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not streams:
            return

        # 统计 quality_tag → count，按出现次数排序
        from collections import Counter
        quality_counter = Counter()
        for s in streams:
            q = (s.get("quality") or "").strip() or "其他"
            quality_counter[q] += 1

        # 「全部」chip + 各清晰度 chip
        all_chip = QPushButton(f"全部 ({len(streams)})")
        all_chip.setObjectName("specChip")
        all_chip.setCheckable(True)
        all_chip.setChecked(self._filter_val == "全部")
        all_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        all_chip.setProperty("spec", "全部")
        all_chip.clicked.connect(lambda: self._apply_filter("全部"))
        self.spec_chips_layout.addWidget(all_chip)

        for quality, count in quality_counter.most_common():
            chip = QPushButton(f"{quality} ({count})")
            chip.setObjectName("specChip")
            chip.setCheckable(True)
            chip.setChecked(self._filter_val == quality)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setProperty("spec", quality)
            chip.clicked.connect(lambda checked=False, q=quality: self._apply_filter(q))
            self.spec_chips_layout.addWidget(chip)

    def _apply_filter(self, quality: str):
        """点击 chip 时调用：按 quality 过滤并重渲染。"""
        self._filter_val = quality
        # 更新 chip 选中态
        for i in range(self.spec_chips_layout.count()):
            item = self.spec_chips_layout.itemAt(i)
            w = item.widget()
            if w is not None:
                w.setChecked(w.property("spec") == quality)
        # 重渲染
        self._render_filtered_streams()

    def _render_filtered_streams(self):
        """按 _filter_val 过滤 _all_streams 后渲染。"""
        if self._filter_val == "全部":
            visible = self._all_streams
        else:
            visible = [
                s for s in self._all_streams
                if (s.get("quality") or "").strip() == self._filter_val
            ]
        # 同步 chip UI（保持 _apply_filter 和 render_streams 两条入口一致）
        for i in range(self.spec_chips_layout.count()):
            item = self.spec_chips_layout.itemAt(i)
            w = item.widget()
            if w is not None:
                w.setChecked(w.property("spec") == self._filter_val)
        self._render_stream_cards(visible)

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
