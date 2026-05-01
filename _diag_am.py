# -*- coding: utf-8 -*-
"""诊断 v6: 检查 AddonManager 内部结构和 Proxyserver 注册状态"""
import asyncio
from mitmproxy import options
from mitmproxy.tools.dump import DumpMaster
from mitmproxy.addons.proxyserver import Proxyserver

opts = options.Options(
    listen_port=8089,
    mode=['wireguard'],
    ssl_insecure=True,
)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

m = DumpMaster(opts, with_termlog=False, loop=loop)

print("=== AddonManager 结构 ===")
am = m.addons
print(f"类型: {type(am)}")
pub_methods = [x for x in dir(am) if not x.startswith('_')]
print(f"公开方法/属性: {pub_methods}")

# chain 属性
if hasattr(am, 'chain'):
    print(f"\nchain 长度: {len(am.chain)}")
    for a in am.chain:
        name = a.__class__.__name__
        extra = ""
        if hasattr(a, 'is_running'):
            extra = f" is_running={a.is_running}"
        print(f"  - {name}{extra}")
elif hasattr(am, '__len__'):
    print(f"\nlen(addons): {len(am)}")
else:
    print(f"\nrepr: {repr(am)}")

# get(Proxyserver)
try:
    ps = am.get(Proxyserver)
    print(f"\nget(Proxyserver): {ps}")
except Exception as e:
    print(f"\nget(Proxyserver) 失败: {e}")

# 尝试通过 chain 找 Proxyserver
print()
if hasattr(am, 'chain'):
    for i, a in enumerate(am.chain):
        cls_name = a.__class__.__name__
        if 'proxy' in cls_name.lower() or 'server' in cls_name.lower():
            running = getattr(a, 'is_running', 'N/A')
            servers = getattr(a, 'servers', 'N/A')
            print(f"[#{i}] {cls_name} -> running={running}, servers={servers}")

loop.close()
