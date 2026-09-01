# -*- coding: utf-8 -*-
"""
build_protected_qt.py — Qt 版防破解打包脚本

复用 build_protected.py 的混淆逻辑（CodeCleaner / TargetedObfuscator / inject_anti_debug），
适配 PySide6 版打包：
  - Python 环境：D:\\Python312（PySide6 不支持 3.14）
  - 入口：qt_main.py（import qt_app 包，qt_app 动态 import 混淆后的 live_stream_fetcher）
  - 混淆对象：业务层 live_stream_fetcher.py（核心商业逻辑，与 UI 解耦）

用法：
    CODEBUDDY_SAFE_DELETE_ENABLED=0 D:\\Python312\\python.exe build_protected_qt.py
"""

import os
import sys
import ast
import shutil
import subprocess

# 复用原混淆逻辑
from build_protected import CodeCleaner, TargetedObfuscator, inject_anti_debug
from build_protected import CORE_FUNCTIONS, CORE_METHODS  # noqa: F401（保留白名单引用）

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_FILE = os.path.join(PROJECT_DIR, "live_stream_fetcher.py")
BUILD_DIR = os.path.join(PROJECT_DIR, "build_protected")

PYTHON = r"D:\Python312\python.exe"


def _read_version() -> str:
    """从 VERSION 文件读取版本号（EXE 命名的唯一数据源）。"""
    p = os.path.join(PROJECT_DIR, "VERSION")
    try:
        with open(p, "r", encoding="utf-8") as f:
            ver = f.read().strip()
            if ver:
                return ver
    except Exception:
        pass
    return "8.3.0"


APP_VERSION = _read_version()   # 例如 "8.3.0"

# ─── Qt 版 spec 模板 ───
QT_SPEC_TEMPLATE = r'''# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [
    (r'qt_app\styles.qss', 'qt_app'),
    (r'{project_dir}\VERSION', '.'),
    (r'{project_dir}\icons\dy_real.png', 'icons'),
    (r'{project_dir}\icons\ks_real.png', 'icons'),
    (r'{project_dir}\icons\tb_real.png', 'icons'),
    (r'{project_dir}\icons\xhs_real.png', 'icons'),
    (r'{project_dir}\icons\yy_real.png', 'icons'),
    (r'C:\Users\15346\AppData\Local\ms-playwright\chromium-1208\chrome-win64', 'embedded_chromium'),
    (r'C:\ffmpeg\bin', 'embedded_ffmpeg'),
    (r'{project_dir}\wechatVideoDownload2.8\微信视频号下载工具2.8.exe', 'wechat_video_tool'),
    (r'{project_dir}\wechatVideoDownload2.8\缓存', 'wechat_video_tool'),
]
hiddenimports = [
    '_threading_local',
    'live_stream_fetcher',
    'yt_dlp', 'yt_dlp.extractor', 'yt_dlp.extractor.common',
    'yt_dlp.extractor.douyin', 'yt_dlp.extractor.kuaishou',
    'yt_dlp.extractor.xiaohongshu', 'yt_dlp.extractor.taobao',
    'yt_dlp.extractor.wechat', 'yt_dlp.extractor.generic',
    'yt_dlp.extractor.youtube', 'yt_dlp.extractor.lazy_extractors',
    'yt_dlp.postprocessor', 'yt_dlp.downloader', 'yt_dlp.utils',
    'yt_dlp.version', 'yt_dlp.compat', 'yt_dlp.cookies',
    'playwright', 'playwright.sync_api',
    'greenlet', 'greenlet._greenlet',
    'requests', 'requests.adapters', 'requests.cookies', 'requests.utils',
    'urllib3', 'certifi', 'charset_normalizer', 'idna',
    'winreg',
    'PIL', 'PIL.Image', 'PIL.ImageTk', 'PIL._tkinter_finder',
    'io',
]
datas += collect_data_files('yt_dlp')
datas += collect_data_files('certifi')
hiddenimports += collect_submodules('yt_dlp')

a = Analysis(
    [r'{build_dir}\qt_main.py'],
    pathex=[r'{build_dir}'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=['tkinter.test', 'unittest', 'pydoc', 'doctest', 'pdb',
              'lib2to3', 'pyarmor', 'pyminifier', 'test', 'tests',
              'IPython', 'jupyter', 'notebook', 'pip',
              'PyQt5', 'PyQt6'],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LiveStreamFetcher_v{app_version}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    icon=r'{project_dir}\app_icon.ico',
    codesign_identity=None,
    entitlements_file=None,
)
'''


