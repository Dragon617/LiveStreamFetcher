# -*- coding: utf-8 -*-
"""
视频号直播流捕获 - 独立测试脚本 (v4 - WireGuard 模式)
==========================================================
用法：
  python test_wechat_proxy.py              # 默认 wireguard 模式
  python test_wechat_proxy.py --mode local # 用 local 模式（需要管理员，可能有 bug）
  python test_wechat_proxy.py --verbose     # 打印所有请求（调试用）

运行后：
  1. 保持此脚本运行
  2. 在微信 PC 端打开任意视频号直播间
  3. 观察控制台输出，看是否出现 [CAPTURED] 行
  4. 按 Ctrl+C 退出

v4 改进：默认使用 WireGuard 模式替代 local 模式
  - local 模式在 Windows 11 (26200) 上有已知 Bug (#6950): redirect daemon 退出
  - WireGuard 模式：纯用户态 VPN 隧道，无需管理员权限，不依赖 WinDivert
"""

import sys
import os
import time
import threading
import argparse
import tempfile

# ─── 配置 ─────────────────────────────────────────────────
PORT = 8088          # 测试用端口（避免和正式版 8080 冲突）
TIMEOUT = 120        # 最长等待秒数
TARGET_PROCESSES = "Weixin,WeChatAppEx"   # 拦截的微信进程名

# ─── CA 证书目录（和正式版保持一致）─────────────────────────
APPDATA = os.environ.get("APPDATA", "")
CONFDIR = os.path.join(APPDATA, "LiveStreamFetcher", "mitmproxy_conf")

# ─── 获取 mitmproxy 版本 ──────────────────────────────────
def get_mitm_version():
    try:
        from importlib.metadata import version
        return version("mitmproxy")
    except Exception:
        return "unknown"

# ─── 拦截逻辑（直接内嵌，不依赖外部 inline script 文件）─────
# 微信视频号直播流 CDN 域名关键词
LIVE_CDN_KEYWORDS = [
    "wxlivecdn.com",
    "pull-m1.",
    "pull-m2.",
    "pull-ws.",
    "pull-p1.",
    "pull-p2.",
    "voipfinderrdsliveplay",
    "liveplay.myqcloud.com",
    "findervod.wxqcloud.qq.com",
    "voipfinder.wxqcloud.qq.com",
    # 视频号相关域名（扩大匹配范围）
    "finder.video.qq.com",
    "finder-pc.douyinqcloud.com",
    "channel.weixin.qq.com",
    "channels.weixin.qq.com",
    "videofinder.ugcqimg.com",
    "wx.qcloud.cn",
    ".myqcloud.com",
]

NON_LIVE_PATHS = [
    "stodownload", "mmtls", "/update", "/upgrade", "/dns", "favicon.ico",
    "/cgi-bin/", "/misc.php", ".js", ".css", ".png", ".jpg", ".webp",
    ".svg", ".ico", ".woff", ".ttf", ".map",
]

_captured_urls = set()
_all_req_count = [0]
_verbose_flag = False
_seen_hosts = set()   # 用于诊断：记录所有见过的域名
_candidate_urls = []  # 候选 URL（视频号域名但不确定是不是直播流）


def check_request(flow):
    """检查一个请求是否是直播流，返回 (is_live, info_dict) 或 (None, None)"""
    req = flow.request
    url = req.pretty_url or req.url
    host = (req.host or "").lower()
    path_lower = (req.path or "").lower()
    method = req.method or ""

    _all_req_count[0] += 1

    # 记录所有域名（诊断用）
    domain = host.split(":")[0]  # 去掉端口
    if domain not in _seen_hosts:
        _seen_hosts.add(domain)

    # 打印所有请求（verbose 模式或前 100 条）
    if _verbose_flag or _all_req_count[0] <= 100:
        print(f"[REQ#{_all_req_count[0]}] {method} {host}{path_lower[:120]}")
        sys.stdout.flush()

    # 跳过非直播路径（静态资源等）
    for skip in NON_LIVE_PATHS:
        if skip in path_lower:
            return None, None

    is_live_cdn = any(k in host for k in LIVE_CDN_KEYWORDS)
    has_live_path = (
        ".flv" in path_lower or ".m3u8" in path_lower or
        "/live/" in path_lower or "/live_" in path_lower or
        "stream_key" in path_lower or ".ts?" in path_lower
    )
    is_finder = "finder" in host and ("qq.com" in host or "qcloud" in host or "weixin" in host)

    # 记录候选（命中的视频号域名但可能不是流）
    if (is_live_cdn or is_finder) and url not in _captured_urls:
        _candidate_urls.append({"url": url, "host": host, "path": path_lower[:200], "method": method})

    # 判断是否为直播流：放宽条件
    if not ((is_live_cdn and has_live_path) or
            (is_finder and has_live_path) or
            (is_live_cdn and "wxlivecdn" in host)):
        return None, None

    # 去重
    if url in _captured_urls:
        return None, None
    _captured_urls.add(url)

    fmt = "FLV" if ".flv" in path_lower else ("M3U8" if ".m3u8" in path_lower else "STREAM")
    info = {"url": url, "format": fmt, "host": host}
    return True, info


