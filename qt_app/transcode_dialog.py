# -*- coding: utf-8 -*-
"""transcode_dialog.py — HEVC → H.264 转码对话框（Qt 版）

对齐原 Tkinter 版 _open_transcode_dialog 逻辑：
  URL + 端口输入 → LocalStreamProxy(port, "通用", codec_hint="hevc") → 输出本地代理地址
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QApplication, QFrame,
)

from .theme import Colors
from .controller import TranscodeWorker


class TranscodeDialog(QDialog):
    """HEVC 转码工具对话框。"""

    def __init__(self, preset_url: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("HEVC → H.264 转码工具")
        self.setFixedSize(700, 460)
        self.setModal(True)

        self._worker = None

        self._build_ui()

        if preset_url:
            self.url_input.setText(preset_url)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        # 标题
        title = QLabel("HEVC / H.265 → H.264 转码")
        title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: #a78bfa; background: transparent;")
        lay.addWidget(title)

        sub = QLabel("输入 HEVC 流链接，转码为 H.264（OBS/VLC 可直接播放）")
        sub.setStyleSheet(f"font-size: 11px; color: {Colors.TEXT_MUTED}; background: transparent;")
        lay.addWidget(sub)

        # URL 输入
        lay.addWidget(self._card_label("HEVC 流链接："))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://.../stream.m3u8")
        self.url_input.setStyleSheet(self._input_style())
        lay.addWidget(self.url_input)

        # 端口
        port_row = QHBoxLayout()
        port_label = QLabel("本地代理端口：")
        port_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent; font-size: 12px;")
        port_row.addWidget(port_label)

        self.port_input = QLineEdit("19876")
        self.port_input.setFixedWidth(90)
        self.port_input.setStyleSheet(self._input_style())
        port_row.addWidget(self.port_input)

        hint = QLabel("转码后访问地址：http://127.0.0.1:<端口>/live")
        hint.setStyleSheet(f"color: {Colors.TEXT_MUTED}; background: transparent; font-size: 11px;")
        port_row.addWidget(hint)
        port_row.addStretch(1)
        lay.addLayout(port_row)

        # 状态
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet(
            f"color: {Colors.TEXT_MUTED}; background: {Colors.BG_CARD};"
            f"border-radius: 8px; padding: 8px 12px; font-size: 12px;"
        )
        lay.addWidget(self.status_label)

        # 结果
        lay.addWidget(self._card_label("转码代理地址（启动后复制到 OBS）："))
        self.result_input = QLineEdit()
        self.result_input.setReadOnly(True)
        self.result_input.setPlaceholderText("启动后显示本地代理地址")
        self.result_input.setStyleSheet(
            self._input_style() + f"color: #a78bfa;"
        )
        lay.addWidget(self.result_input)

        lay.addStretch(1)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.start_btn = QPushButton("启动转码代理")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setStyleSheet(
            "background: #8b5cf6; color: #fff; border: none; border-radius: 8px;"
            "padding: 8px 16px; font-weight: bold;"
        )
        self.start_btn.clicked.connect(self._start)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(self._ghost_style())
        self.stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self.stop_btn)

        self.copy_btn = QPushButton("复制地址")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setEnabled(False)
        self.copy_btn.setStyleSheet(self._ghost_style())
        self.copy_btn.clicked.connect(self._copy_result)
        btn_row.addWidget(self.copy_btn)

        btn_row.addStretch(1)

        close_btn = QPushButton("关闭")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(self._ghost_style())
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)

        lay.addLayout(btn_row)

    @staticmethod
    def _input_style() -> str:
        return (
            f"QLineEdit {{ background: {Colors.BG_INPUT}; color: {Colors.TEXT_PRIMARY};"
            f"border: 1px solid {Colors.BORDER}; border-radius: 8px;"
            f"padding: 8px 12px; font-family: Consolas; font-size: 12px; }}"
            f"QLineEdit:focus {{ border: 1px solid {Colors.ACCENT_BLUE}; }}"
        )

    @staticmethod
    def _ghost_style() -> str:
        return (
            f"QPushButton {{ background: {Colors.BG_CARD}; color: {Colors.TEXT_SECONDARY};"
            f"border: 1px solid {Colors.BORDER}; border-radius: 8px; padding: 8px 14px; }}"
            f"QPushButton:hover {{ background: {Colors.BG_HOVER}; color: {Colors.TEXT_PRIMARY}; }}"
        )

    def _card_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; background: transparent; font-size: 12px; font-weight: bold;"
        )
        return lbl

    # ── 启动 / 停止 ──
    def _start(self):
        url = self.url_input.text().strip()
        if not url:
            self._set_status("请输入 HEVC 流链接", error=True)
            return
        if not url.startswith("http"):
            self._set_status("链接格式不正确（需要以 http 开头）", error=True)
            return
        try:
            port = int(self.port_input.text().strip())
        except ValueError:
            self._set_status("端口号必须是数字", error=True)
            return

        # 停止旧代理
        self._stop()

        self.start_btn.setEnabled(False)
        self.start_btn.setText("启动中...")
        self._set_status("正在启动转码代理...")
        self.result_input.clear()

        worker = TranscodeWorker(url, port)
        worker.ready.connect(self._on_ready)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_ready(self, local_url: str):
        self.result_input.setText(local_url)
        self._set_status(f"转码代理已启动 → {local_url}", ok=True)
        self.start_btn.setEnabled(True)
        self.start_btn.setText("重新启动")
        self.stop_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)

    def _on_failed(self, msg: str):
        self._set_status(f"启动失败：{msg}", error=True)
        self.start_btn.setEnabled(True)
        self.start_btn.setText("启动转码代理")

    def _stop(self):
        if self._worker is not None:
            self._worker.stop_proxy()
            self._worker = None
        self.result_input.clear()
        self.stop_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)

    def _copy_result(self):
        txt = self.result_input.text()
        if txt:
            QApplication.clipboard().setText(txt)
            self.copy_btn.setText("已复制！")

    def _set_status(self, text: str, error: bool = False, ok: bool = False):
        self.status_label.setText(text)
        color = Colors.ACCENT_RED if error else (Colors.ACCENT_GREEN if ok else Colors.TEXT_MUTED)
        self.status_label.setStyleSheet(
            f"color: {color}; background: {Colors.BG_CARD};"
            f"border-radius: 8px; padding: 8px 12px; font-size: 12px;"
        )

    def closeEvent(self, event):
        self._stop()
        super().closeEvent(event)
