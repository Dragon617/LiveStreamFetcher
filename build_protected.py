# -*- coding: utf-8 -*-
"""
LiveStreamFetcher 防破解打包脚本（精准混淆版）
==============================================
策略：只对核心商业逻辑函数做 AST 混淆，其余代码原样保留。
原始文件完全不动，混淆后的代码输出到 build_protected/ 目录。

防护层级：
1. 核心函数参数名+局部变量混淆
2. 全局注释/文档字符串清理
3. 反调试注入（调试器/VM/完整性校验）
4. PyInstaller 打包（optimize=2 + strip + UPX + console禁用）
"""

import os
import sys
import ast
import shutil
import subprocess

# ══════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_FILE = os.path.join(PROJECT_DIR, "live_stream_fetcher.py")
BUILD_DIR = os.path.join(PROJECT_DIR, "build_protected")

PYTHON = r"C:\Python314\python.exe"

# ─── 核心函数白名单：只混淆这些函数/类的内部实现 ─────────
# 包含密码验证、平台解析核心逻辑、本地代理等商业敏感代码
CORE_FUNCTIONS = {
    # 密码验证
    "_fetch_password_from_doc",
    "PasswordGate",
    # 快手核心
    "_ks_fetch_via_playwright",
    "fetch_kuaishou",
    # 抖音核心
    "fetch_douyin",
    # 小红书核心
    "_xhs_fetch_via_playwright",
    "_xhs_try_extract_streams",
    "_xhs_parse_pull_config",
    "fetch_xiaohongshu",
    # 淘宝核心
    "_tb_fetch_via_playwright",
    "fetch_taobao_live",
    # YY直播核心
    "_yy_fetch_via_playwright",
    "fetch_yy_live",
    # 本地代理（商业逻辑）
    "LocalStreamProxy",
    "_StreamProxyHTTPServer",
    # 统一入口
    "extract_streams",
    # 启动流程
    "main",
}

# ─── 核心类内部的敏感方法 ──────────────────────────────────
# 格式: "ClassName.method_name"
CORE_METHODS = {
    "LocalStreamProxy._run_proxy",
    "LocalStreamProxy._handle_client",
    "LocalStreamProxy._do_forward",
    "LocalStreamProxy._read_request_line",
    "LocalStreamProxy._find_stream_url",
    "_StreamProxyHTTPServer.__init__",
    "_StreamProxyHTTPServer.do_GET",
    "PasswordGate._build_ui",
    "PasswordGate._verify",
    "LiveStreamFetcherApp._on_proxy_ready",
    # UI 回调方法（按钮绑定，不能被混淆破坏）
    "LiveStreamFetcherApp._on_fetch",
    "LiveStreamFetcherApp._on_copy_all",
    "LiveStreamFetcherApp._on_toggle_system_proxy",
    # 核心业务实例方法（_on_fetch 链路调用）
    "LiveStreamFetcherApp._do_fetch",
    "LiveStreamFetcherApp._show_result",
    "LiveStreamFetcherApp._render_filtered_streams",
    "LiveStreamFetcherApp._render_stream_card",
    "LiveStreamFetcherApp._copy_single_url",
    "LiveStreamFetcherApp._refresh_all_login_status",
    # 分类筛选方法
    "LiveStreamFetcherApp._build_and_render_filter_tags",
    "LiveStreamFetcherApp._switch_filter_dimension",
    "LiveStreamFetcherApp._on_filter_tag_click",
    # OBS/HEVC 辅助方法
    "LiveStreamFetcherApp._copy_obs_url",
    "LiveStreamFetcherApp._open_transcode_dialog",
}


# ══════════════════════════════════════════════════════════════
# 第一步：代码清理 — 移除文档字符串/注释
# ══════════════════════════════════════════════════════════════

class CodeCleaner(ast.NodeTransformer):
    """AST 清理器：移除文档字符串，保护代码结构"""

    def _strip_docstring(self, node):
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body.pop(0)

    def visit_FunctionDef(self, node):
        self._strip_docstring(node)
        self.generic_visit(node)
        return node

    def visit_AsyncFunctionDef(self, node):
        self._strip_docstring(node)
        self.generic_visit(node)
        return node

    def visit_ClassDef(self, node):
        self._strip_docstring(node)
        self.generic_visit(node)
        return node

    def visit_Module(self, node):
        self._strip_docstring(node)
        self.generic_visit(node)
        return node


