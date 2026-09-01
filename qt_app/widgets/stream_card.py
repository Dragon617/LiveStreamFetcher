# -*- coding: utf-8 -*-
"""stream_card.py — 单条直播流卡片组件"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGraphicsDropShadowEffect,
)
from PySide6.QtGui import QColor

from ..theme import QUALITY_LEVELS, FORMAT_COLORS


class StreamCard(QFrame):
    """展示单条直播流，含复制 / OBS / 转码操作。"""

    copyClicked = Signal(str)        # 复制 url
    obsClicked = Signal(str)         # 复制代理 url
    transcodeClicked = Signal(str)   # 转码 url
    urlClicked = Signal(str)         # 点击 url 复制

    def __init__(self, stream: dict, index: int, platform: str = "", parent=None):
        super().__init__(parent)
        self.stream = stream
        self.index = index
        self.platform = platform
        self.setObjectName("streamCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)

        self._build()

    def _build(self):
        url = self.stream.get("url", "")
        quality = self.stream.get("quality", "默认")
        fmt = self.stream.get("format", "")
        source = self.stream.get("source", "")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(6)

        # ── 第一行：序号 + 清晰度 + 格式徽章 + 来源 ──
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        idx = QLabel(f"#{self.index + 1}")
        idx.setObjectName("streamIndex")
        hdr.addWidget(idx)

        qual = QLabel(quality)
        qual.setObjectName("streamQuality")
        qual_color = self._quality_color(quality)
        qual.setStyleSheet(f"color: {qual_color}; background: transparent;")
        hdr.addWidget(qual)

        if fmt:
            badge = QLabel(f"  {fmt.upper()}  ")
            badge.setObjectName("fmtBadge")
            badge.setStyleSheet(
                f"background: {self._fmt_color(fmt)}; color: #fff; border-radius: 4px;"
                f"padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
            hdr.addWidget(badge)

        hdr.addStretch(1)

        if source and source != "INITIAL_DATA":
            src = QLabel(f"来源: {source}")
            src.setObjectName("streamIndex")
            hdr.addWidget(src)

        outer.addLayout(hdr)

        # ── 第二行：URL ──
        display = url if len(url) <= 90 else url[:87] + "..."
        url_lbl = QLabel(display)
        url_lbl.setObjectName("streamUrl")
        url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        url_lbl.setToolTip("点击复制完整链接")
        outer.addWidget(url_lbl)

        # HEVC 提示
        is_hevc = any(kw in quality.lower() for kw in ["hevc", "h265", "h.265"])
        if is_hevc:
            hint = QLabel("* 该链接为 HEVC 编码，无法直接在 OBS 使用，请点击「转码」按钮")
            hint.setObjectName("streamHevcHint")
            outer.addWidget(hint)

        # ── 第三行：操作按钮 ──
        btns = QHBoxLayout()
        btns.setSpacing(8)

        cp = QPushButton("复制链接")
        cp.setObjectName("copyBtn")
        cp.setCursor(Qt.CursorShape.PointingHandCursor)
        cp.clicked.connect(lambda: self.copyClicked.emit(url))
        btns.addWidget(cp)

        # OBS 按钮（代理就绪时由外部更新）
        self.obs_btn = QPushButton("OBS")
        self.obs_btn.setObjectName("obsBtn")
        self.obs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.obs_btn.setVisible(False)
        self._proxy_url = None
        self.obs_btn.clicked.connect(self._on_obs_clicked)
        btns.addWidget(self.obs_btn)

        # HLS/M3U8 非 HEVC → 绿色提示
        fmt_lc = fmt.lower()
        if not is_hevc and ("hls" in fmt_lc or "m3u8" in fmt_lc):
            direct = QLabel(" 可直接在 OBS 使用 ")
            direct.setStyleSheet(
                "background: #2ea043; color: #fff; border-radius: 8px;"
                "padding: 5px 12px; font-size: 12px;"
            )
            btns.addWidget(direct)

        btns.addStretch(1)

        # 转码按钮
        trans = QPushButton("HEVC转码" if is_hevc else "转码")
        trans.setObjectName("transcodeBtn")
        trans.setCursor(Qt.CursorShape.PointingHandCursor)
        if is_hevc:
            trans.setStyleSheet(
                "background: #8b5cf6; color: #fff; border: none; border-radius: 8px;"
                "padding: 5px 14px; font-size: 12px; font-weight: bold;"
            )
        trans.clicked.connect(lambda: self.transcodeClicked.emit(url))
        btns.addWidget(trans)

        outer.addLayout(btns)

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
        """代理就绪：显示 OBS 按钮并绑定代理地址。"""
        self._proxy_url = proxy_url
        self.obs_btn.setVisible(True)

    def _on_obs_clicked(self):
        """点击 OBS 按钮：发射代理地址（无代理时回退原始 url）。"""
        self.obsClicked.emit(self._proxy_url or self.stream.get("url", ""))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # 仅当点击的是 URL 区域时触发复制（简化：整卡复制）
            self.urlClicked.emit(self.stream.get("url", ""))
        super().mousePressEvent(event)
