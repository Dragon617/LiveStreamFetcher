# -*- coding: utf-8 -*-
"""
v6.0 精准修复脚本（处理已 patch 过的文件中的残留问题）
"""
import re, sys
sys.stdout.reconfigure(encoding='utf-8')

SRC = r"c:\Users\15346\WorkBuddy\20260408125205\live_stream_fetcher.py"

print("读取文件...")
with open(SRC, "r", encoding="utf-8") as f:
    code = f.read()

lines_before = len(code.splitlines())
print(f"当前行数: {lines_before}")

# ──────────────────────────────────────────────────────
# 1. 删除重复的 _set_system_proxy / _clear_system_proxy
#    保留第一次出现，删除后两次
# ──────────────────────────────────────────────────────
print("\n[1] 去重 _set_system_proxy / _clear_system_proxy...")

FUNC_BLOCK = """\ndef _set_system_proxy(port: int) -> str:
    \"\"\"设置 Windows 系统代理（替代原 WeChatChannelsProxy.set_system_proxy）\"\"\"
    import winreg
    proxy_addr = f\"127.0.0.1:{port}\"
    try:
        reg_path = r\"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings\"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, \"ProxyEnable\", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, \"ProxyServer\", 0, winreg.REG_SZ, proxy_addr)
        import ctypes
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
        return proxy_addr
    except Exception as e:
        raise RuntimeError(f\"设置系统代理失败: {e}\")


def _clear_system_proxy() -> None:
    \"\"\"关闭 Windows 系统代理\"\"\"
    import winreg
    try:
        reg_path = r\"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings\"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, \"ProxyEnable\", 0, winreg.REG_DWORD, 0)
        import ctypes
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
    except Exception:
        pass

"""

# 找所有出现位置
positions = []
start = 0
while True:
    idx = code.find(FUNC_BLOCK, start)
    if idx == -1:
        break
    positions.append(idx)
    start = idx + len(FUNC_BLOCK)

print(f"  找到 {len(positions)} 处重复")
if len(positions) > 1:
    # 保留第一处，删除第2、3处
    # 从后往前删（保持索引准确）
    for idx in reversed(positions[1:]):
        code = code[:idx] + code[idx + len(FUNC_BLOCK):]
    print(f"  已删除 {len(positions)-1} 处重复")

# ──────────────────────────────────────────────────────
# 2. 检查并删除 WeChatChannelsProxy class 主体（如果还存在）
# ──────────────────────────────────────────────────────
print("\n[2] 删除 WeChatChannelsProxy class（如存在）...")
if "class WeChatChannelsProxy:" in code:
    # 找到起始注释（用 bytes 匹配更可靠）
    # 视频号注释块：找第一个含"视频号"和"mitmproxy"的注释行
    marker = "\nclass WeChatChannelsProxy:"
    idx_start = code.find("\n# ")
    # 更精确：找到包含"视频号"相关注释的那一行
    for line in code.splitlines():
        if "mitmproxy" in line and ("视频号" in line or "WeChatChannels" in line):
            marker_line = line
            break
    
    # 直接找 class 行前面的最近一个空行
    class_idx = code.find("\nclass WeChatChannelsProxy:")
    # 往前找注释
    pre_idx = code.rfind("\n\n", 0, class_idx)
    # 找结束：WeChatProxyRunningError 类结束后
    end_marker = "\n# 平台解析器注册表"
    alt_end = "\nPLATFORM_FETCHERS"
    end_idx = code.find(end_marker)
    if end_idx == -1:
        end_idx = code.find(alt_end)
    
    if pre_idx != -1 and end_idx != -1:
        deleted = code[pre_idx:end_idx].count('\n')
        code = code[:pre_idx] + "\n" + code[end_idx:]
        print(f"  已删除视频号代码块 ({deleted} 行)")
    else:
        print(f"  警告: 边界未找到 pre={pre_idx}, end={end_idx}")
else:
    print("  WeChatChannelsProxy 不存在，跳过")

