# -*- coding: utf-8 -*-
"""
最小化诊断 - 直接测试 DumpMaster.run() 是否真的在运行
用 regular 模式（不需要特殊驱动），然后手动 curl 测试
"""
import sys
import os
import time
import threading
import asyncio
import subprocess

PORT = 8090

class SimpleAddon:
    def __init__(self):
        self.count = 0
    
    def request(self, flow):
        self.count += 1
        req = flow.request
        print(f"  [REQ#{self.count}] {req.method} {req.host}{(req.path or '')[:80]}")
        sys.stdout.flush()
    
    def response(self, flow):
        if self.count <= 5:
            print(f"  [RES#{self.count}] {flow.response.status_code}")
            sys.stdout.flush()


def main():
    from mitmproxy import options
    from mitmproxy.tools.dump import DumpMaster

    print("=" * 60)
    print("  mitmproxy 基础功能测试 (regular 模式)")
    print("=" * 60)
    
    # 用最简单的 regular 模式
    opts = options.Options(
        listen_port=PORT,
        mode=["regular"],
        ssl_insecure=True,
        # 不设 confdir，让它自己处理证书
    )
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    addon = SimpleAddon()
    m = DumpMaster(opts, with_termlog=False, loop=loop)
    m.addons.add(addon)
    
    print(f"[1] DumpMaster 创建完成")
    print(f"[2] 启动 proxy 线程...")
    
    proxy_error = [None]
    
    def run_proxy():
        try:
            loop.create_task(m.run())
            loop.run_forever()
            print("[loop] run_forever() 正常退出")
        except Exception as e:
            proxy_error[0] = e
            print(f"[loop] 异常: {type(e).__name__}: {e}")
    
    t = threading.Thread(target=run_proxy, daemon=True)
    t.start()
    
    time.sleep(3)  # 等 proxy 初始化
    
    # 测试 1: 端口是否在监听
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("127.0.0.1", PORT))
    sock.close()
    
    if result == 0:
        print(f"[3] 端口 :{PORT} 已在监听 OK")
    else:
        print(f"[3] 端口 :{PORT} 未在监听! (error={result})")
    
    # 测试 2: 用 curl 发一个请求（走系统代理）
    print(f"[4] 发送测试请求到 httpbin.org (通过 proxy 127.0.0.1:{PORT})...")
    try:
        result = subprocess.run(
            ["curl.exe", "-x", f"http://127.0.0.1:{PORT}", 
             "-s", "-o", "NUL", "-w", "HTTP/%{http_version} %{http_code} %{time_total}s",
             "--connect-timeout", "10",
             "http://httpbin.org/get"],
            capture_output=True, text=True, timeout=15
        )
        print(f"  curl 结果: '{result.stdout.strip()}'")
        if result.stderr:
            print(f"  curl stderr: {result.stderr.strip()[:200]}")
    except FileNotFoundError:
        print("  curl.exe 不存在，跳过")
        # 用 Python urllib 测试
        try:
            import urllib.request
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{PORT}"})
            )
            resp = opener.open("http://httpbin.org/get", timeout=10)
            print(f"  urllib 结果: HTTP {resp.status}")
        except Exception as e2:
            print(f"  urllib 也失败: {e2}")
    except Exception as e:
        print(f"  curl 异常: {e}")
    
    # 再等几秒看有没有请求到达 addon
    print("\n[5] 等待 3s 看 addon 收到多少请求...")
    time.sleep(3)
    
    print(f"\n[6] Addon 统计:")
    print(f"  总请求数: {addon.count}")
    
    if proxy_error[0]:
        print(f"  Proxy 错误: {proxy_error[0]}")
    
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
    if addon.count > 0:
        print("  结论: DumpMaster 工作正常！能收到请求。")
        print("  问题出在 wireguard/local 模式的流量拦截层。")
    else:
        print("  结论: 即使 regular 模式 + curl 也收不到请求！")
        print("  说明 DumpMaster.run() 根本没正常工作。")
    print("=" * 60)


if __name__ == "__main__":
    main()
