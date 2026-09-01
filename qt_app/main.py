# -*- coding: utf-8 -*-
"""main.py — PySide6 版入口

用法：
    python -m qt_app.main              # 正常启动
    python -m qt_app.main --demo       # 演示模式（mock 数据展示 UI 效果）
"""

import sys
import os

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from .main_window import MainWindow
from .password_gate import PasswordGate


def _load_stylesheet(app: QApplication) -> None:
    """加载 styles.qss，并合并高 DPI 基础设置。"""
    qss_path = os.path.join(os.path.dirname(__file__), "styles.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())


def _inject_demo_streams(window: MainWindow) -> None:
    """演示模式：注入 mock 流数据，用于预览 UI 视觉效果。"""
    demo = [
        {"url": "https://live-source-play.xhscdn.com/live/stream_1080p_hevc.m3u8?token=abc123",
         "quality": "1080p HEVC", "quality_tag": "OR4", "format": "m3u8", "source": "edith"},
        {"url": "https://pull-flv-l1.douyincdn.com/live/stream_720p.flv?expire=123",
         "quality": "720p 高清", "quality_tag": "HD", "format": "flv", "source": "douyin_api"},
        {"url": "https://alivc-live.taobao.com/live/stream_sd.flv?auth_key=xyz",
         "quality": "标清", "quality_tag": "SD", "format": "flv", "source": "mtop"},
        {"url": "https://live-source-play-bak-tx.xhscdn.com/live/stream_1080p_h264.m3u8",
         "quality": "原画", "quality_tag": "OR4", "format": "hls", "source": "edith"},
    ]
    window.render_streams(demo, platform="小红书")


def main():
    # 高分屏适配
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("直播流获取工具")
    app.setOrganizationName("LONGSHAO")

    _load_stylesheet(app)

    # 密码验证（--skip-password 可跳过，供开发调试）
    # 创建主窗口
    window = MainWindow()

    # 演示模式：注入 mock 数据预览结果区
    if "--demo" in sys.argv:
        _inject_demo_streams(window)

    # 密码验证（--skip-password 跳过，供开发调试）
    if "--skip-password" in sys.argv:
        window.show()
    else:
        gate = PasswordGate()
        gate.verified.connect(window.show)
        gate.exec()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
