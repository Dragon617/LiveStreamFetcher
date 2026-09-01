# -*- coding: utf-8 -*-
"""stream_card.py — 单条直播流卡片（v8.2.8 单行布局）"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QGraphicsDropShadowEffect,
)
from PySide6.QtGui import QColor

from ..theme import QUALITY_LEVELS, FORMAT_COLORS


class StreamCard(QFrame):
    """单行布局：#序号 | 清晰度 | 规格 | 链接 | 复制链接 | 转码 | 来源"""

    copyClicked = Signal(str)
    obsClicked = Signal(str)
    transcodeClicked = Signal(str)
    urlClicked = Signal(str)

    def __init__(self, stream: dict, index: int, platform: str = "", parent=None):
        super().__init__(parent)
        self.stream = stream
        self.index = index
        self.platform = platform
        self.setObjectName("streamCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setOffset(0, 1)
        shadow.setColor(QColor(0, 0, 0, 50))
        self.setGraphicsEffect(shadow)

        self._build()

    def _build(self):
        url = self.stream.get("url", "")
        quality = self.stream.get("quality", "默认")
        fmt = self.stream.get("format", "")
        source = self.stream.get("source", "")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(8)

        # 1. 序号 #1
        idx = QLabel(f"#{self.index + 1}")
        idx.setObjectName("streamIndex")
        outer.addWidget(idx)

        # 2. 清晰度（颜色）
        qual = QLabel(quality)
        qual.setObjectName("streamQuality")
        qual_color = self._quality_color(quality)
        qual.setStyleSheet(f"color: {qual_color}; background: transparent; font-weight: bold;")
        outer.addWidget(qual)

        # 分隔
        outer.addWidget(self._sep())

        # 3. 规格（FLV / M3U8）
        if fmt:
            fmt_lbl = QLabel(fmt.upper())
            fmt_lbl.setStyleSheet(
                f"color: {self._fmt_color(fmt)}; background: transparent; font-weight: bold;"
            )
            outer.addWidget(fmt_lbl)

        # 分隔
        outer.addWidget(self._sep())

        # 4. 链接（弹性宽度，带省略号）
        display = url if len(url) <= 70 else url[:67] + "..."
        url_lbl = QLabel(display)
        url_lbl.setObjectName("streamUrl")
        url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        url_lbl.setToolTip(url)
        outer.addWidget(url_lbl, 1)  # 弹性伸缩

        # 5. 复制链接（蓝色）
        cp = QPushButton("复制链接")
        cp.setObjectName("copyBtn")
        cp.setCursor(Qt.CursorShape.PointingHandCursor)
        cp.clicked.connect(lambda: self.copyClicked.emit(url))
        outer.addWidget(cp)

        # 6. OBS 按钮（代理就绪时显示）
        self.obs_btn = QPushButton("OBS")
        self.obs_btn.setObjectName("obsBtn")
        self.obs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.obs_btn.setVisible(False)
        self._proxy_url = None
        self.obs_btn.clicked.connect(self._on_obs_clicked)
        outer.addWidget(self.obs_btn)

        # 7. 转码（灰色）
        is_hevc = any(kw in quality.lower() for kw in ["hevc", "h265", "h.265"])
        trans = QPushButton("HEVC转码" if is_hevc else "转码")
        trans.setObjectName("transcodeBtn")
        trans.setCursor(Qt.CursorShape.PointingHandCursor)
        if is_hevc:
            trans.setStyleSheet(
                "background: #8b5cf6; color: #fff; border: none; border-radius: 6px;"
                "padding: 3px 10px; font-size: 11px; font-weight: bold;"
            )
        trans.clicked.connect(lambda: self.transcodeClicked.emit(url))
        outer.addWidget(trans)

        # 8. 来源（"FLV 直播流"等）
        if source and source != "INITIAL_DATA":
            src = QLabel(f"来源: {source}")
            src.setStyleSheet("color: #6a7390; background: transparent; font-size: 11px;")
            outer.addWidget(src)

    def _sep(self) -> QLabel:
        sep = QLabel("|")
        sep.setStyleSheet("color: #3a3158; background: transparent;")
        return sep

    def _quality_color(self, quality: str) -> str:
        for code, label, color in QUALITY_LEVELS.values():
            if code.lower() in quality.lower() or label in quality:
                return color
        return "#a78bfa"

    @staticmethod
    def _fmt_color(fmt: str) -> str:
        fmt_lc = fmt.lower()
        for key, color in FORMAT_COLORS.items():
            if key in fmt_lc:
                return color
        return "#6a7390"

    def set_obs_ready(self, proxy_url: str):
        self._proxy_url = proxy_url
        self.obs_btn.setVisible(True)

    def _on_obs_clicked(self):
        self.obsClicked.emit(self._proxy_url or self.stream.get("url", ""))
