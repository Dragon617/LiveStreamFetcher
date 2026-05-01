# -*- coding: utf-8 -*-
"""
打包脚本：将 live_stream_fetcher.py 打包成单个 EXE
用法：
  python build_exe.py          # 正式版（无控制台窗口）
  python build_exe.py --debug  # 调试版（带控制台窗口，可看 print 输出）
"""
import subprocess
import sys
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(SCRIPT_DIR, "VERSION")
MAIN_SCRIPT = os.path.join(SCRIPT_DIR, "live_stream_fetcher.py")
ICON_FILE = os.path.join(SCRIPT_DIR, "app.ico")


def bump_version():
    """读取版本号，自动递增最后一位，写回文件，返回新版本号。"""
    if not os.path.exists(VERSION_FILE):
        version = "3.1"
    else:
        with open(VERSION_FILE, "r") as f:
            version = f.read().strip()
        # 解析 x.y.z 或 x.y
        parts = version.split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        version = ".".join(parts)
    with open(VERSION_FILE, "w") as f:
        f.write(version + "\n")
    print(f"  版本号: v{version}")
    return version


def patch_version_in_source(version):
    """将版本号写入 live_stream_fetcher.py 的窗口标题中。"""
    with open(MAIN_SCRIPT, "r", encoding="utf-8") as f:
        content = f.read()
    # 替换窗口标题中的版本号
    new_content = re.sub(
        r'LiveStreamFetcher v[\d.]+',
        f'LiveStreamFetcher v{version}',
        content,
    )
    if new_content != content:
        with open(MAIN_SCRIPT, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"  已将版本号写入主程序")
    return new_content != content


def main():
    debug_mode = "--debug" in sys.argv

    version = bump_version()
    patched = patch_version_in_source(version)

    if not os.path.exists(MAIN_SCRIPT):
        print(f"错误：找不到 {MAIN_SCRIPT}")
        sys.exit(1)

    # 嵌入式 Chromium 路径
    chromium_src = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "ms-playwright", "chromium-1208", "chrome-win64"
    )

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name", "LiveStreamFetcher",
        "--clean",
        # 隐式导入 yt-dlp 的子模块（动态加载的）
        "--hidden-import=yt_dlp",
        "--hidden-import=yt_dlp.extractor",
        "--hidden-import=yt_dlp.extractor.common",
        "--hidden-import=yt_dlp.extractor.douyin",
        "--hidden-import=yt_dlp.extractor.kuaishou",
        "--hidden-import=yt_dlp.extractor.xiaohongshu",
        "--hidden-import=yt_dlp.extractor.taobao",
        "--hidden-import=yt_dlp.extractor.wechat",
        "--hidden-import=yt_dlp.extractor.generic",
        "--hidden-import=yt_dlp.extractor.youtube",
        "--hidden-import=yt_dlp.extractor.lazy_extractors",
        "--hidden-import=yt_dlp.postprocessor",
        "--hidden-import=yt_dlp.downloader",
        "--hidden-import=yt_dlp.utils",
        "--hidden-import=yt_dlp.version",
        "--hidden-import=yt_dlp.compat",
        "--hidden-import=yt_dlp.cookies",
        # Playwright 相关（动态导入检测不到）
        "--hidden-import=playwright",
        "--hidden-import=playwright.sync_api",
        "--hidden-import=greenlet",
        "--hidden-import=greenlet._greenlet",
        # 通用库
        "--hidden-import=requests",
        "--hidden-import=requests.adapters",
        "--hidden-import=requests.cookies",
        "--hidden-import=requests.utils",
        "--hidden-import=urllib3",
        "--hidden-import=certifi",
        "--hidden-import=charset_normalizer",
        "--hidden-import=idna",
        "--hidden-import=json",
        "--hidden-import=re",
        "--hidden-import=uuid",
        "--hidden-import=random",
        "--hidden-import=string",
        "--hidden-import=threading",
        "--hidden-import=tkinter",
        "--hidden-import=tkinter.ttk",
        "--hidden-import=tkinter.scrolledtext",
        "--hidden-import=tkinter.messagebox",
        # 收集 yt-dlp 的数据文件（版本信息、extractor 列表等）
        "--collect-data=yt_dlp",
        "--collect-submodules=yt_dlp",
        # certifi 的 CA 证书（HTTPS 必需）
        "--collect-data=certifi",
        # 输出目录
        "--distpath", os.path.join(SCRIPT_DIR, "dist"),
        "--workpath", os.path.join(SCRIPT_DIR, "build"),
        "--specpath", SCRIPT_DIR,
    ]

    # 调试模式：显示控制台窗口（可以看到 print 输出）
    if debug_mode:
        print("  模式: 调试版（带控制台窗口）")
        # 不加 --windowed，默认会显示控制台
    else:
        print("  模式: 正式版（无控制台窗口）")
        cmd.append("--windowed")

    # 嵌入式 Chromium（如果存在）
    if os.path.isdir(chromium_src):
        cmd.append(f"--add-data={chromium_src};embedded_chromium")
        print(f"  嵌入 Chromium: {chromium_src}")
    else:
        print(f"  警告：未找到嵌入式 Chromium ({chromium_src})，将依赖系统浏览器")

    # 如果有图标文件就用
    if os.path.exists(ICON_FILE):
        cmd.extend(["--icon", ICON_FILE])

    cmd.append(MAIN_SCRIPT)

    print("=" * 60)
    print(f"  正在打包 LiveStreamFetcher v{version} ...")
    print("=" * 60)
    print(f"  脚本：{MAIN_SCRIPT}")
    print(f"  输出：{os.path.join(SCRIPT_DIR, 'dist', 'LiveStreamFetcher.exe')}")
    print("=" * 60)

    result = subprocess.run(cmd, cwd=SCRIPT_DIR)

    if result.returncode == 0:
        exe_path = os.path.join(SCRIPT_DIR, "dist", "LiveStreamFetcher.exe")
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print()
        print("=" * 60)
        print(f"  打包成功！")
        print(f"  版本：v{version}")
        print(f"  文件：{exe_path}")
        print(f"  大小：{size_mb:.1f} MB")
        print(f"  模式：{'调试版（带控制台）' if debug_mode else '正式版（无控制台）'}")
        print(f"  双击即可运行，无需 Python 环境")
        print("=" * 60)
    else:
        print(f"\n打包失败，返回码：{result.returncode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