# ══════════════════════════════════════════════════════════════
# 第二步：精准混淆 — 只混淆白名单内的函数/类
# ══════════════════════════════════════════════════════════════

class TargetedObfuscator(ast.NodeTransformer):
    """精准混淆器：只对 CORE_FUNCTIONS / CORE_METHODS 中的函数做参数名混淆。

    策略：
    - 遇到函数/类定义时，检查名称是否在白名单中
    - 在白名单中的：混淆函数参数 + 函数体内同名局部变量
    - 不在白名单中的：原样保留
    - 类定义：如果类名在白名单中，混淆所有方法
    """

    def __init__(self):
        self.total_renames = 0

    def _new_name(self):
        self.total_renames += 1
        chars = "lI1O0"
        return "_" + chars[self.total_renames % len(chars)] + f"{self.total_renames:x}"

    @staticmethod
    def _is_protected_arg(name):
        """判断参数名是否应该保护（不混淆）"""
        if not name:
            return True
        if name.startswith('__') and name.endswith('__'):
            return True
        if name in ('self', 'cls', 'root', 'event', 'e', 'master',
                     'parent', 'frame', 'canvas', 'widget', 'msg', 'title',
                     'args', 'kwargs'):
            return True
        if name.startswith('_'):
            return True
        if name in ('str', 'int', 'float', 'bool', 'list', 'dict', 'set', 'tuple',
                     'None', 'bytes', 'object', 'type', 'Any', 'Optional', 'Union',
                     'Callable', 'Iterable', 'List', 'Dict', 'Set', 'Tuple'):
            return True
        return False

    def _obfuscate_function(self, node):
        """对单个函数做参数名+局部变量混淆"""
        arg_names_map = {}
        for arg in node.args.args:
            original = arg.arg
            if not self._is_protected_arg(original):
                new_name = self._new_name()
                arg_names_map[original] = new_name
                arg.arg = new_name

        # 在函数体内同步替换
        if arg_names_map:
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id in arg_names_map:
                    child.id = arg_names_map[child.id]

        return node

    def visit_FunctionDef(self, node):
        """顶层函数：只混淆白名单中的"""
        if node.name in CORE_FUNCTIONS:
            self._obfuscate_function(node)
            print(f"      [混淆] 函数 {node.name}")
        self.generic_visit(node)
        return node

    def visit_AsyncFunctionDef(self, node):
        if node.name in CORE_FUNCTIONS:
            self._obfuscate_function(node)
            print(f"      [混淆] 异步函数 {node.name}")
        self.generic_visit(node)
        return node

    def visit_ClassDef(self, node):
        """类定义：如果类名在白名单中，混淆所有方法"""
        if node.name in CORE_FUNCTIONS:
            print(f"      [混淆] 类 {node.name} 的所有方法")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    full_name = f"{node.name}.{item.name}"
                    # 核心方法始终混淆
                    if item.name in CORE_METHODS or node.name + "." + item.name in CORE_METHODS:
                        self._obfuscate_function(item)
                        print(f"             方法 {item.name}")
                    # 特殊：LiveStreamFetcherApp 只混淆白名单方法
                    elif node.name == "LiveStreamFetcherApp":
                        if f"LiveStreamFetcherApp.{item.name}" in CORE_METHODS:
                            self._obfuscate_function(item)
                            print(f"             方法 {item.name}")
                    # 其他核心类的所有方法都混淆
                    else:
                        self._obfuscate_function(item)
                        print(f"             方法 {item.name}")
        self.generic_visit(node)
        return node


# ══════════════════════════════════════════════════════════════
# 第三步：反调试代码注入
# ══════════════════════════════════════════════════════════════

