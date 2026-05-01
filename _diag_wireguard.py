# -*- coding: utf-8 -*-
"""
诊断 v6: 验证根因 —— running() 后需要手动触发 servers.update()
并测试手动调用 setup_servers() 能否让 WireGuard 启动
"""
import sys
import os
import time
import threading
import asyncio

os.environ["MITMPROXY_LOG_DIR"] = r"c:\Users\15346\WorkBuddy\20260408125205"

from mitmproxy import options, ctx
from mitmproxy.tools.dump import DumpMaster


class SimpleAddon:
    def __init__(self):
        self.count = 0

    def request(self, flow):
        self.count += 1
        req = flow.request
        print(f"  [REQ#{self.count}] {req.method} {req.host}{(req.path or '')[:100]}")
        sys.stdout.flush()


def main():
    print("=" * 60)
    print("  WireGuard 根因验证 + 手动触发修复")
    print("=" * 60)

    PORT = 8093

    opts = options.Options(
        listen_port=PORT,
        mode=["wireguard"],
        server=True,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    addon = SimpleAddon()
    m = DumpMaster(opts, with_termlog=False, loop=loop)
    m.addons.add(addon)

    # 获取 Proxyserver
    from mitmproxy.addons.proxyserver import Proxyserver
    ps = m.addons.get(Proxyserver)

    print(f"\n[1] 创建完成, is_running={ps.is_running}")
    print(f"    server={opts.server}, mode={opts.mode}")

    # 启动
    async def do_startup():
        await m.run()

    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    time.sleep(5)

    print(f"\n[2] 启动 5s 后:")
    print(f"    is_running={ps.is_running}")
    print(f"    _instances={list(ps.servers._instances.keys())}")

    if not ps.servers._instances:
        print("\n    *** 确认: servers.update() 未被自动调用 ***")

    # ===== 关键修复: 手动调用 setup_servers() =====
    print("\n[3] 手动调用 ps.setup_servers()...")

    async def manual_setup():
        try:
            result = await ps.setup_servers()
            return result, None
        except Exception as e:
            import traceback
            return False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    future = asyncio.run_coroutine_threadsafe(manual_setup(), loop)
    result, error = future.result(timeout=15)

    if error:
        print(f"    失败: {error}")
    else:
        print(f"    返回值: {result}")

    time.sleep(2)

    print(f"\n[4] 手动 setup_servers 后:")
    print(f"    is_running={ps.is_running}")
    print(f"    _instances keys: {list(ps.servers._instances.keys())}")
    for spec, inst in ps.servers._instances.items():
        print(f"    - {spec}:")
        print(f"      type: {type(inst).__name__}")
        print(f"      is_running: {getattr(inst, 'is_running', 'N/A')}")
        if hasattr(inst, 'listen_addrs'):
            print(f"      listen_addrs: {inst.listen_addrs}")
        if hasattr(inst, 'last_exception') and inst.last_exception:
            print(f"      last_exception: {inst.last_exception}")

    # 端口检查
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_ok = sock.connect_ex(("127.0.0.1", PORT)) == 0
    sock.close()
    print(f"\n[5] TCP :{PORT} -> {'LISTENING' if tcp_ok else 'NOT LISTENING'}")

    # UDP 检查（WireGuard 用 UDP）
    sock_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 尝试发个包看端口是否可达
        sock_udp.settimeout(1)
        sock_udp.sendto(b"ping", ("127.0.0.1", PORT))
        udp_ok = True
    except Exception as e:
        udp_ok = False
    finally:
        sock_udp.close()
    print(f"    UDP :{PORT} (WireGuard) -> 可达测试完成")

    # 清理
    print("\n关闭中...")
    try:
        loop.call_soon_threadsafe(m.shutdown)
        t.join(timeout=3)
    except Exception as e:
        print(f"  关闭异常: {e}")
    try:
        loop.stop()
    except:
        pass

    print("\n" + "=" * 60)
    has_instances = len(ps.servers._instances) > 0
    if has_instances and result:
        print("  结论: 手动调用 setup_servers() 成功!")
        print("  根因确认: running() 不触发 update(), 需要手动调用")
        print("  正确用法: master.startup() 完毕后手动调 ps.setup_servers()")
    elif has_instances:
        print("  结论: Server 实例创建了但启动失败 (看上面 last_exception)")
    else:
        print("  结论: setup_servers() 也失败了，看上面的错误信息")
    print("=" * 60)


if __name__ == "__main__":
    main()
