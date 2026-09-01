# -*- coding: utf-8 -*-
"""password_gate.py — 启动密码验证对话框（Qt 版）

对齐原 Tkinter 版 PasswordGate 逻辑：
  1. 窗口创建后后台预取云端密码
  2. 用户输入密码 → 优先用预取密码比对（瞬时返回）
  3. 预取未就绪则网络拉取后比对
  4. 成功发射 verified 信号，失败显示错误
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QFrame,
)
from PySide6.QtGui import QFont

from .theme import Colors
from .controller import PasswordFetchWorker


class PasswordGate(QDialog):
    """密码验证对话框。"""

    verified = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("影视匠直播流获取 — 密码验证")
        self.setFixedSize(420, 340)
        self.setModal(True)

        self._prefetched_pwd = None
        self._prefetch_done = False
        self._loading = False
        self._worker = None

        self._build_ui()

        # 启动后台预取
        self._start_prefetch()

    def _build_ui(self):
        self.setStyleSheet(f"""
            QDialog {{ background: {Colors.BG_DARK}; }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(10)

        # 图标
        icon = QLabel("🔐")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"font-size: 40px; background: transparent;")
        lay.addWidget(icon)

        # 标题
        title = QLabel("请输入访问密码")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {Colors.TEXT_PRIMARY}; background: transparent;"
        )
        lay.addWidget(title)

        sub = QLabel("输入密码后即可使用软件")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(
            f"font-size: 11px; color: {Colors.TEXT_MUTED}; background: transparent;"
        )
        lay.addWidget(sub)

        lay.addSpacing(8)

        # 密码输入框
        self.pwd_input = QLineEdit()
        self.pwd_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pwd_input.setPlaceholderText("请输入密码")
        self.pwd_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pwd_input.setMinimumHeight(42)
        self.pwd_input.setStyleSheet(f"""
            QLineEdit {{
                background: {Colors.BG_INPUT};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 15px;
            }}
            QLineEdit:focus {{
                border: 1px solid {Colors.ACCENT_BLUE};
            }}
        """)
        self.pwd_input.returnPressed.connect(self._verify)
        lay.addWidget(self.pwd_input)

        # 错误提示
        self.error_label = QLabel("")
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_label.setWordWrap(True)
        self.error_label.setMinimumHeight(32)
        self.error_label.setStyleSheet(
            f"font-size: 11px; color: {Colors.ACCENT_RED}; background: transparent;"
        )
        lay.addWidget(self.error_label)

        # 验证按钮
        self.verify_btn = QPushButton("验证")
        self.verify_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.verify_btn.setMinimumHeight(42)
        self.verify_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FFE55C, stop:1 #FFD700);
                color: {Colors.TEXT_DARK};
                border: none;
                border-radius: 10px;
                padding: 10px;
                font-weight: bold;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FFEE88, stop:1 #FFE055);
            }}
            QPushButton:pressed {{ background: #D4A017; }}
        """)
        self.verify_btn.clicked.connect(self._verify)
        lay.addWidget(self.verify_btn)

        # 初始聚焦
        self.pwd_input.setFocus()

    # ── 预取密码 ──
    def _start_prefetch(self):
        worker = PasswordFetchWorker()
        worker.ready.connect(self._on_prefetch_ready)
        self._worker = worker
        worker.start()

    def _on_prefetch_ready(self, pwd: str, diag: str):
        self._prefetch_done = True
        if pwd:
            self._prefetched_pwd = pwd

    # ── 验证 ──
    def _verify(self):
        if self._loading:
            return

        user_input = self.pwd_input.text().strip()
        if not user_input:
            self.error_label.setText("请输入密码")
            self.pwd_input.setFocus()
            return

        # 预取已就绪 → 直接比对
        if self._prefetch_done and self._prefetched_pwd:
            if user_input == self._prefetched_pwd:
                self._on_success()
            else:
                self._on_fail("密码错误，请重新输入")
            return

        # 预取未就绪 → 网络拉取
        self._set_loading(True)
        self.error_label.setText("正在从云端获取密码...")

        worker = PasswordFetchWorker()
        worker.ready.connect(
            lambda pwd, diag: self._on_network_verify(user_input, pwd, diag)
        )
        self._worker = worker
        worker.start()

    def _on_network_verify(self, user_input: str, pwd: str, diag: str):
        self._set_loading(False)
        if not pwd:
            self._on_fail(f"无法获取密码：{diag}")
            return
        if user_input == pwd:
            self._on_success()
        else:
            self._on_fail("密码错误，请重新输入")

    def _on_success(self):
        self._loading = False
        self.verified.emit()
        self.accept()

    def _on_fail(self, msg: str):
        self._loading = False
        self._set_loading(False)
        self.error_label.setText(msg)
        self.pwd_input.clear()
        self.pwd_input.setFocus()

    def _set_loading(self, loading: bool):
        self._loading = loading
        self.verify_btn.setEnabled(not loading)
        self.verify_btn.setText("验证中..." if loading else "验证")
