# -*- coding: utf-8 -*-
"""main_window.py — 主窗口（PySide6 版）

布局结构（对齐原 Tkinter 版）：
  侧边栏（Logo + 菜单） | 主内容（顶栏 + hero + 平台网格 + 输入区 + 结果区 + 状态栏）
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QLineEdit, QScrollArea, QGridLayout, QSizePolicy, QMenu,
    QMessageBox,
)
from PySide6.QtGui import QColor

from .theme import Colors, PLATFORM_META
from .widgets.platform_card import PlatformCard
from .widgets.stream_card import StreamCard
from .controller import FetchWorker, LoginCheckWorker, ProxyStartWorker
from .transcode_dialog import TranscodeDialog


class MainWindow(QMainWindow):
    """直播流获取工具主窗口。"""

    fetchRequested = Signal(str)   # 请求解析 url

    def __init__(self):
        super().__init__()
        self.setWindowTitle("直播流获取工具 v8.2.8 · LONGSHAO")
        self.resize(1120, 820)
        self.setMinimumSize(980, 720)

        self._stream_cards = []       # 当前流卡片列表
        self._all_streams = []        # 全部解析结果
        self._selected_platform = None
        self._result_platform = ""    # 当前解析结果平台
        self._fetch_worker = None     # 当前解析线程
        self._login_workers = []      # 登录态检测线程列表
        self._proxy_worker = None     # 本地代理线程
        self._card_by_url = {}        # url → StreamCard 映射

        self._build_ui()

        # 连接解析信号 → 后台线程
        self.fetchRequested.connect(self._start_fetch)

        # 启动登录态检测
        self._start_login_checks()

    # ═══════════════════════════════════════════════════
    # UI 构建
    # ═══════════════════════════════════════════════════
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 侧边栏
        sidebar = self._build_sidebar()
        root.addWidget(sidebar)

        # 主内容区
        main_area = self._build_main_area()
        root.addWidget(main_area, 1)

    # ── 侧边栏 ──
    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)

        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(14, 20, 14, 16)
        lay.setSpacing(6)

        # Logo
        logo = QLabel("🎬 直播流获取")
        logo.setObjectName("logoTitle")
        lay.addWidget(logo)

        sub = QLabel("Live Stream Fetcher")
        sub.setObjectName("logoSubtitle")
        lay.addWidget(sub)

        lay.addSpacing(20)

        # 菜单项
        self._menu_buttons = []
        menu_items = [
            ("🏠", "首页", "home"),
            ("📡", "直播解析", "parse"),
            ("🔑", "登录管理", "login"),
            ("⚙️", "代理设置", "proxy"),
        ]
        for icon, name, key in menu_items:
            btn = QPushButton(f"{icon}  {name}")
            btn.setObjectName("sideMenuBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("active", key == "parse")
            lay.addWidget(btn)
            self._menu_buttons.append((key, btn))

        lay.addStretch(1)

        # 底部版本号
        ver = QLabel("v8.2.8")
        ver.setObjectName("logoSubtitle")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ver)

        return sidebar

    # ── 主内容区 ──
    def _build_main_area(self) -> QWidget:
        area = QWidget()
        area.setObjectName("mainArea")

        lay = QVBoxLayout(area)
        lay.setContentsMargins(20, 16, 20, 0)
        lay.setSpacing(14)

        # 顶栏
        lay.addWidget(self._build_topbar())

        # 可滚动内容
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("scroll")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("content")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(16)

        content_lay.addWidget(self._build_hero())
        content_lay.addWidget(self._build_platform_grid())
        content_lay.addWidget(self._build_input_section())
        content_lay.addWidget(self._build_result_section())
        content_lay.addStretch(1)

        self.scroll.setWidget(content)
        lay.addWidget(self.scroll, 1)

        # 状态栏
        lay.addWidget(self._build_status_bar())

        return area

    # ── 顶栏 ──
    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(2, 2, 2, 10)
        h.setSpacing(8)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("直播流解析")
        title.setObjectName("pageTitle")
        title_box.addWidget(title)
        sub = QLabel("输入直播间链接，一键获取可用流地址")
        sub.setObjectName("pageSubtitle")
        title_box.addWidget(sub)
        h.addLayout(title_box)
        h.addStretch(1)

        return bar

    # ── Hero 渐变区 ──
    def _build_hero(self) -> QWidget:
        hero = QFrame()
        hero.setObjectName("hero")
        hero.setFixedHeight(120)

        lay = QHBoxLayout(hero)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(20)

        text_box = QVBoxLayout()
        text_box.setSpacing(6)
        t = QLabel("专业直播流获取工具")
        t.setObjectName("heroTitle")
        text_box.addWidget(t)
        d = QLabel("支持抖音 · 快手 · 小红书 · 淘宝直播 · YY 直播 · 视频号")
        d.setObjectName("heroDesc")
        text_box.addWidget(d)
        lay.addLayout(text_box, 1)

        # 统计
        for value, label in [("6", "支持平台"), ("5", "登录托管"), ("1", "本地代理")]:
            stat = QVBoxLayout()
            stat.setSpacing(2)
            v = QLabel(value)
            v.setObjectName("heroStatValue")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stat.addWidget(v)
            l = QLabel(label)
            l.setObjectName("heroStatLabel")
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stat.addWidget(l)
            lay.addLayout(stat)

        return hero

    # ── 平台网格 ──
    def _build_platform_grid(self) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        title = QLabel("选择平台")
        title.setObjectName("sectionTitle")
        v.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(12)

        self._platform_cards = {}
        for i, (key, meta) in enumerate(PLATFORM_META.items()):
            card = PlatformCard(key, meta)
            card.clicked.connect(self._on_platform_clicked)
            card.rightClicked.connect(self._on_platform_right_clicked)
            grid.addWidget(card, i // 3, i % 3)
            self._platform_cards[key] = card

        v.addLayout(grid)
        return wrap

    # ── 输入区 ──
    def _build_input_section(self) -> QWidget:
        card = QFrame()
        card.setObjectName("inputCard")

        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        title = QLabel("直播间链接")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.url_input = QLineEdit()
        self.url_input.setObjectName("urlInput")
        self.url_input.setPlaceholderText("粘贴直播间链接，例如 https://live.douyin.com/xxxx")
        self.url_input.setMinimumHeight(42)
        self.url_input.returnPressed.connect(self._on_fetch_clicked)
        row.addWidget(self.url_input, 1)

        fetch_btn = QPushButton("获取流链接")
        fetch_btn.setObjectName("primaryBtn")
        fetch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fetch_btn.setMinimumHeight(42)
        fetch_btn.clicked.connect(self._on_fetch_clicked)
        row.addWidget(fetch_btn)

        lay.addLayout(row)

        # 次按钮行
        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)

        self.copy_all_btn = QPushButton("复制全部")
        self.copy_all_btn.setObjectName("ghostBtn")
        self.copy_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_all_btn.clicked.connect(self._on_copy_all)
        sub_row.addWidget(self.copy_all_btn)

        transcode_btn = QPushButton("HEVC转码")
        transcode_btn.setObjectName("ghostBtn")
        transcode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        transcode_btn.clicked.connect(self._on_transcode_clicked)
        sub_row.addWidget(transcode_btn)

        proxy_btn = QPushButton("代理设置")
        proxy_btn.setObjectName("ghostBtn")
        proxy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        proxy_btn.clicked.connect(self._on_proxy_clicked)
        sub_row.addWidget(proxy_btn)

        sys_proxy_btn = QPushButton("系统代理")
        sys_proxy_btn.setObjectName("ghostBtn")
        sys_proxy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sys_proxy_btn.clicked.connect(self._on_system_proxy)
        sub_row.addWidget(sys_proxy_btn)

        wechat_btn = QPushButton("视频号工具")
        wechat_btn.setObjectName("ghostBtn")
        wechat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        wechat_btn.clicked.connect(self._on_wechat_tool)
        sub_row.addWidget(wechat_btn)

        sub_row.addStretch(1)
        lay.addLayout(sub_row)

        return card

    # ── 结果区 ──
    def _build_result_section(self) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("解析结果")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.result_hint = QLabel("")
        self.result_hint.setObjectName("sectionHint")
        header.addWidget(self.result_hint)
        v.addLayout(header)

        # 筛选区：维度切换按钮 + 标签 chips
        self.filter_row = QHBoxLayout()
        self.filter_row.setSpacing(8)

        self.dim_quality_btn = QPushButton("清晰度")
        self.dim_quality_btn.setObjectName("filterChip")
        self.dim_quality_btn.setCheckable(True)
        self.dim_quality_btn.setChecked(True)
        self.dim_quality_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dim_quality_btn.clicked.connect(lambda: self._switch_filter_dimension("quality"))
        self.filter_row.addWidget(self.dim_quality_btn)

        self.dim_format_btn = QPushButton("格式")
        self.dim_format_btn.setObjectName("filterChip")
        self.dim_format_btn.setCheckable(True)
        self.dim_format_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dim_format_btn.clicked.connect(lambda: self._switch_filter_dimension("format"))
        self.filter_row.addWidget(self.dim_format_btn)

        # 分隔
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #2a3148;")
        sep.setFixedHeight(20)
        self.filter_row.addWidget(sep)

        # 标签 chips 容器
        self.filter_tags_layout = QHBoxLayout()
        self.filter_tags_layout.setSpacing(6)
        self.filter_row.addLayout(self.filter_tags_layout)
        self.filter_row.addStretch(1)
        v.addLayout(self.filter_row)

        # 结果容器
        self.result_container = QWidget()
        self.result_layout = QVBoxLayout(self.result_container)
        self.result_layout.setContentsMargins(0, 0, 0, 0)
        self.result_layout.setSpacing(8)
        v.addWidget(self.result_container)

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
        bar.setFixedHeight(40)

        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(14)

        self.status_login = QLabel("● 未登录")
        self.status_login.setObjectName("statusDot")
        h.addWidget(self.status_login)

        self.status_text = QLabel("就绪")
        self.status_text.setObjectName("statusText")
        h.addWidget(self.status_text)

        h.addStretch(1)

        author = QLabel("LONGSHAO")
        author.setObjectName("authorLabel")
        h.addWidget(author)

        return bar

    # ═══════════════════════════════════════════════════
    # 交互
    # ═══════════════════════════════════════════════════
    def _on_platform_clicked(self, key: str):
        self._selected_platform = key
        meta = PLATFORM_META[key]
        self.status_text.setText(f"已选择平台：{meta['name']}")

        # 高亮选中卡片
        for k, card in self._platform_cards.items():
            card.set_selected(k == key)

    # 平台官网 URL 映射（用于右键打开）
    _PLATFORM_URLS = {
        "dy": "https://live.douyin.com/",
        "ks": "https://live.kuaishou.com/",
        "xhs": "https://www.xiaohongshu.com/",
        "tb": "https://tbzb.taobao.com/",
        "yy": "https://www.yy.com/",
        "wechat": "https://channels.weixin.qq.com/",
    }

    def _on_platform_right_clicked(self, key: str):
        meta = PLATFORM_META[key]
        menu = QMenu(self)
        act_parse = menu.addAction(f"解析 {meta['name']}")
        act_open = menu.addAction("打开平台官网")
        chosen = menu.exec(self.mapFromGlobal(self.cursor().pos()))
        if chosen == act_parse:
            self._on_platform_clicked(key)
        elif chosen == act_open:
            url = self._PLATFORM_URLS.get(key)
            if url:
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl(url))
                self.status_text.setText(f"已打开 {meta['name']} 官网")

    def _on_fetch_clicked(self):
        url = self.url_input.text().strip()
        if not url:
            self.status_text.setText("⚠ 请输入直播间链接")
            return
        self.fetchRequested.emit(url)

    # ═══════════════════════════════════════════════════
    # 解析流程（后台线程）
    # ═══════════════════════════════════════════════════
    def _start_fetch(self, url: str):
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            self.status_text.setText("⚠ 正在解析中，请稍候...")
            return

        self.set_loading(True)
        self._clear_result_cards()
        self.result_hint.setText("解析中...")

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
        self.status_text.setText(f"解析成功 · {method}")

        # 平台标题回填
        if title:
            self.result_hint.setText(f"{title} · 共 {len(streams)} 条流")

        # 淘宝/小红书自动启动本地代理
        if platform in ("淘宝直播", "小红书"):
            self._start_proxy(streams, platform)

    def _start_proxy(self, streams: list, platform: str):
        """为需代理的流启动本地代理，就绪后更新 OBS 按钮。"""
        if self._proxy_worker is not None and self._proxy_worker.isRunning():
            return
        self.status_text.setText("正在启动本地代理...")
        worker = ProxyStartWorker(streams, platform)
        worker.ready.connect(self._on_proxy_ready)
        worker.failed.connect(lambda msg: self.status_text.setText(f"代理启动失败：{msg}"))
        self._proxy_worker = worker
        worker.start()

    def _on_proxy_ready(self, proxy_map: dict):
        """代理就绪：更新对应流卡片的 OBS 按钮。"""
        for url, proxy_url in proxy_map.items():
            card = self._card_by_url.get(url)
            if card is not None:
                card.set_obs_ready(proxy_url)
        self.status_text.setText(f"本地代理已就绪（{len(proxy_map)} 条流）")

    def _on_fetch_error(self, msg: str):
        self._show_placeholder()
        self.result_hint.setText("解析失败")
        self.status_text.setText("解析失败")
        QMessageBox.critical(self, "解析失败", msg)

    def _on_fetch_finished(self):
        self.set_loading(False)
        self._fetch_worker = None

    # ═══════════════════════════════════════════════════
    # 登录态检测
    # ═══════════════════════════════════════════════════
    def _start_login_checks(self):
        for key, meta in PLATFORM_META.items():
            if not meta.get("login"):
                continue
            worker = LoginCheckWorker(key)
            worker.statusReady.connect(self._on_login_status)
            self._login_workers.append(worker)
            worker.start()

    def _on_login_status(self, key: str, online: bool, expired: bool):
        if key in self._platform_cards:
            self._platform_cards[key].set_status(online, expired)

    def _on_copy_all(self):
        if not self._all_streams:
            self.status_text.setText("暂无流可复制")
            return
        from PySide6.QtWidgets import QApplication
        all_urls = "\n".join(s.get("url", "") for s in self._all_streams)
        QApplication.clipboard().setText(all_urls)
        self.status_text.setText(f"已复制全部 {len(self._all_streams)} 条流链接")

    def _on_transcode_clicked(self):
        dlg = TranscodeDialog(parent=self)
        dlg.exec()

    def _on_proxy_clicked(self):
        # 代理设置：显示当前代理状态（简化提示）
        self.status_text.setText("代理设置：解析淘宝/小红书流后自动启动本地代理")

    def _on_system_proxy(self):
        try:
            from live_stream_fetcher import _is_system_proxy_on, _set_system_proxy, _clear_system_proxy
            if _is_system_proxy_on():
                _clear_system_proxy()
                self.status_text.setText("系统代理已关闭")
            else:
                addr = _set_system_proxy(8080)
                self.status_text.setText(f"系统代理已开启（{addr}）")
        except Exception as e:
            self.status_text.setText(f"系统代理操作失败：{e}")

    def _on_wechat_tool(self):
        try:
            from live_stream_fetcher import _ensure_wechat_video_tool
            exe_path = _ensure_wechat_video_tool()
            if not exe_path:
                self.status_text.setText("视频号工具未找到")
                return
            import subprocess, os
            subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path))
            self.status_text.setText("视频号下载工具已启动")
        except Exception as e:
            self.status_text.setText(f"视频号工具启动失败：{e}")

    # ═══════════════════════════════════════════════════
    # 结果渲染
    # ═══════════════════════════════════════════════════
    # 平台操作指引数据（未解析时的引导卡片）
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

        # 1. 欢迎卡片
        welcome = QFrame()
        welcome.setObjectName("inputCard")
        welcome_lay = QVBoxLayout(welcome)
        welcome_lay.setContentsMargins(0, 0, 0, 0)
        welcome_lay.setSpacing(0)

        gold_bar = QFrame()
        gold_bar.setFixedHeight(3)
        gold_bar.setStyleSheet("background: #FFD700; border-radius: 2px;")
        welcome_lay.addWidget(gold_bar)

        body = QVBoxLayout()
        body.setContentsMargins(20, 14, 20, 16)
        body.setSpacing(4)

        t = QLabel("🎬  欢迎使用影视匠直播流获取工具")
        t.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {Colors.GOLD_PRIMARY}; background: transparent;")
        body.addWidget(t)

        d = QLabel("一键解析抖音 / 快手 / 小红书 / 淘宝 / YY / 视频号 的直播推流地址")
        d.setStyleSheet(f"font-size: 11px; color: {Colors.TEXT_MUTED}; background: transparent;")
        body.addWidget(d)

        hint = QLabel("💡  提示：选择平台卡片可查看登录状态，点击「获取流链接」开始解析")
        hint.setStyleSheet(f"font-size: 10px; color: {Colors.GOLD_DARK}; background: transparent;")
        body.addWidget(hint)
        welcome_lay.addLayout(body)
        self.result_layout.addWidget(welcome)

        # 2. 使用教程（可折叠）
        self._guide_collapsed = getattr(self, "_guide_collapsed", False)
        guide_header = QPushButton(
            ("▶ 使用教程 · 按平台查看操作指引" if self._guide_collapsed
             else "▼ 使用教程 · 按平台查看操作指引")
        )
        guide_header.setObjectName("ghostBtn")
        guide_header.setCursor(Qt.CursorShape.PointingHandCursor)
        guide_header.clicked.connect(self._toggle_guide)
        self.result_layout.addWidget(guide_header)

        # 教程内容容器
        self._guide_container = QWidget()
        self._guide_container_lay = QVBoxLayout(self._guide_container)
        self._guide_container_lay.setContentsMargins(0, 0, 0, 0)
        self._guide_container_lay.setSpacing(8)
        if not self._guide_collapsed:
            self._build_guide_content()
            self.result_layout.addWidget(self._guide_container)

        self.result_layout.addStretch(1)

    def _toggle_guide(self):
        self._guide_collapsed = not self._guide_collapsed
        # 简化：重新渲染占位符
        self._show_placeholder()

    def _build_guide_content(self):
        for name, color, tips, footer in self._GUIDE_DATA:
            card = QFrame()
            card.setObjectName("inputCard")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(4)

            # 标题
            title = QLabel(f"{name} · 操作指引")
            title.setStyleSheet(
                f"font-size: 11px; font-weight: bold; color: {color};"
                f"background: transparent; padding: 8px 14px 4px 14px;"
            )
            lay.addWidget(title)

            # 步骤
            for idx, tip in enumerate(tips, 1):
                row = QLabel(f"{idx}. {tip}")
                row.setWordWrap(True)
                row.setStyleSheet(
                    f"font-size: 10px; color: {Colors.TEXT_SECONDARY};"
                    f"background: transparent; padding: 0 20px;"
                )
                lay.addWidget(row)

            # 底部提示
            foot = QLabel(f"💡 {footer}")
            foot.setWordWrap(True)
            foot.setStyleSheet(
                f"font-size: 9px; color: {Colors.TEXT_MUTED};"
                f"background: transparent; padding: 4px 20px 10px 20px;"
            )
            lay.addWidget(foot)

            self._guide_container_lay.addWidget(card)

    def _clear_result_cards(self):
        # 清空结果容器
        while self.result_layout.count():
            item = self.result_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._stream_cards = []
        self._card_by_url = {}

    def render_streams(self, streams: list, platform: str = ""):
        """渲染流列表（由解析线程回传结果后调用）。"""
        self._all_streams = streams
        self._result_platform = platform

        # 重置筛选状态
        self._filter_dimension = "quality"
        self._filter_val = "全部"
        self.dim_quality_btn.setChecked(True)
        self.dim_format_btn.setChecked(False)

        if not streams:
            self._clear_result_cards()
            self._show_placeholder()
            self.result_hint.setText("未解析到流")
            return

        self.result_hint.setText(f"共 {len(streams)} 条流 · {platform}")

        # 构建筛选标签 + 渲染流卡片
        self._build_filter_tags(streams)
        self._render_stream_cards(streams)

    def _render_stream_cards(self, streams: list):
        """渲染流卡片列表（不含筛选标签重建）。"""
        self._clear_result_cards()
        for i, s in enumerate(streams):
            card = StreamCard(s, i, self._result_platform)
            card.copyClicked.connect(self._copy_url)
            card.urlClicked.connect(self._copy_url)
            card.obsClicked.connect(self._copy_url)
            card.transcodeClicked.connect(self._on_transcode_stream)
            self.result_layout.addWidget(card)
            self._stream_cards.append(card)
            self._card_by_url[s.get("url", "")] = card

    # ═══════════════════════════════════════════════════
    # 筛选标签
    # ═══════════════════════════════════════════════════
    def _build_filter_tags(self, streams: list):
        """根据当前维度构建分类统计标签。"""
        # 清空旧标签
        while self.filter_tags_layout.count():
            item = self.filter_tags_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        counts = {}
        for s in streams:
            val = s.get(self._filter_dimension, "").strip() or "其他"
            counts[val] = counts.get(val, 0) + 1

        sorted_items = sorted(counts.items(), key=lambda x: -x[1])

        for tag_name, count in sorted_items:
            chip = QPushButton(f"{tag_name} ({count})")
            chip.setObjectName("filterChip")
            chip.setCheckable(True)
            chip.setChecked(self._filter_val == tag_name)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda checked, t=tag_name: self._on_filter_tag_click(t))
            self.filter_tags_layout.addWidget(chip)

    def _switch_filter_dimension(self, dimension: str):
        self._filter_dimension = dimension
        self._filter_val = "全部"
        self.dim_quality_btn.setChecked(dimension == "quality")
        self.dim_format_btn.setChecked(dimension == "format")
        self._build_filter_tags(self._all_streams)
        self._render_filtered_streams()

    def _on_filter_tag_click(self, tag_name: str):
        self._filter_val = tag_name if self._filter_val != tag_name else "全部"
        self._build_filter_tags(self._all_streams)
        self._render_filtered_streams()

    def _render_filtered_streams(self):
        """根据当前筛选条件重新渲染流卡片。"""
        if not self._all_streams:
            return
        streams = self._all_streams
        if self._filter_val and self._filter_val != "全部":
            streams = [
                s for s in streams
                if s.get(self._filter_dimension, "").strip() == self._filter_val
            ]
        self.result_hint.setText(f"共 {len(streams)} 条流 · {self._result_platform}")
        self._render_stream_cards(streams)

    def _copy_url(self, url: str):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(url)
        self.status_text.setText(f"已复制：{url[:60]}...")

    def _on_transcode_stream(self, url: str):
        dlg = TranscodeDialog(preset_url=url, parent=self)
        dlg.exec()

    # ═══════════════════════════════════════════════════
    # 状态更新
    # ═══════════════════════════════════════════════════
    def set_loading(self, loading: bool):
        if loading:
            self.status_text.setText("⏳ 正在解析直播流，请稍候...")
        else:
            self.status_text.setText("就绪")

    def set_login_status(self, platform: str, online: bool, expired: bool = False):
        """更新侧边/状态栏登录状态。"""
        if platform in self._platform_cards:
            self._platform_cards[platform].set_status(online, expired)
