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
        {"url": "https://live-source-play.xhscdn.com/live/stream_fullhd1.flv?token=abc123",
         "quality": "FULL_HD1", "quality_tag": "OR4", "format": "FLV", "source": "edith"},
        {"url": "https://pull-flv-l1.douyincdn.com/live/stream_hd1.flv?expire=123",
         "quality": "HD1", "quality_tag": "HD", "format": "FLV", "source": "douyin_api"},
        {"url": "https://alivc-live.taobao.com/live/stream_sd1.flv?auth_key=xyz",
         "quality": "SD1", "quality_tag": "SD", "format": "FLV", "source": "mtop"},
        {"url": "https://live-source-play-bak-tx.xhscdn.com/live/stream_sd2.m3u8",
         "quality": "SD2", "quality_tag": "SD", "format": "m3u8", "source": "edith"},
    ]
    window.render_streams(demo, platform="小红书")


def _guard_console_encoding() -> None:
    """v8.5.8: stdout/stderr 编码兜底——重配置为 UTF-8 + errors=replace。

    实证：GBK 控制台/重定向下 print 含 emoji（✅❌⚠️）的日志会抛
    UnicodeEncodeError，曾在证书安装成功路径上崩溃并阻断视频号工具启动
    （'gbk' codec can't encode character '\\u274c'）。
    窗口版 EXE 无控制台时 stdout 为 None，reconfigure 不存在则跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _guard_console_encoding()

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

    # 双保险：EXE 退出时主动关闭所有 Playwright 浏览器 context，
    # 避免 Chromium 进程持有 PyInstaller 临时目录文件锁导致清理失败。
    try:
        from live_stream_fetcher import _cleanup_all_playwright_contexts
        app.aboutToQuit.connect(_cleanup_all_playwright_contexts)
    except Exception:
        pass  # 业务层未加载时跳过（不影响 dev 模式）

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
