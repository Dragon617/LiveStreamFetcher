# -*- coding: utf-8 -*-
"""截图脚本：加载中文字体 + 离屏渲染 UI 并保存 PNG"""
import sys, os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase

from qt_app.main_window import MainWindow
from qt_app.main import _load_stylesheet, _inject_demo_streams


def _load_chinese_font(app: QApplication) -> str:
    """显式加载中文字体（offscreen 平台不加载系统字体，必须手动 addApplicationFont）。"""
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",     # 微软雅黑
        r"C:\Windows\Fonts\simhei.ttf",   # 黑体
        r"C:\Windows\Fonts\simsun.ttc",   # 宋体
    ]
    for path in candidates:
        if os.path.exists(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0:
                fams = QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    # 设置应用默认字体
                    font = QFont(fams[0])
                    app.setFont(font)
                    return fams[0]
    return ""


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("直播流获取工具")
    fam = _load_chinese_font(app)
    print(f"加载字体: {fam}")
    _load_stylesheet(app)

    window = MainWindow()
    window.show()
    _inject_demo_streams(window)

    app.processEvents()

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_preview.png")
    window.grab().save(out)
    print(f"截图已保存: {out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