def main():
    print("=" * 60)
    print("  LiveStreamFetcher Qt 版防破解打包（PySide6）")
    print("=" * 60)

    # ── 清理构建目录 ──
    if os.path.exists(BUILD_DIR):
        print("\n[1/8] 清理构建目录...")
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR, exist_ok=True)

    # ── 读取源码 ──
    print("\n[2/8] 读取业务层源码...")
    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        source = f.read()
    print(f"      源码: {len(source):,} 字符, {source.count(chr(10)):,} 行")

    # ── AST 解析 + 清理文档字符串 ──
    print("\n[3/8] AST 解析 & 清理文档字符串...")
    tree = ast.parse(source, filename="live_stream_fetcher.py")
    tree = CodeCleaner().visit(tree)
    ast.fix_missing_locations(tree)

    # ── 精准混淆 ──
    print("\n[4/8] 精准混淆（核心商业逻辑）...")
    obfuscator = TargetedObfuscator()
    tree = obfuscator.visit(tree)
    ast.fix_missing_locations(tree)
    print(f"      混淆 {obfuscator.total_renames} 个参数/变量")

    # ── 注入反调试 ──
    print("\n[5/8] 注入反调试 + 完整性校验...")
    tree = inject_anti_debug(tree)

    # ── 生成混淆源码 ──
    print("\n[6/8] 生成混淆源码...")
    obfuscated_source = ast.unparse(tree)
    with open(os.path.join(BUILD_DIR, "live_stream_fetcher.py"), "w", encoding="utf-8") as f:
        f.write(obfuscated_source)
    print(f"      混淆源码: {len(obfuscated_source):,} 字符")

    # ── 复制 qt_app 包 + 入口 ──
    print("\n[7/8] 复制 qt_app 包 + 入口脚本...")
    shutil.copytree(
        os.path.join(PROJECT_DIR, "qt_app"),
        os.path.join(BUILD_DIR, "qt_app"),
        ignore=shutil.ignore_patterns("__pycache__", "_preview*.png"),
    )
    shutil.copy2(os.path.join(PROJECT_DIR, "qt_main.py"), os.path.join(BUILD_DIR, "qt_main.py"))
    print("      已复制 qt_app/ 和 qt_main.py")

    # ── 生成 spec 并打包 ──
    print("\n[8/8] 生成 spec 并执行 PyInstaller 打包...")
    spec_content = QT_SPEC_TEMPLATE.format(build_dir=BUILD_DIR, project_dir=PROJECT_DIR, app_version=APP_VERSION)
    spec_path = os.path.join(BUILD_DIR, "LiveStreamFetcher_qt_protected.spec")
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(spec_content)

    cmd = [PYTHON, "-m", "PyInstaller", "--clean", "--noconfirm", spec_path]
    print(f"      命令: {' '.join(cmd)}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    result = subprocess.run(cmd, cwd=BUILD_DIR, env=env)

    output_exe = os.path.join(BUILD_DIR, "dist", f"LiveStreamFetcher_v{APP_VERSION}.exe")
    if os.path.exists(output_exe):
        size_mb = os.path.getsize(output_exe) / (1024 * 1024)
        print(f"\n  打包成功: {output_exe} ({size_mb:.1f} MB)")
        project_dist = os.path.join(PROJECT_DIR, "dist", f"LiveStreamFetcher_v{APP_VERSION}.exe")
        if os.path.exists(project_dist):
            os.remove(project_dist)
        shutil.copy2(output_exe, project_dist)
        print(f"  已复制到: {project_dist}")
        print("\n  防护层级: 核心函数混淆 + 文档清理 + 反调试 + 完整性校验 + UPX + 符号剥离")
        return True

    print("\n  打包失败！")
    return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
