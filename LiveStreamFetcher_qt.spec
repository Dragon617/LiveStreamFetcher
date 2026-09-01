# -*- mode: python ; coding: utf-8 -*-
"""LiveStreamFetcher Qt 版打包配置（PySide6 + 业务层复用）

与旧 Tkinter 版差异：
  - 入口：qt_app/main.py
  - 业务层 live_stream_fetcher 通过动态 import 加载，需显式加入 hiddenimports
  - styles.qss 作为 data 打包到 qt_app/ 目录
  - PySide6 由 PyInstaller 内置 hooks 自动处理
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ── 业务层依赖（与旧版一致） ──
hiddenimports = [
    '_threading_local',
    'live_stream_fetcher',          # ★ 动态 import 的业务层
    'yt_dlp', 'yt_dlp.extractor', 'yt_dlp.extractor.common',
    'yt_dlp.extractor.douyin', 'yt_dlp.extractor.kuaishou',
    'yt_dlp.extractor.xiaohongshu', 'yt_dlp.extractor.taobao',
    'yt_dlp.extractor.generic', 'yt_dlp.extractor.youtube',
    'yt_dlp.extractor.lazy_extractors',
    'yt_dlp.postprocessor', 'yt_dlp.downloader',
    'yt_dlp.utils', 'yt_dlp.version', 'yt_dlp.compat', 'yt_dlp.cookies',
    'playwright', 'playwright.sync_api',
    'greenlet', 'greenlet._greenlet',
    'requests', 'requests.adapters', 'requests.cookies', 'requests.utils',
    'urllib3', 'certifi', 'charset_normalizer', 'idna',
    'winreg',
]

# ── Qt 资源 + 嵌入式资源 ──
datas = [
    ('qt_app/styles.qss', 'qt_app'),   # QSS 样式表，保持相对路径
    # 嵌入式 Chromium（Playwright 浏览器，跨电脑分发不依赖系统浏览器）
    (r'C:\Users\15346\AppData\Local\ms-playwright\chromium-1208\chrome-win64', 'embedded_chromium'),
    # 嵌入式 ffmpeg（HEVC 转码）
    (r'C:\ffmpeg\bin', 'embedded_ffmpeg'),
    # 微信视频号下载工具
    (r'wechatVideoDownload2.6\微信视频号下载工具2.6.exe', 'wechat_video_tool'),
    (r'wechatVideoDownload2.6\缓存', 'wechat_video_tool'),
]

datas += collect_data_files('yt_dlp')
datas += collect_data_files('certifi')
hiddenimports += collect_submodules('yt_dlp')

a = Analysis(
    ['qt_main.py'],               # ★ 顶层入口（qt_app 作为包被 import）
    pathex=['.'],                 # 项目根目录，供 live_stream_fetcher / qt_app import
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6'],   # 排除其他 Qt 绑定，减小体积（tkinter 保留：业务层顶层依赖）
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LiveStreamFetcher_qt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    icon='app_icon.ico',
    codesign_identity=None,
    entitlements_file=None,
)