ANTI_DEBUG_CODE = '''
import ctypes, ctypes.wintypes

def _anti_debug_check():
    """多层级反调试检测"""
    if ctypes.windll.kernel32.IsDebuggerPresent():
        ctypes.windll.kernel32.ExitProcess(0xC0000005)
    try:
        class _PBI(ctypes.Structure):
            _fields_ = [
                ("ExitStatus", ctypes.c_ulong),
                ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_ulong),
                ("BasePriority", ctypes.c_long),
                ("UniqueProcessId", ctypes.c_ulong),
                ("InheritedFromUniqueProcessId", ctypes.c_ulong),
            ]
        _pbi = _PBI()
        _hnd = ctypes.windll.kernel32.GetCurrentProcess()
        _st = ctypes.windll.ntdll.NtQueryInformationProcess(
            _hnd, 0x0, ctypes.byref(_pbi), ctypes.sizeof(_pbi), None)
        if _st == 0 and _pbi.PebBaseAddress:
            _peb = ctypes.cast(_pbi.PebBaseAddress, ctypes.POINTER(ctypes.c_char * 0x1000))
            if _peb.contents[0x02] != 0:
                ctypes.windll.kernel32.ExitProcess(0xC0000005)
    except Exception:
        pass
    _dbg = ["x64dbg.exe", "x32dbg.exe", "ollydbg.exe", "ida.exe", "ida64.exe",
            "idaq.exe", "idaq64.exe", "windbg.exe", "devenv.exe", "ProcessHacker.exe"]
    _snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if _snap != -1:
        class _PE(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                        ("th32ProcessID", ctypes.c_ulong), ("th32DefaultHeapID", ctypes.c_void_p),
                        ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                        ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_char * 260)]
        _pe = _PE()
        _pe.dwSize = ctypes.sizeof(_PE)
        if ctypes.windll.kernel32.Process32First(_snap, ctypes.byref(_pe)):
            while True:
                _n = _pe.szExeFile.decode("utf-8", errors="ignore").lower()
                for _d in _dbg:
                    if _d.lower() in _n:
                        ctypes.windll.kernel32.CloseHandle(_snap)
                        ctypes.windll.kernel32.ExitProcess(0xC0000005)
                if not ctypes.windll.kernel32.Process32Next(_snap, ctypes.byref(_pe)):
                    break
        ctypes.windll.kernel32.CloseHandle(_snap)

def _check_integrity():
    """运行时完整性校验"""
    import importlib.util
    _self = importlib.util.find_spec("live_stream_fetcher")
    if _self and _self.origin:
        try:
            with open(_self.origin, "rb") as _f:
                _data = _f.read()
            if len(_data) < 50000:
                ctypes.windll.kernel32.ExitProcess(0xC0000005)
        except Exception:
            pass

threading.Thread(target=lambda: [_anti_debug_check(), _check_integrity(), time.sleep(0.1)], daemon=True).start()
'''

ANTI_DEBUG_IMPORTS = '''
import ctypes
import ctypes.wintypes
import threading
'''


