# -*- coding: utf-8 -*-
"""settings_dialog.py — 软件设置对话框（v8.5.0）

当前设置项：
  - 浏览器引擎：本软件内置浏览器（Chrome for Testing，推荐）
               / 电脑自带浏览器（系统 Chrome / Edge）

配置通过业务层 `_get_browser_engine()` / `_set_browser_engine()` 持久化到
缓存根目录 settings.json，平台登录浏览器与解析浏览器共用同一引擎。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QButtonGroup, QFrame,
)

from .theme import Colors


class SettingsDialog(QDialog):
    """软件设置对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setFixedSize(480, 300)
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

        sub = QLabel("选择平台登录浏览器与解析直播流时使用的浏览器：")
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"font-size: 11px; color: {Colors.TEXT_MUTED}; background: transparent;"
        )
        lay.addWidget(sub)

        # 选项卡片
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {Colors.BG_CARD}; border: 1px solid {Colors.BORDER};"
            f"border-radius: 10px; }}"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 14, 18, 14)
        card_lay.setSpacing(10)

        self._radio_builtin = QRadioButton("本软件内置浏览器（推荐）")
        self._radio_builtin.setStyleSheet(self._radio_style())
        self._radio_system = QRadioButton("电脑自带浏览器（系统 Chrome / Edge）")
        self._radio_system.setStyleSheet(self._radio_style())

        self._btn_group = QButtonGroup(self)
        self._btn_group.addButton(self._radio_builtin, 0)
        self._btn_group.addButton(self._radio_system, 1)

        card_lay.addWidget(self._radio_builtin)
        hint1 = QLabel("无需安装任何浏览器，开箱即用，内置完整视频编解码")
        hint1.setStyleSheet(
            f"font-size: 10px; color: {Colors.TEXT_MUTED}; background: transparent;"
            "padding-left: 24px;"
        )
        card_lay.addWidget(hint1)

        card_lay.addWidget(self._radio_system)
        hint2 = QLabel("调用电脑已安装的 Chrome / Edge 执行登录与解析等全部步骤")
        hint2.setStyleSheet(
            f"font-size: 10px; color: {Colors.TEXT_MUTED}; background: transparent;"
            "padding-left: 24px;"
        )
        card_lay.addWidget(hint2)

        lay.addWidget(card)

        # 状态提示
        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(
            f"font-size: 11px; color: {Colors.ACCENT_BLUE}; background: transparent;"
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
    def _radio_style() -> str:
        return f"""
            QRadioButton {{
                color: {Colors.TEXT_PRIMARY};
                font-size: 13px;
                font-weight: bold;
                background: transparent;
                spacing: 8px;
            }}
            QRadioButton::indicator {{
                width: 16px; height: 16px;
            }}
        """

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

    def _load_current(self):
        """读取当前引擎设置并选中对应单选项。"""
        engine = "builtin"
        try:
            from live_stream_fetcher import _get_browser_engine
            engine = _get_browser_engine()
        except Exception:
            pass
        if engine == "system":
            self._radio_system.setChecked(True)
        else:
            self._radio_builtin.setChecked(True)

    def _on_save(self):
        engine = "system" if self._radio_system.isChecked() else "builtin"
        try:
            from live_stream_fetcher import _set_browser_engine
            ok = _set_browser_engine(engine)
        except Exception as e:
            self.hint_label.setText(f"保存失败：{e}")
            return
        if ok:
            self.hint_label.setText("已保存，下次打开浏览器时生效")
            self.accept()
        else:
            self.hint_label.setText("保存失败：无法写入配置文件")