def request(flow):
    """mitmproxy request hook"""
    try:
        is_live, info = check_request(flow)
        if is_live:
            print("\n" + "=" * 60)
            print(f"[CAPTURED #{len(_captured_urls)}] {info['format']}")
            print(f"  URL : {info['url']}")
            print(f"  Host: {info['host']}")
            print("=" * 60 + "\n")
            sys.stdout.flush()
    except Exception as e:
        print(f"[ERROR] hook 异常: {e}")


class RequestAddon:
    """mitmproxy addon：拦截 HTTP 请求并检测直播流"""
    def request(self, flow):
        request(flow)


# ─── 主流程 ──────────────────────────────────────────────
def main():
    global _verbose_flag

    parser = argparse.ArgumentParser(description="微信视频号直播流捕获测试脚本")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印所有请求（调试用）")
    parser.add_argument("--port", type=int, default=PORT, help=f"监听端口（默认 {PORT}）")
    parser.add_argument("--timeout", type=int, default=TIMEOUT, help=f"等待超时秒数（默认 {TIMEOUT}）")
    parser.add_argument("--mode", "-m", default="wireguard",
                        choices=["wireguard", "local", "regular"],
                        help="代理模式: wireguard(默认VPN隧道) | local(需管理员) | regular(常规代理)")
    args = parser.parse_args()
    _verbose_flag = args.verbose

    print("=" * 60)
    print("  视频号直播流捕获测试 v4")
    print("=" * 60)
    print(f"  模式     : {args.mode}")
    if args.mode == "local":
        print(f"  拦截进程 : {TARGET_PROCESSES}")
    print(f"  端口     : {args.port}")
    print(f"  CA目录   : {CONFDIR}")
    print(f"  超时     : {args.timeout}s")
    print(f"  Verbose  : {args.verbose}")
    print("=" * 60)

    # 检查 CA 证书目录
    ca_pem = os.path.join(CONFDIR, "mitmproxy-ca-cert.pem")
    if not os.path.exists(ca_pem):
        print(f"\n[警告] CA 证书不存在: {ca_pem}")
        print("  → 请先运行主程序完成 CA 证书安装，否则 HTTPS 流量无法解密\n")
    else:
        print(f"[OK] CA 证书已就绪: {ca_pem}")

    # ── 使用 mitmproxy Python API 直接在进程中启动 ──
    try:
        from mitmproxy import options, ctx
        from mitmproxy.tools.dump import DumpMaster
        print(f"[OK] mitmproxy {get_mitm_version()} 已加载")
    except ImportError as e:
        print(f"[ERROR] 导入 mitmproxy 失败: {e}")
        print("  请先安装: pip install mitmproxy")
        sys.exit(1)

    # 构造 Options（根据模式选择）
    if args.mode == "local":
        mode_val = [f"local:{TARGET_PROCESSES}"]
    elif args.mode == "wireguard":
        mode_val = ["wireguard"]
    else:  # regular
        mode_val = ["regular"]

    opts = options.Options(
        listen_port=args.port,
        mode=mode_val,
        ssl_insecure=True,
        upstream_cert=False,
    )
    if os.path.exists(ca_pem):
        opts.confdir = CONFDIR

    # 创建 master 并添加 addon（需要先创建 event loop）
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    m = DumpMaster(opts, with_termlog=False, loop=loop)
    m.addons.add(RequestAddon())

    # 将 master.run() 协程调度到 loop 上，然后 loop.run_forever() 来执行
    loop.create_task(m.run())

    print(f"\n[OK] 正在启动 mitmdump proxy (mode={args.mode})...")

    # 在线程中运行 master.run()（阻塞）
    proxy_running = [True]
    proxy_error = [None]

    def run_proxy():
        """在线程中运行 DumpMaster"""
        try:
            loop.run_forever()
        except Exception as e:
            proxy_error[0] = e
        finally:
            proxy_running[0] = False

    t = threading.Thread(target=run_proxy, daemon=True)
    t.start()

    time.sleep(3)  # 等待 proxy 绑定端口（wireguard 需要更多时间初始化）

    if not proxy_running[0]:
        if proxy_error[0]:
            print(f"\n[ERROR] proxy 启动失败: {proxy_error[0]}")
            import traceback
            traceback.print_exception(type(proxy_error[0]), proxy_error[0], proxy_error[0].__traceback__)
        else:
            print("\n[ERROR] proxy 启动后立刻退出！")
            if args.mode == "local":
                print("  -> local 模式在 Windows 11 上有已知 Bug (#6950): redirect daemon 退出")
                print("  -> 建议改用: python test_wechat_proxy.py --mode wireguard")
            sys.exit(1)

    print(f"[OK] mitmdump 已就绪，监听 127.0.0.1:{args.port}")
    
    if args.mode == "wireguard":
        print("\n" + "<" * 30)
        print("  WireGuard VPN 模式已启动")
        print("  所有流量将通过隧道自动拦截，无需配置代理")
        print("<" * 30)

    print()
    print(">>> 现在请在 PC 微信中打开视频号直播间 <<<")
    print(f">>> 等待捕获直播流（最长 {args.timeout}s）… Ctrl+C 退出 <<<")
    print()

    # 等待循环
    start = time.time()
    captured_count = len(_captured_urls)
    try:
        while time.time() - start < args.timeout:
            if not proxy_running[0]:
                print(f"\n[WARN] proxy 进程已退出")
                if proxy_error[0]:
                    print(f"  错误: {proxy_error[0]}")
                break
            new_count = len(_captured_urls)
            if new_count > captured_count:
                captured_count = new_count
                # 有新捕获，延长等待时间
            time.sleep(1)
        else:
            print(f"\n[INFO] 已等待 {args.timeout}s，超时退出。")
    except KeyboardInterrupt:
        print("\n[INFO] 用户中断。")
    finally:
        print("\n正在关闭 proxy...")
        try:
            loop.call_soon_threadsafe(m.shutdown)
            t.join(timeout=5)
        except Exception:
            pass
        try:
            loop.stop()
        except Exception:
            pass

    # 总结
    total_captured = len(_captured_urls)
    print("\n" + "=" * 60)
    print(f"  共捕获到 {total_captured} 条直播流标记")
    print(f"  总请求数: {_all_req_count[0]}")
    print(f"  涉及域名: {len(_seen_hosts)} 个")
    if _seen_hosts:
        print(f"  域名列表: {', '.join(sorted(_seen_hosts)[:20])}")
        if len(_seen_hosts) > 20:
            print(f"            ... 还有 {len(_seen_hosts) - 20} 个")
    print("=" * 60)

    # 输出候选 URL（视频号域名命中的请求）
    if _candidate_urls and not total_captured:
        print(f"\n[候选] 命中视频号域名的请求（共 {len(_candidate_urls)} 条，但未匹配直播流特征）:")
        for i, c in enumerate(_candidate_urls[:30]):
            print(f"  {i+1}. [{c['method']}] {c['host']}{c['path'][:150]}")
        if len(_candidate_urls) > 30:
            print(f"  ... 还有 {len(_candidate_urls) - 30} 条")

    if not total_captured:
        print("\n[诊断提示]")
        print(f"  1. 上方是否有 [REQ#N] 日志？（共 {_all_req_count[0]} 条）")
        if _all_req_count[0] == 0:
            print("     - 没有任何请求 -> 流量未被拦截")
            print("       最可能原因：未以管理员权限运行！")
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            print(f"       当前管理员权限: {is_admin}")
            print("       解决方案：右键 PowerShell -> 以管理员身份运行 -> 再执行此脚本")
        elif len(_seen_hosts) > 0:
            print("     - 有请求但没匹配到直播流特征")
            print("       可能原因：直播间已结束 / 直播流域名变了 / 需要进一步分析")
            print("       建议：用 --verbose 模式重试，查看所有请求 URL")


if __name__ == "__main__":
    main()
