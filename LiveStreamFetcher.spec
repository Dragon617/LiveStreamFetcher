# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [('C:\\Users\\15346\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64', 'embedded_chromium'), ('C:\\ffmpeg\\bin', 'embedded_ffmpeg')]
hiddenimports = [
    '_threading_local',
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
datas += collect_data_files('yt_dlp')
datas += collect_data_files('certifi')
hiddenimports += collect_submodules('yt_dlp')


a = Analysis(
    ['live_stream_fetcher.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='LiveStreamFetcher_v6.2',
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
