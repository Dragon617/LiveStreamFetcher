# -*- coding: utf-8 -*-
"""controller.py — 业务层接入（线程封装）

UI 层通过本模块调用 live_stream_fetcher 的业务函数，
业务层采用延迟 import，避免 UI 启动时加载 tkinter / yt_dlp。
"""

import traceback

from PySide6.QtCore import QThread, Signal


class FetchWorker(QThread):
    """后台解析线程：调用 extract_streams 并回传结果。"""

    succeeded = Signal(dict)      # 成功 → result dict
    failed = Signal(str)          # 失败 → 错误信息

    def __init__(self, url: str, proxy: str = "", parent=None):
        super().__init__(parent)
        self.url = url
        self.proxy = proxy

    def run(self):
        try:
            from live_stream_fetcher import extract_streams
            result = extract_streams(self.url, self.proxy)
            self.succeeded.emit(result)
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc(limit=3)}")


class LoginCheckWorker(QThread):
    """后台登录状态检查线程。

    调用 live_stream_fetcher 的模块级 `_check_xxx_login_status()` 函数。
    返回值语义：logged_in / never / expired。
    """

    statusReady = Signal(str, bool, bool)   # (platform_key, online, expired)

    # 平台 key → 模块级检测函数名
    _CHECKERS = {
        "dy": "_check_dy_login_status",
        "ks": "_check_ks_login_status",
        "xhs": "_check_xhs_login_status",
        "tb": "_check_tb_login_status",
    }

    def __init__(self, platform_key: str, parent=None):
        super().__init__(parent)
        self.platform_key = platform_key

    def run(self):
        try:
            import live_stream_fetcher as lsf
            func_name = self._CHECKERS.get(self.platform_key)
            if func_name is None:
                self.statusReady.emit(self.platform_key, False, False)
                return
            checker = getattr(lsf, func_name, None)
            if checker is None:
                self.statusReady.emit(self.platform_key, False, False)
                return
            status = checker()
            online = status == "logged_in"
            expired = status == "expired"
            self.statusReady.emit(self.platform_key, online, expired)
        except Exception:
            self.statusReady.emit(self.platform_key, False, False)


class PasswordFetchWorker(QThread):
    """后台获取云端密码线程。

    调用 live_stream_fetcher._fetch_password_from_doc()。
    返回 (password, diag_msg)。
    """

    ready = Signal(str, str)   # (password, diag_msg)

    def run(self):
        try:
            from live_stream_fetcher import _fetch_password_from_doc
            pwd, diag = _fetch_password_from_doc()
            self.ready.emit(pwd or "", diag or "")
        except Exception as e:
            self.ready.emit("", f"{e}")


class ProxyStartWorker(QThread):
    """后台启动本地流代理（淘宝 alicdn / 小红书 xhscdn）。

    为每条需代理的流创建 LocalStreamProxy，就绪后回传映射。
    """

    ready = Signal(dict)   # {original_url: proxy_url}
    failed = Signal(str)

    def __init__(self, streams: list, platform: str, parent=None):
        super().__init__(parent)
        self.streams = streams
        self.platform = platform
        self._proxies = []   # 保持 LocalStreamProxy 引用，防止 GC

    def run(self):
        try:
            from live_stream_fetcher import LocalStreamProxy
            proxy_map = {}
            for s in self.streams:
                url = s.get("url", "")
                if not self._should_proxy(url):
                    continue
                proxy = LocalStreamProxy(
                    platform=self.platform,
                    codec_hint=s.get("codec", ""),
                )
                local_url = proxy.start(url)
                self._proxies.append(proxy)
                proxy_map[url] = local_url
            self.ready.emit(proxy_map)
        except Exception as e:
            self.failed.emit(str(e))

    def _should_proxy(self, url: str) -> bool:
        if self.platform == "淘宝直播":
            return any(d in url for d in ("alicdn.com", "tbcdn.cn", "taobaocdn.com"))
        if self.platform == "小红书":
            return "xhscdn.com" in url
        return False

    def stop_all(self):
        for p in self._proxies:
            try:
                p.stop()
            except Exception:
                pass


class TranscodeWorker(QThread):
    """后台启动 HEVC 转码代理。"""

    ready = Signal(str)    # 本地代理 URL
    failed = Signal(str)

    def __init__(self, url: str, port: int, parent=None):
        super().__init__(parent)
        self.url = url
        self.port = port
        self.proxy = None   # LocalStreamProxy 实例，供外部 stop

    def run(self):
        try:
            from live_stream_fetcher import LocalStreamProxy
            # v8.3.2: 用位置参数，避免防破解混淆改 __init__ 参数名后关键字调用失败
            self.proxy = LocalStreamProxy(
                self.port,   # port（位置参数）
                "通用",       # platform
                "hevc",      # codec_hint
            )
            local_url = self.proxy.start(self.url)
            self.ready.emit(local_url)
        except Exception as e:
            self.failed.emit(str(e))

    def stop_proxy(self):
        if self.proxy is not None:
            try:
                self.proxy.stop()
            except Exception:
                pass
