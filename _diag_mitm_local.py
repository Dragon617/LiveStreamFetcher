#!/usr/bin/env python3
"""
诊断 mitmproxy local 模式是否能正常工作
"""
import sys
import ctypes

print("=" * 60)
print("mitmproxy local 模式诊断")
print("=" * 60)

# 1. 检查管理员权限
is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
print(f"\n[1] 管理员权限: {is_admin}")
if not is_admin:
    print("    [WARN] 需要管理员权限!")

# 2. 检查 mitmproxy 版本
try:
    import mitmproxy
    print(f"\n[2] mitmproxy 版本: {mitmproxy.__version__}")
except Exception as e:
    print(f"\n[2] 获取版本失败: {e}")

# 3. 检查 mitmproxy_rs.local
try:
    from mitmproxy_rs import local
    print(f"\n[3] mitmproxy_rs.local 可用: OK")
    
    # 测试 describe_spec
    try:
        desc = local.LocalRedirector.describe_spec("Weixin,WeChatAppEx")
        print(f"    describe_spec('Weixin,WeChatAppEx'): {desc}")
    except Exception as e:
        print(f"    describe_spec 失败: {e}")
    
    # 检查 LocalRedirector 类
    print(f"    LocalRedirector 类: {local.LocalRedirector}")
    
except ImportError as e:
    print(f"\n[3] mitmproxy_rs.local 不可用: {e}")
except Exception as e:
    print(f"\n[3] mitmproxy_rs.local 错误: {e}")

# 4. 检查 Windows 相关依赖
try:
    import asyncio
    print(f"\n[4] asyncio 可用: OK")
except Exception as e:
    print(f"\n[4] asyncio 错误: {e}")

# 5. 检查是否可以创建 event loop
try:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    print(f"\n[5] Event loop 创建: OK")
except Exception as e:
    print(f"\n[5] Event loop 创建失败: {e}")

# 6. 尝试直接测试 local redirector（不通过 mitmproxy）
print(f"\n[6] 尝试直接启动 LocalRedirector...")
try:
    from mitmproxy_rs import local
    
    # 这个会尝试创建 Windows Filter Platform 规则
    # 可能会失败，但能看到错误信息
    redirector = local.LocalRedirector.start(
        "127.0.0.1:8088",  # 代理地址
        "Weixin,WeChatAppEx"  # 进程列表
    )
    print(f"    LocalRedirector 启动成功: {redirector}")
    
    # 停止它
    redirector.stop()
    print(f"    LocalRedirector 已停止")
    
except Exception as e:
    print(f"    LocalRedirector 启动失败: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

# 7. 检查 Windows 服务/驱动
try:
    import subprocess
    result = subprocess.run(
        ["sc", "query", "WinDivert"],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='ignore'
    )
    print(f"\n[7] WinDivert 服务状态:")
    if "WinDivert" in result.stdout:
        for line in result.stdout.split('\n'):
            if 'STATE' in line or '状态' in line:
                print(f"    {line.strip()}")
    else:
        print(f"    WinDivert 服务未找到（这是正常的，mitmproxy 12.x 可能不用 WinDivert）")
except Exception as e:
    print(f"\n[7] 检查 WinDivert 失败: {e}")

# 8. 检查微信进程是否存在
try:
    import psutil
    weixin_procs = [p for p in psutil.process_iter(['pid', 'name']) 
                    if 'weixin' in p.info['name'].lower() or 'wechat' in p.info['name'].lower()]
    print(f"\n[8] 微信相关进程:")
    if weixin_procs:
        for p in weixin_procs[:10]:
            print(f"    PID {p.info['pid']}: {p.info['name']}")
    else:
        print(f"    未找到微信进程（请先打开微信）")
except Exception as e:
    print(f"\n[8] 检查进程失败: {e}")

print("\n" + "=" * 60)
print("诊断完成")
print("=" * 60)
