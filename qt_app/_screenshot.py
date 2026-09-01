# -*- coding: utf-8 -*-
"""截图脚本：离屏渲染 UI 并保存 PNG，用于预览视觉效果。"""
import sys, os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from qt_app.main_window import MainWindow
from qt_app.main import _load_stylesheet, _inject_demo_streams


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("直播流获取工具")
    _load_stylesheet(app)

    window = MainWindow()
    window.resize(1120, 820)
    window.show()
    _inject_demo_streams(window)

    # 让布局生效
    app.processEvents()

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_preview.png")
    window.grab().save(out)
    print("截图已保存:", out)
    sys.exit(0)


if __name__ == "__main__":
    main()
