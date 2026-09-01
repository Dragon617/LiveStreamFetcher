# -*- coding: utf-8 -*-
"""qt_main.py — 顶层入口（供 PyInstaller 打包使用）

原因：qt_app/main.py 内部使用相对 import（from .xxx），
PyInstaller 直接以它为入口时会因 __package__ 为空而失败。
通过本顶层入口 `from qt_app.main import main`，让 qt_app 作为包被正确加载。
"""

from qt_app.main import main

if __name__ == "__main__":
    main()