def inject_anti_debug(source_tree):
    """在 import 区域后注入反调试代码"""
    anti_debug_ast = ast.parse(ANTI_DEBUG_IMPORTS + ANTI_DEBUG_CODE)

    last_import_idx = 0
    for i, node in enumerate(source_tree.body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import_idx = i + 1
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        else:
            break

    source_tree.body = (
        source_tree.body[:last_import_idx]
        + anti_debug_ast.body
        + source_tree.body[last_import_idx:]
    )
    ast.fix_missing_locations(source_tree)
    return source_tree


# ══════════════════════════════════════════════════════════════
# 第四步：生成防破解 spec 文件
# ══════════════════════════════════════════════════════════════

PROTECTED_SPEC_TEMPLATE = r'''# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [
    (r'C:\Users\15346\AppData\Local\ms-playwright\chromium-1208\chrome-win64', 'embedded_chromium'),
    (r'C:\ffmpeg\bin', 'embedded_ffmpeg'),
    (r'C:\Users\15346\WorkBuddy\20260408125205\wechatVideoDownload2.6\微信视频号下载工具2.6.exe', 'wechat_video_tool'),
    (r'C:\Users\15346\WorkBuddy\20260408125205\wechatVideoDownload2.6\缓存', 'wechat_video_tool'),
]
hiddenimports = [
    '_threading_local',
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
]
datas += collect_data_files('yt_dlp')
datas += collect_data_files('certifi')
hiddenimports += collect_submodules('yt_dlp')

a = Analysis(
    [r'{build_dir}\live_stream_fetcher.py'],
    pathex=[r'{build_dir}'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=['tkinter.test', 'unittest', 'pydoc', 'doctest', 'pdb',
              'lib2to3', 'pyarmor', 'pyminifier', 'test', 'tests',
              'IPython', 'jupyter', 'notebook', 'pip'],
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
    name='LiveStreamFetcher_v6.3',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 不显示 CMD 窗口（正式版）
    disable_windowed_traceback=True,   # 禁用错误追踪弹窗
    argv_emulation=False,
    target_arch=None,
    icon=r'{project_dir}\app_icon.ico',
    codesign_identity=None,
    entitlements_file=None,
)
'''


# 未加密版 spec 模板（直接用原始源码打包，无混淆/反调试）
PLAIN_SPEC_TEMPLATE = r'''# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [
    (r'C:\Users\15346\AppData\Local\ms-playwright\chromium-1208\chrome-win64', 'embedded_chromium'),
    (r'C:\ffmpeg\bin', 'embedded_ffmpeg'),
    (r'C:\Users\15346\WorkBuddy\20260408125205\wechatVideoDownload2.6\微信视频号下载工具2.6.exe', 'wechat_video_tool'),
    (r'C:\Users\15346\WorkBuddy\20260408125205\wechatVideoDownload2.6\缓存', 'wechat_video_tool'),
]
hiddenimports = [
    '_threading_local',
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
]
datas += collect_data_files('yt_dlp')
datas += collect_data_files('certifi')
hiddenimports += collect_submodules('yt_dlp')

a = Analysis(
    [r'{source_file}'],
    pathex=[r'{project_dir}'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=['tkinter.test', 'unittest', 'pydoc', 'doctest', 'pdb',
              'lib2to3', 'pyarmor', 'pyminifier', 'test', 'tests',
              'IPython', 'jupyter', 'notebook', 'pip'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LiveStreamFetcher_v6.3_plain',
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


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def build_plain():
    """打包未加密版本（直接用源码）"""
    print("\n" + "=" * 60)
    print("  [版本A] 打包未加密版本（原始源码，方便调试）")
    print("=" * 60)

    spec_content = PLAIN_SPEC_TEMPLATE.format(
        source_file=SOURCE_FILE,
        project_dir=PROJECT_DIR,
    )
    plain_spec = os.path.join(BUILD_DIR, "LiveStreamFetcher_plain.spec")
    with open(plain_spec, "w", encoding="utf-8") as f:
        f.write(spec_content)

    cmd = [PYTHON, "-m", "PyInstaller", "--clean", "--noconfirm", plain_spec]
    print(f"      命令: {' '.join(cmd)}")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        cmd, cwd=BUILD_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env
    )

    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", errors="replace") or ""
        print("      未加密版打包失败！")
        for line in stderr_text.split("\n"):
            line = line.strip()
            if line and ("Error" in line or "error" in line):
                print(f"      ERR: {line[:200]}")
        return None

    output_exe = os.path.join(BUILD_DIR, "dist", "LiveStreamFetcher_v6.3_plain.exe")
    if os.path.exists(output_exe):
        size_mb = os.path.getsize(output_exe) / (1024 * 1024)
        print(f"      未加密版打包成功: {size_mb:.1f} MB")
        # 复制到项目 dist 目录
        project_dist = os.path.join(PROJECT_DIR, "dist", "LiveStreamFetcher_v6.3_plain.exe")
        if os.path.exists(project_dist):
            os.remove(project_dist)
        shutil.copy2(output_exe, project_dist)
        print(f"      已复制到: {project_dist}")
        return project_dist
    return None


def main():
    print("=" * 60)
    print("  LiveStreamFetcher 防破解打包工具（精准混淆版 + 双版本输出）")
    print("  策略：只混淆核心商业逻辑函数，其余代码原样保留")
    print("=" * 60)

    # ── 清理构建目录 ──
    if os.path.exists(BUILD_DIR):
        print("\n[1/9] 清理构建目录...")
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR, exist_ok=True)
    print(f"      构建目录: {BUILD_DIR}")

    # ── 读取源码（只读，绝不修改原始文件） ──
    print("\n[2/9] 读取源码...")
    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        source = f.read()
    print(f"      源码大小: {len(source):,} 字符, {source.count(chr(10)):,} 行")
    print(f"      原始文件: {SOURCE_FILE}（不会被修改）")

    # ── 先打未加密版本 ──
    plain_path = build_plain()

    # ── AST 解析 + 清理文档字符串 ──
    print("\n[3/9] AST 解析 & 清理文档字符串...")
    tree = ast.parse(source, filename="live_stream_fetcher.py")
    cleaner = CodeCleaner()
    tree = cleaner.visit(tree)
    ast.fix_missing_locations(tree)
    print("      文档字符串已移除")

    # ── 精准混淆：只混淆白名单函数 ──
    print("\n[4/9] 精准混淆（只混淆核心商业逻辑）...")
    obfuscator = TargetedObfuscator()
    tree = obfuscator.visit(tree)
    ast.fix_missing_locations(tree)
    print(f"      共混淆 {obfuscator.total_renames} 个参数/变量")

    # ── 注入反调试代码 ──
    print("\n[6/9] 注入反调试 + 完整性校验代码...")
    tree = inject_anti_debug(tree)
    print("      反调试: 调试器检测 + 进程扫描 + 完整性校验")

    # ── 生成混淆源码 ──
    print("\n[7/9] 生成混淆源码...")
    obfuscated_source = ast.unparse(tree)
    obfuscated_path = os.path.join(BUILD_DIR, "live_stream_fetcher.py")
    with open(obfuscated_path, "w", encoding="utf-8") as f:
        f.write(obfuscated_source)
    print(f"      混淆源码: {len(obfuscated_source):,} 字符")
    print(f"      输出到: {obfuscated_path}")

    # ── 生成 spec 文件 ──
    print("\n[8/9] 生成加密版 spec 文件...")
    spec_content = PROTECTED_SPEC_TEMPLATE.format(build_dir=BUILD_DIR, project_dir=PROJECT_DIR)
    protected_spec = os.path.join(BUILD_DIR, "LiveStreamFetcher_protected.spec")
    with open(protected_spec, "w", encoding="utf-8") as f:
        f.write(spec_content)
    print(f"      SPEC: {protected_spec}")

    # ── 执行加密版打包 ──
    print("\n[9/9] PyInstaller 打包（防破解模式）...")
    print("      这可能需要几分钟时间，请耐心等待...")
    cmd = [PYTHON, "-m", "PyInstaller", "--clean", "--noconfirm", protected_spec]
    print(f"      命令: {' '.join(cmd)}")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        cmd, cwd=BUILD_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env
    )

    stdout_text = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

    if result.returncode != 0:
        print("\n      加密版打包失败！")
        for line in stderr_text.split("\n"):
            line = line.strip()
            if line and ("Error" in line or "error" in line or "failed" in line.lower()):
                print(f"      ERR: {line[:200]}")

    # ── 检查输出 & 汇总 ──
    print(f"\n{'=' * 60}")
    print("  打包完成汇总:")
    print(f"{'=' * 60}")

    if plain_path and os.path.exists(plain_path):
        size_plain = os.path.getsize(plain_path) / (1024 * 1024)
        print(f"  [A] 未加密版: {plain_path} ({size_plain:.1f} MB)")

    output_exe = os.path.join(BUILD_DIR, "dist", "LiveStreamFetcher_v6.3.exe")
    protected_ok = False
    if os.path.exists(output_exe):
        size_mb = os.path.getsize(output_exe) / (1024 * 1024)
        print(f"  [B] 加密版:   {output_exe} ({size_mb:.1f} MB)")
        # 复制到项目 dist 目录
        project_dist = os.path.join(PROJECT_DIR, "dist", "LiveStreamFetcher_v6.3.exe")
        if os.path.exists(project_dist):
            os.remove(project_dist)
        shutil.copy2(output_exe, project_dist)
        print(f"       已复制到: {project_dist}")
        protected_ok = True

    print(f"\n  防护层级 (加密版):")
    print(f"    [+] 核心函数精准混淆")
    print(f"    [+] 文档字符串清理")
    print(f"    [+] 反调试注入 (IsDebuggerPresent + NtQuery + 进程扫描)")
    print(f"    [+] 完整性校验")
    print(f"    [+] UPX 压缩 + 符号表剥离")
    print(f"    [+] CMD 窗口隐藏 (console=False)")
    print(f"{'=' * 60}")

    return protected_ok


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
