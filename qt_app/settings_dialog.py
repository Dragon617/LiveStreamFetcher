# -*- coding: utf-8 -*-
"""settings_dialog.py — 软件设置对话框（v8.5.1）

当前设置项：
  - 浏览器引擎：本软件内置浏览器（Chrome for Testing，推荐）
               / 电脑自带浏览器（v8.5.3 起 = 系统默认浏览器，
                 点平台按钮 os.startfile 打开；解析仍用 Chrome/Edge）

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
            "点平台按钮用系统默认浏览器打开；解析直播流仍调用 Chrome / Edge",
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
