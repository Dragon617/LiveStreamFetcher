# -*- coding: utf-8 -*-
"""settings_dialog.py — 软件设置对话框（v8.5.1）

当前设置项：
  - 浏览器引擎：本软件内置浏览器（Chrome for Testing，推荐）
               / 电脑自带浏览器（= 系统默认浏览器：点平台按钮 os.startfile
                 打开；v8.5.5 起解析直播流也调用默认浏览器 exe，
                 仅 Chrome/Edge 白名单，其余默认浏览器回退 channel 链）

配置通过业务层 `_get_browser_engine()` / `_set_browser_engine()` 持久化到
缓存根目录 settings.json，平台登录浏览器与解析浏览器共用同一引擎。

v8.5.1：
  - 选项改为卡片式大按钮（金色边框 + ✓ 角标高亮选中态），
    解决"选中没选中看不出来"的问题
  - 保存后立即关闭当前共享浏览器，引擎切换即时生效，
    解决"保存后软件跳转的还是内置浏览器"的问题
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)

from .theme import Colors


class _EngineCard(QFrame):
    """可勾选的引擎选项卡片（金色边框 + ✓ 角标表示选中）。"""

    clicked = Signal()

    def __init__(self, title: str, hint: str, parent=None):
        super().__init__(parent)
        self._checked = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(72)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet("background: transparent; border: none;")
        text_col.addWidget(self._title_lbl)
        self._hint_lbl = QLabel(hint)
        self._hint_lbl.setWordWrap(True)
        self._hint_lbl.setStyleSheet(
            f"font-size: 10px; color: {Colors.TEXT_MUTED};"
            "background: transparent; border: none;"
        )
        text_col.addWidget(self._hint_lbl)
        lay.addLayout(text_col, 1)

        self._check_lbl = QLabel("✓")
        self._check_lbl.setFixedWidth(22)
        self._check_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._check_lbl.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(self._check_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self._refresh_style()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool):
        if self._checked == on:
            return
        self._checked = on
        self._refresh_style()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(ev)

    def _refresh_style(self):
        if self._checked:
            self.setStyleSheet(
                f"_EngineCard {{ background: {Colors.BG_CARD_LIGHT};"
                f"border: 2px solid {Colors.GOLD_PRIMARY}; border-radius: 10px; }}"
            )
            self._title_lbl.setStyleSheet(
                f"font-size: 13px; font-weight: bold; color: {Colors.GOLD_PRIMARY};"
                "background: transparent; border: none;"
            )
            self._check_lbl.setStyleSheet(
                f"font-size: 16px; font-weight: bold; color: {Colors.GOLD_PRIMARY};"
                "background: transparent; border: none;"
            )
        else:
            self.setStyleSheet(
                f"_EngineCard {{ background: {Colors.BG_CARD};"
                f"border: 2px solid {Colors.BORDER}; border-radius: 10px; }}"
                f"_EngineCard:hover {{ border-color: {Colors.BORDER_LIGHT};"
                f"background: {Colors.BG_CARD_HOVER}; }}"
            )
            self._title_lbl.setStyleSheet(
                f"font-size: 13px; font-weight: bold; color: {Colors.TEXT_PRIMARY};"
                "background: transparent; border: none;"
            )
            self._check_lbl.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: transparent;"
                "background: transparent; border: none;"
            )


class SettingsDialog(QDialog):
    """软件设置对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setFixedSize(500, 380)
        self.setModal(True)

        self._build_ui()
        self._load_current()

    def _build_ui(self):
        self.setStyleSheet(f"QDialog {{ background: {Colors.BG_DARK}; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        # 标题
        title = QLabel("浏览器引擎")
        title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {Colors.TEXT_PRIMARY};"
            f"background: transparent;"
        )
        lay.addWidget(title)

        sub = QLabel("选择平台登录浏览器与解析直播流时使用的浏览器（点击卡片选择）：")
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"font-size: 11px; color: {Colors.TEXT_MUTED}; background: transparent;"
        )
        lay.addWidget(sub)

        # 引擎选项卡片
        self._card_builtin = _EngineCard(
            "本软件内置浏览器（推荐）",
            "无需安装任何浏览器，开箱即用，内置完整视频编解码",
        )
        self._card_system = _EngineCard(
            "电脑自带浏览器（系统默认浏览器）",
            "点平台按钮与解析直播流均调用系统默认浏览器（登录数据独立保存）",
        )
        self._card_builtin.clicked.connect(lambda: self._select("builtin"))
        self._card_system.clicked.connect(lambda: self._select("system"))
        lay.addWidget(self._card_builtin)
        lay.addWidget(self._card_system)

        # 状态提示
        self.hint_label = QLabel(
            "注意：切换引擎后需要重新登录各平台（不同浏览器的登录数据不互通）"
        )
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(
            f"font-size: 11px; color: {Colors.ACCENT_ORANGE}; background: transparent;"
        )
        lay.addWidget(self.hint_label)

        # ── v8.5.6: 登录状态导入区 ──
        from PySide6.QtWidgets import QFrame
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {Colors.BORDER}; margin: 6px 0;")
        lay.addWidget(divider)

        import_title = QLabel("登录状态导入")
        import_title.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {Colors.TEXT_PRIMARY}; background: transparent;"
        )
        lay.addWidget(import_title)

        import_desc = QLabel(
            "解析直播流使用的是软件独立数据目录，默认浏览器里已登录的直播平台不会自动带入。"
            "点击下方按钮，把默认浏览器中的直播平台登录状态复制到软件数据目录"
            "（会短暂关闭并重新打开默认浏览器；软件内现有登录态将被覆盖，旧数据自动备份）。"
        )
        import_desc.setWordWrap(True)
        import_desc.setStyleSheet(
            f"font-size: 11px; color: {Colors.TEXT_MUTED}; background: transparent;"
        )
        lay.addWidget(import_desc)

        self.import_btn = QPushButton("从默认浏览器导入登录状态")
        self.import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_btn.setMinimumSize(0, 36)
        self.import_btn.setStyleSheet(self._btn_style(Colors.ACCENT_BLUE, "#ffffff"))
        self.import_btn.clicked.connect(self._on_import_login)
        lay.addWidget(self.import_btn)

        lay.addStretch(1)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setMinimumSize(88, 36)
        cancel_btn.setStyleSheet(self._btn_style(Colors.BG_CARD_HOVER, Colors.TEXT_PRIMARY))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setMinimumSize(88, 36)
        save_btn.setStyleSheet(self._btn_style("#FFD700", "#1a1530"))
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        lay.addLayout(btn_row)

    @staticmethod
    def _btn_style(bg: str, fg: str) -> str:
        return f"""
            QPushButton {{
                background: {bg}; color: {fg};
                border: none; border-radius: 8px;
                font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ opacity: 0.85; }}
        """

    def _select(self, engine: str):
        self._card_builtin.setChecked(engine == "builtin")
        self._card_system.setChecked(engine == "system")

    def _selected_engine(self) -> str:
        return "system" if self._card_system.isChecked() else "builtin"

    def _load_current(self):
        """读取当前引擎设置并选中对应卡片。"""
        engine = "builtin"
        try:
            from live_stream_fetcher import _get_browser_engine
            engine = _get_browser_engine()
        except Exception:
            pass
        self._select("system" if engine == "system" else "builtin")

    def _on_save(self):
        engine = self._selected_engine()
        old_engine = "builtin"
        try:
            from live_stream_fetcher import _get_browser_engine, _set_browser_engine
            old_engine = _get_browser_engine()
            ok = _set_browser_engine(engine)
        except Exception as e:
            self.hint_label.setText(f"保存失败：{e}")
            return
        if ok:
            # v8.5.1: 引擎变化时立即关闭当前共享浏览器，让下次打开
            # 直接用新引擎启动（保存即生效）。
            if engine != old_engine:
                try:
                    from live_stream_fetcher import _close_shared_browser_for_fetch
                    _close_shared_browser_for_fetch("engine-switch")
                except Exception:
                    pass
            self.accept()
        else:
            self.hint_label.setText("保存失败：无法写入配置文件")

    def _on_import_login(self):
        """v8.5.6: 从系统默认浏览器导入登录状态（需系统引擎模式）。"""
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import QThread, Signal

        # 仅系统引擎模式有意义：解析用默认浏览器 exe 才能解密导入的 Cookie
        engine = "builtin"
        try:
            from live_stream_fetcher import _get_browser_engine
            engine = _get_browser_engine()
        except Exception:
            pass
        if engine != "system":
            QMessageBox.warning(
                self, "登录状态导入",
                "该功能仅在「电脑自带浏览器（系统默认浏览器）」模式下可用。\n\n"
                "解析直播流需要用默认浏览器打开导入的登录数据才能正常解密；"
                "内置浏览器模式下 Cookie 加密不匹配，导入无效。\n\n"
                "请先选择系统引擎并保存。",
            )
            return

        # 获取默认浏览器名称用于提示
        try:
            from live_stream_fetcher import _find_default_browser_exe
            _exe = _find_default_browser_exe()
        except Exception:
            _exe = ""
        if not _exe:
            QMessageBox.warning(
                self, "登录状态导入",
                "系统默认浏览器不是 Chrome / Edge，无法导入。\n"
                "请先把 Chrome 或 Edge 设为系统默认浏览器。",
            )
            return
        browser_name = "Edge" if "msedge" in _exe.lower() else "Chrome"

        ret = QMessageBox.question(
            self, "确认导入登录状态",
            f"即将从 {browser_name}（系统默认浏览器）导入直播平台登录状态：\n\n"
            f"  1. 自动关闭 {browser_name}（未保存的网页内容请先处理）\n"
            f"  2. 复制其中的登录 Cookie 到软件数据目录\n"
            f"  3. 软件内现有登录态将被覆盖（旧数据自动备份）\n"
            f"  4. 完成后自动重新打开 {browser_name}\n\n"
            f"确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        self.import_btn.setEnabled(False)
        self.import_btn.setText("正在导入（请勿操作）...")

        class _ImportWorker(QThread):
            done = Signal(bool, str)

            def run(self):
                try:
                    from live_stream_fetcher import import_login_from_default_browser
                    ok, msg = import_login_from_default_browser()
                    self.done.emit(ok, msg)
                except Exception as e:
                    self.done.emit(False, f"导入过程出错: {e}")

        def _finish(ok: bool, msg: str):
            self.import_btn.setEnabled(True)
            self.import_btn.setText("从默认浏览器导入登录状态")
            if ok:
                QMessageBox.information(self, "导入成功", msg)
            else:
                QMessageBox.warning(self, "导入失败", msg)

        self._import_worker = _ImportWorker(self)
        self._import_worker.done.connect(_finish)
        self._import_worker.start()
