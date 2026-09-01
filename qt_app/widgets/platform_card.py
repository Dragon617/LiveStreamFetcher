# -*- coding: utf-8 -*-
"""platform_card.py — 平台卡片组件（带 hover / 选中态）"""

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QGraphicsDropShadowEffect,
)


class PlatformCard(QFrame):
    """单个直播平台卡片，点击触发 parse 信号。"""

    clicked = Signal(str)          # 发射平台 key（dy/ks/xhs/tb/yy/wechat）
    rightClicked = Signal(str)     # 右键：快捷访问

    def __init__(self, key: str, meta: dict, parent=None):
        super().__init__(parent)
        self.key = key
        self.meta = meta
        self.setObjectName("platformCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", False)
        self.setMinimumSize(QSize(150, 96))

        self._build()

        # 阴影效果
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(shadow)

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)

        # 顶部：图标 + 名称 + 状态点
        top = QHBoxLayout()
        top.setSpacing(8)

        icon = QLabel(self.meta.get("icon", "📦"))
        icon.setObjectName("platformIcon")
        icon.setFixedSize(30, 30)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"font-size: 20px; background: {self._tint(self.meta['color'])};"
            f"border-radius: 8px;"
        )
        top.addWidget(icon)

        name = QLabel(self.meta.get("short", ""))
        name.setObjectName("platformName")
        top.addWidget(name)
        top.addStretch(1)

        self.status_lbl = QLabel("●")
        self.status_lbl.setObjectName("platformStatus")
        self.status_lbl.setToolTip("登录状态")
        top.addWidget(self.status_lbl)

        outer.addLayout(top)

        # 描述
        desc = QLabel(self.meta.get("desc", ""))
        desc.setObjectName("platformDesc")
        desc.setWordWrap(True)
        desc.setMaximumHeight(30)
        outer.addWidget(desc)

        outer.addStretch(1)

    @staticmethod
    def _tint(hex_color: str) -> str:
        """返回半透明品牌色背景。"""
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
        return f"rgba({r}, {g}, {b}, 0.18)"

    def set_selected(self, selected: bool):
        self.setProperty("selected", selected)
        self._repolish()

    def set_status(self, online: bool, expired: bool = False):
        if expired:
            self.status_lbl.setText("●")
            self.status_lbl.setStyleSheet("font-size: 10px; color: #f59e0b; background: transparent;")
            self.status_lbl.setToolTip("登录可能失效")
        elif online:
            self.status_lbl.setText("●")
            self.status_lbl.setStyleSheet("font-size: 10px; color: #10b981; background: transparent;")
            self.status_lbl.setToolTip("已登录")
        else:
            self.status_lbl.setText("●")
            self.status_lbl.setStyleSheet("font-size: 10px; color: #6a7390; background: transparent;")
            self.status_lbl.setToolTip("未登录")

    def _repolish(self):
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        elif event.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit(self.key)
        super().mousePressEvent(event)