# ──────────────────────────────────────────────────────
# 3. 替换剩余 WeChatChannelsProxy.is_system_proxy_on() 
#    和 get_current_proxy_server()
# ──────────────────────────────────────────────────────
print("\n[3] 替换剩余 WeChatChannelsProxy 调用...")

# 注入 _is_system_proxy_on() 和 _get_current_proxy_server() 辅助函数
# 先检查剩余引用
refs = []
for i, line in enumerate(code.splitlines()):
    if "WeChatChannelsProxy" in line:
        refs.append((i+1, line.strip()))
        print(f"  剩余引用 {i+1}: {line.strip()}")

if refs:
    # 注入两个辅助函数（放在 _set_system_proxy 旁边）
    EXTRA_HELPERS = """\

def _is_system_proxy_on() -> bool:
    \"\"\"检测 Windows 系统代理是否已启用\"\"\"
    try:
        import winreg
        reg_path = r"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            return bool(enabled)
    except Exception:
        return False


def _get_current_proxy_server() -> str:
    \"\"\"获取当前 Windows 系统代理服务器地址\"\"\"
    try:
        import winreg
        reg_path = r"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            return server or ""
    except Exception:
        return ""

"""
    # 注入到 _clear_system_proxy 之后
    INSERT_AFTER = "    except Exception:\n        pass\n\n"
    idx_insert = code.find(INSERT_AFTER)
    if idx_insert != -1:
        code = code[:idx_insert + len(INSERT_AFTER)] + EXTRA_HELPERS + code[idx_insert + len(INSERT_AFTER):]
        print("  辅助函数注入完成")
    
    # 替换调用
    code = code.replace("WeChatChannelsProxy.is_system_proxy_on()", "_is_system_proxy_on()")
    code = code.replace("WeChatChannelsProxy.get_current_proxy_server()", "_get_current_proxy_server()")
    
    remaining = code.count("WeChatChannelsProxy")
    print(f"  替换后剩余引用: {remaining}")
    if remaining > 0:
        for i, line in enumerate(code.splitlines()):
            if "WeChatChannelsProxy" in line:
                print(f"    行 {i+1}: {line.strip()}")

# ──────────────────────────────────────────────────────
# 4. 清理 docstring 里的引用文字（无害但不整洁）
# ──────────────────────────────────────────────────────
code = code.replace(
    "（替代原 WeChatChannelsProxy.set_system_proxy）",
    ""
)

# ──────────────────────────────────────────────────────
# 5. 删除视频号指引卡片（wch_guide 相关）
# ──────────────────────────────────────────────────────
print("\n[4] 清理视频号指引卡片...")
if "wch_guide" in code:
    # 找 _enter_wch_wait_mode 方法的引用文本
    count = code.count("wch_guide")
    print(f"  找到 wch_guide 引用: {count}")
    # 这些在视频号 UI 方法里，如果已删除 _enter_wch_wait_mode 就应该不存在
    # 检查是否是孤立引用
    for i, line in enumerate(code.splitlines()):
        if "wch_guide" in line or "_wch_" in line or "wch_btn" in line:
            print(f"  行 {i+1}: {line.strip()[:100]}")
else:
    print("  无 wch_guide 引用")

# ──────────────────────────────────────────────────────
# 6. 语法检查
# ──────────────────────────────────────────────────────
print("\n[5] 语法检查...")
import py_compile, tempfile, os
tmp = SRC + ".tmp_check.py"
with open(tmp, "w", encoding="utf-8") as f:
    f.write(code)
try:
    py_compile.compile(tmp, doraise=True)
    print("  语法 OK")
except py_compile.PyCompileError as e:
    print(f"  语法错误: {e}")
finally:
    os.remove(tmp)
    if os.path.exists(tmp + "c"):
        os.remove(tmp + "c")

# ──────────────────────────────────────────────────────
# 7. 保存
# ──────────────────────────────────────────────────────
lines_after = len(code.splitlines())
print(f"\n[6] 保存...")
print(f"  修改后行数: {lines_after}")
print(f"  变化: {lines_after - lines_before:+d} 行")

with open(SRC, "w", encoding="utf-8") as f:
    f.write(code)
print("[OK] 保存完成")
