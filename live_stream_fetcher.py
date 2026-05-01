# -*- coding: utf-8 -*-
"""
多平台直播视频流获取工具 v6.3
支持平台：抖音、快手、小红书、淘宝直播、YY直播
功能：输入直播间URL → 输出可用的直播视频流链接（M3U8/FLV/MP4）
策略：优先使用平台专属解析器，失败后降级到 yt-dlp
"""

import sys
import os
import re
import json
import time
import uuid
import random
import string
import shutil
import subprocess
import threading
import socket
import io
import base64
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from urllib.parse import urlparse, parse_qs


# ═══════════════════════════════════════════════════════
# 自定义异常（不会被降级逻辑吞掉）
# ═══════════════════════════════════════════════════════

class FetchUserError(Exception):
    """用户可理解的错误（如未直播、URL无效等），直接展示给用户，不降级到 yt-dlp"""
    pass

try:
    import yt_dlp
except ImportError:
    print("缺少 yt-dlp，请运行: pip install yt-dlp")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("缺少 requests，请运行: pip install requests")
    sys.exit(1)

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None


# ─── 平台识别 ───────────────────────────────────────────
PLATFORM_PATTERNS = {
    "抖音": [
        r"douyin\.com",
        r"iesdouyin\.com",
        r"tiktok\.com",
    ],
    "快手": [
        r"kuaishou\.com",
        r"gifshow\.com",
        r"chenzhongtech\.com",
    ],
    "小红书": [
        r"xiaohongshu\.com",
        r"xhslink\.com",
    ],
    "淘宝直播": [
        r"taobao\.com.*live",
        r"tb\.cn",
        r"m\.tb\.cn",
        r"live\.taobao\.com",
        r"tbzb\.taobao\.com",
        r"taobao\.com\/.*\/live",
    ],
    "YY直播": [
        r"yy\.com",
        r"mobi\.yy\.com",
    ],
}

HEADERS_PC = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

HEADERS_MOBILE = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/16.6 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def detect_platform(url: str) -> str:
    url_lower = url.lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url_lower):
                return platform
    return "未知平台"


def guess_format(url: str) -> str:
    url_lower = url.lower()
    if ".m3u8" in url_lower or "m3u8" in url_lower:
        return "M3U8"
    elif ".flv" in url_lower or "flv" in url_lower:
        return "FLV"
    elif ".mp4" in url_lower or "mp4" in url_lower:
        return "MP4"
    return "未知"


def make_requests_session(proxy: str = "") -> requests.Session:
    s = requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


# ═══════════════════════════════════════════════════════
# 平台专属解析器
# ═══════════════════════════════════════════════════════

# ─── 快手 ────────────────────────────────────────────────

def _ks_extract_room_id(url: str) -> str:
    """从快手URL提取直播间ID"""
    # https://live.kuaishou.com/u/3xd7in4gwwnjpua
    m = re.search(r'live\.kuaishou\.com/u/([A-Za-z0-9_]+)', url)
    if m:
        return m.group(1)
    # https://live.kuaishou.com/profile/ltsx1219
    m = re.search(r'live\.kuaishou\.com/profile/([A-Za-z0-9_]+)', url)
    if m:
        return m.group(1)
    # https://m.gifshow.com/fw/live/xxx
    m = re.search(r'gifshow\.com/fw/live/(\w+)', url)
    if m:
        return m.group(1)
    return ""


def _ks_extract_state(text: str):
    """从快手PC端页面提取 __INITIAL_STATE__ JSON"""
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*', text)
    if not m:
        return None
    start = m.end()
    brace_count = 0
    json_str = ""
    for ch in text[start:]:
        if ch == '{':
            brace_count += 1
        elif ch == '}':
            brace_count -= 1
        json_str += ch
        if brace_count == 0:
            break
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, IndexError):
        return None


def _ks_find_livestream(obj, path=""):
    """递归搜索含非空 liveStream 的节点"""
    if isinstance(obj, dict):
        if "liveStream" in obj and isinstance(obj["liveStream"], dict) and obj["liveStream"]:
            return path, obj
        for k, v in obj.items():
            result = _ks_find_livestream(v, f"{path}.{k}")
            if result and result[1]:
                return result
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            result = _ks_find_livestream(item, f"{path}[{i}]")
            if result and result[1]:
                return result
    return None


def _ks_fetch_livedetail(session, room_id, url):
    """通过 livedetail API 获取直播间信息（不受 SSR 风控影响）"""
    detail_url = "https://live.kuaishou.com/live_api/liveroom/livedetail"
    try:
        resp = session.get(
            detail_url,
            params={"principalId": room_id},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": url,
                "Accept": "application/json, text/plain, */*",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _ks_parse_playurls_adaptation(play_urls):
    """解析 playUrls 中 adaptationSet 格式的流地址（新格式）"""
    streams = []
    if not play_urls or not isinstance(play_urls, dict):
        return streams

    codec_labels = {"h264": "H264", "hevc": "HEVC/H265"}
    for codec, quality_data in play_urls.items():
        if not quality_data or not isinstance(quality_data, dict):
            continue
        codec_label = codec_labels.get(codec, codec.upper())

        # 新格式: { adaptationSet: { representation: [{url, name, bitrate}] } }
        adaptation = quality_data.get("adaptationSet")
        if isinstance(adaptation, dict):
            reps = adaptation.get("representation", [])
            for rep in reps:
                stream_url = rep.get("url", "") or rep.get("completeUrl", "")
                name = rep.get("name", "") or rep.get("qualityType", "")
                if stream_url:
                    streams.append({
                        "quality": f"{name}({codec_label})",
                        "format": guess_format(stream_url),
                        "url": stream_url,
                        "source": f"playUrls-{codec_label}",
                    })
            continue

        # 旧格式: { "name": {url, qualityType} }
        for quality_name, url_data in quality_data.items():
            stream_url = ""
            if isinstance(url_data, dict):
                stream_url = url_data.get("url", "") or url_data.get("completeUrl", "")
            elif isinstance(url_data, str):
                stream_url = url_data
            if stream_url:
                streams.append({
                    "quality": f"{quality_name}({codec_label})",
                    "format": guess_format(stream_url),
                    "url": stream_url,
                    "source": f"playUrls-{codec_label}",
                })

    return streams


def _get_ks_browser_data_dir():
    """获取快手浏览器持久化缓存目录（cookie / session / localStorage）

    使用 %APPDATA%/LiveStreamFetcher/kuaishou_browser_data 目录，
    这样 EXE 和源码运行都能找到同一个缓存目录。
    """
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(base, "LiveStreamFetcher", "kuaishou_browser_data")
    os.makedirs(d, exist_ok=True)
    return d


def _check_ks_login_status():
    """检测快手浏览器持久化目录中是否存在有效的登录 Cookie。

    通过直接读取 SQLite 数据库，检查是否含有 kuaishou.com 域名的 Cookie。
    这比检查文件大小更准确，避免浏览器自动生成的空数据库导致误判。
    返回:
        "logged_in"  — 存在快手登录 Cookie
        "never"      — 从未登录（无有效 Cookie）
        "expired"    — 保留字段
    """
    import sqlite3

    data_dir = _get_ks_browser_data_dir()
    default_dir = os.path.join(data_dir, "Default")
    if not os.path.isdir(default_dir):
        return "never"

    # 在两个可能的 Cookie 存储位置中检查
    cookie_paths = [
        os.path.join(default_dir, "Cookies"),
        os.path.join(default_dir, "Network", "Cookies"),
    ]

    for db_path in cookie_paths:
        if not os.path.isfile(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            # 查询是否存在 kuaishou.com 域名的 Cookie
            cursor.execute(
                "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%kuaishou.com%'"
            )
            count = cursor.fetchone()[0]
            conn.close()
            if count > 0:
                return "logged_in"
        except Exception:
            pass

    # 没有找到任何快手 Cookie
    return "never"


def _clear_ks_cookies():
    """删除快手浏览器整个持久化目录，强制重新登录。

    由于登录状态检测采用多指标（Cookies、Login Data、History 等），
    只删除 Cookies 文件不够，需要清除整个 browser_data 目录，
    确保退出后状态检测返回 "never"。
    返回 True 表示成功清除。
    """
    import shutil
    data_dir = _get_ks_browser_data_dir()

    if os.path.isdir(data_dir):
        try:
            shutil.rmtree(data_dir)
            return True
        except Exception:
            return False

    # 目录不存在，视为已清除
    return True


def _check_tb_login_status():
    """检测淘宝浏览器持久化目录中是否存在有效的登录 Cookie。

    淘宝登录的关键 Cookie 包括：_tb_token_, cookie2, sgcookie, unb, lgc, nk 等。
    通过直接读取 SQLite 数据库，检查是否含有 taobao.com 域名的 Cookie。
    返回:
        "logged_in"  — 存在淘宝登录 Cookie
        "never"      — 从未登录（无有效 Cookie）
        "expired"    — 保留字段
    """
    import sqlite3

    data_dir = _get_tb_browser_data_dir()
    default_dir = os.path.join(data_dir, "Default")
    if not os.path.isdir(default_dir):
        return "never"

    # 在两个可能的 Cookie 存储位置中检查
    cookie_paths = [
        os.path.join(default_dir, "Cookies"),
        os.path.join(default_dir, "Network", "Cookies"),
    ]

    for db_path in cookie_paths:
        if not os.path.isfile(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            # 查询是否存在 taobao.com 域名的 Cookie
            cursor.execute(
                "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%taobao.com%'"
            )
            count = cursor.fetchone()[0]
            conn.close()
            if count > 0:
                return "logged_in"
        except Exception:
            pass

    # 没有找到任何淘宝 Cookie
    return "never"


def _clear_tb_cookies():
    """删除淘宝浏览器整个持久化目录，强制重新登录。

    返回 True 表示成功清除。
    """
    import shutil
    data_dir = _get_tb_browser_data_dir()

    if os.path.isdir(data_dir):
        try:
            shutil.rmtree(data_dir)
            return True
        except Exception:
            return False

    return True


def _get_embedded_chromium_path():
    """获取嵌入式 Chromium 浏览器的可执行文件路径。

    逻辑：
    1. 检查 EXE 同目录下的 embedded_chromium/chrome.exe（便携部署）
    2. 检查 PyInstaller sys._MEIPASS 临时解压目录中的 embedded_chromium/chrome.exe
    3. 检查 %APPDATA%/LiveStreamFetcher/embedded_chromium/chrome.exe（已释放）
    都没有则返回 None。
    """
    # 路径1: EXE 同目录（便携部署场景）
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    portable_path = os.path.join(exe_dir, "embedded_chromium", "chrome.exe")
    if os.path.isfile(portable_path):
        return os.path.dirname(portable_path)

    # 路径2: PyInstaller 临时目录（首次运行，datas 从 _MEIPASS 解压）
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        meipass_path = os.path.join(sys._MEIPASS, "embedded_chromium", "chrome.exe")
        if os.path.isfile(meipass_path):
            return os.path.dirname(meipass_path)

    # 路径3: 已释放到 AppData
    appdata_path = os.path.join(os.environ.get("APPDATA", ""), "LiveStreamFetcher", "embedded_chromium", "chrome.exe")
    if os.path.isfile(appdata_path):
        return os.path.dirname(appdata_path)

    return None


def _extract_embedded_chromium():
    """从 PyInstaller _MEIPASS 释放 Chromium 到 %APPDATA%/LiveStreamFetcher/embedded_chromium/

    仅在首次运行时执行（检测目标目录无 chrome.exe 则释放）。
    返回释放后的 chromium 目录路径，失败返回 None。
    """
    if not getattr(sys, 'frozen', False) or not hasattr(sys, '_MEIPASS'):
        return None

    src_dir = os.path.join(sys._MEIPASS, "embedded_chromium")
    if not os.path.isdir(src_dir):
        return None

    dst_base = os.path.join(os.environ.get("APPDATA", ""), "LiveStreamFetcher")
    dst_dir = os.path.join(dst_base, "embedded_chromium")

    # 已存在则不重复释放
    if os.path.isfile(os.path.join(dst_dir, "chrome.exe")):
        return dst_dir

    print("[Chromium] 首次运行，正在释放嵌入式浏览器到本地（约 400MB）...")
    try:
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        print(f"[Chromium] 释放完成: {dst_dir}")
        return dst_dir
    except Exception as e:
        print(f"[Chromium] 释放失败: {e}")
        return None


def _ensure_chromium_ready():
    """确保 Chromium 可用：检查便携目录 → 检查 AppData → 从 _MEIPASS 释放。

    返回 chromium 目录路径（包含 chrome.exe），失败返回 None。
    """
    # 先检查便携目录和 AppData
    path = _get_embedded_chromium_path()
    if path:
        return path

    # 尝试从 _MEIPASS 释放
    path = _extract_embedded_chromium()
    if path:
        return path

    return None


def _ks_fetch_via_playwright(url, room_id):
    """通过 Playwright 浏览器自动化获取快手直播流（风控降级方案）

    快手 2024-2025 年大幅升级反爬：
    - SSR 页面频繁返回 errorType.type=2（请求过快）
    - 可能触发 CAPTCHA 滑块验证
    - livedetail API 需要数字 principalId（不接受用户名）

    策略：
    1. 使用 persistent_context 保持登录态（cookie / session 持久化到本地）
    2. 用非 headless 模式打开浏览器（让用户可以手动过验证码）
    3. 监听 livev.m.chenzhongtech.com 的 byUser / web API
    4. 从拦截到的数据中提取直播流地址
    5. 检测到快手登录成功后自动刷新页面
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[Playwright] playwright 未安装，跳过浏览器解析策略")
        return None

    try:
        with sync_playwright() as p:
            user_data_dir = _get_ks_browser_data_dir()
            print(f"[Playwright] 使用浏览器缓存目录: {user_data_dir}")

            # ── 浏览器启动参数（所有方式共用）──
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "no_viewport": False,
            }

            # ── 启动浏览器（优先嵌入式 Chromium → Playwright Chromium → Chrome → Edge）──
            launch_errors = []
            context = None

            # 方式1: 嵌入式 Chromium（打包在 EXE 中，首次运行释放到 AppData）
            embedded_chromium = _ensure_chromium_ready()
            if embedded_chromium:
                try:
                    print(f"[Playwright] 使用嵌入式 Chromium: {embedded_chromium}")
                    context = p.chromium.launch_persistent_context(
                        user_data_dir,
                        executable_path=os.path.join(embedded_chromium, "chrome.exe"),
                        **launch_kwargs,
                    )
                except Exception as e_embed:
                    launch_errors.append(f"Embedded Chromium: {e_embed}")
                    print(f"[Playwright] 嵌入式 Chromium 启动失败: {e_embed}")

            # 方式2: Playwright 内置 Chromium（开发环境）
            if not context:
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir,
                        channel=None,
                        **launch_kwargs,
                    )
                except Exception as e1:
                    launch_errors.append(f"Chromium: {e1}")
                    # 方式3: 系统 Chrome
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir,
                            channel="chrome",
                            **launch_kwargs,
                        )
                    except Exception as e2:
                        launch_errors.append(f"Chrome: {e2}")
                        # 方式4: 系统 Edge
                        try:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir,
                                channel="msedge",
                                **launch_kwargs,
                            )
                        except Exception as e3:
                            launch_errors.append(f"Edge: {e3}")

            if not context:
                print(f"[Playwright] 无法启动浏览器: {'; '.join(launch_errors)}")
                return None

            page = context.pages[0] if context.pages else context.new_page()

            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
            """)

            result_data = {}

            def on_response(response):
                resp_url = response.url
                if any(k in resp_url for k in [
                    "byUser", "livev.m.chenzhongtech.com",
                    "livedetail", "liveroom/enterroom", "liveroom/reco",
                ]):
                    try:
                        ct = response.body()
                        if ct and len(ct) < 500000:
                            result_data[resp_url] = json.loads(ct.decode("utf-8", errors="replace"))
                    except Exception:
                        pass

            page.on("response", on_response)

            # ── 登录检测：监听页面跳转到快手首页说明登录成功 ──
            login_detected = {"value": False}
            prev_url = {"value": page.url if page.url.startswith("http") else ""}

            def on_navigate(navigation):
                try:
                    nav_url = navigation.url
                    if not nav_url or nav_url == "about:blank":
                        return
                    # 检测登录成功：从登录页面跳转到快手首页/其他非登录页面
                    if "passport.kuaishou.com" in prev_url.get("value", ""):
                        if "passport.kuaishou.com" not in nav_url:
                            login_detected["value"] = True
                            print(f"[Playwright] 检测到快手登录成功，将自动刷新直播间页面")
                    prev_url["value"] = nav_url
                except Exception:
                    pass

            page.on("framenavigated", on_navigate)

            # ── 登录检测：用 cookie 判断是否已登录 ──
            KS_LOGIN_URL = "https://passport.kuaishou.com/pc/account/login"
            already_logged_in = False
            try:
                cookies = context.cookies()
                # 检查快手的关键登录 cookie
                ks_cookies = [c for c in cookies if "kuaishou" in c.get("domain", "")]
                cookie_names = [c.get("name", "") for c in ks_cookies]
                # userId 或 kuaishou.server.web_st 存在说明已登录
                if any(n in cookie_names for n in ("userId", "kuaishou.server.web_st", "did", "kpf")):
                    already_logged_in = True
                    print("[Playwright] 检测到已有快手登录态，直接访问直播间")
            except Exception:
                pass

            # ── 先访问直播间页面 ──
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # 如果未登录，页面可能被重定向到登录页，或者内容为空
            # 等待 3 秒后检查是否需要登录
            page.wait_for_timeout(3000)

            need_login = False
            if not already_logged_in:
                current_url = page.url
                # 检查是否被重定向到了登录页面
                if "passport.kuaishou.com" in current_url:
                    need_login = True
                    print("[Playwright] 被重定向到登录页面，将打开二维码登录页")
                else:
                    # 检查页面是否有登录提示（通过 SSR state 检测）
                    try:
                        state = page.evaluate("""() => {
                            if (window.__INITIAL_STATE__) return window.__INITIAL_STATE__;
                            return null;
                        }""")
                        if state:
                            user_info = state.get("userData") or state.get("user")
                            if not user_info or not (user_info.get("user_id") or user_info.get("userId")):
                                # 没有 SSR state 里的用户信息，再检查 cookie
                                cookies = context.cookies()
                                ks_cookies = [c for c in cookies if "kuaishou" in c.get("domain", "")]
                                cookie_names = [c.get("name", "") for c in ks_cookies]
                                if not any(n in cookie_names for n in ("userId", "kuaishou.server.web_st")):
                                    need_login = True
                                    print("[Playwright] 未检测到登录态，将打开二维码登录页")
                    except Exception:
                        # 页面 JS 执行失败，可能需要登录
                        need_login = True
                        print("[Playwright] 页面状态异常，将打开二维码登录页")

            # ── 需要登录：跳转到二维码登录页 ──
            if need_login:
                print("[Playwright] 正在打开快手二维码登录页面...")
                page.goto(KS_LOGIN_URL, wait_until="networkidle", timeout=60000)
                # 等待二维码元素出现
                try:
                    page.wait_for_selector(
                        "img[src*='qrcode'], .qrcode-img, .login-panel, [class*='qrcode']",
                        timeout=15000,
                    )
                except Exception:
                    pass
                page.wait_for_timeout(2000)
                prev_url["value"] = page.url
                # 等待用户扫码，最长 120 秒
                for wait_i in range(24):
                    page.wait_for_timeout(5000)
                    if login_detected["value"]:
                        login_detected["value"] = False
                        result_data.clear()
                        print("[Playwright] 登录成功！正在跳转回直播间...")
                        # 登录成功，跳转回直播间
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        prev_url["value"] = page.url
                        break
                else:
                    print("[Playwright] 等待登录超时（120秒），尝试继续解析...")

            # ── 等待足够长时间，让页面加载完成 + 用户可能过验证码 ──
            # 最长等 60 秒
            for i in range(12):
                page.wait_for_timeout(5000)

                # 检测登录成功 → 自动刷新直播间页面
                if login_detected["value"]:
                    login_detected["value"] = False  # 防止重复刷新
                    result_data.clear()  # 清空旧数据
                    print("[Playwright] 检测到登录成功，正在刷新直播间页面...")
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    prev_url["value"] = page.url
                    # 刷新后再等一轮，让数据加载
                    for j in range(6):
                        page.wait_for_timeout(5000)
                        # 检查是否有直播流数据
                        for resp_url, data in result_data.items():
                            if isinstance(data, dict):
                                live_stream = data.get("liveStream")
                                if not live_stream:
                                    dd = data.get("data", {})
                                    if isinstance(dd, dict):
                                        live_stream = dd.get("liveStream")
                                if live_stream and isinstance(live_stream, dict) and live_stream.get("playUrls"):
                                    streams = _ks_parse_playurls_adaptation(live_stream.get("playUrls"))
                                    if not streams:
                                        streams = _ks_parse_livestream(live_stream)
                                    if streams:
                                        context.close()
                                        return {
                                            "platform": "快手",
                                            "title": live_stream.get("userEid", room_id),
                                            "uploader": live_stream.get("userEid", room_id),
                                            "is_live": True,
                                            "streams": streams,
                                            "method": "Playwright浏览器解析",
                                        }
                    break

                # 检查是否有直播流数据
                for resp_url, data in result_data.items():
                    if "captcha" in page.url.lower():
                        continue

                    live_stream = None
                    if isinstance(data, dict):
                        live_stream = data.get("liveStream")
                        if not live_stream:
                            dd = data.get("data", {})
                            if isinstance(dd, dict):
                                live_stream = dd.get("liveStream")

                    if live_stream and isinstance(live_stream, dict) and live_stream.get("playUrls"):
                        streams = _ks_parse_playurls_adaptation(live_stream.get("playUrls"))
                        if not streams:
                            streams = _ks_parse_livestream(live_stream)
                        if streams:
                            context.close()
                            return {
                                "platform": "快手",
                                "title": live_stream.get("userEid", room_id),
                                "uploader": live_stream.get("userEid", room_id),
                                "is_live": True,
                                "streams": streams,
                                "method": "Playwright浏览器解析",
                            }

                # 检查页面状态
                if "captcha" not in page.url.lower():
                    state = page.evaluate("""() => {
                        if (window.__INITIAL_STATE__) return window.__INITIAL_STATE__;
                        return null;
                    }""")
                    if state:
                        playlist = state.get("liveroom", {}).get("playList", [])
                        if playlist:
                            item = playlist[0]
                            err = item.get("errorType") or {}
                            ls = item.get("liveStream", {})

                            # 检测到"请求过快"风控，自动等待后刷新重试
                            if err.get("type"):
                                err_type = err.get("type")
                                print(f"[Playwright] 检测到风控 errorType={err_type}")
                                # 等待 3~5 秒后自动刷新页面
                                wait_sec = random.randint(3, 5)
                                print(f"[Playwright] 等待 {wait_sec} 秒后自动刷新页面...")
                                page.wait_for_timeout(wait_sec * 1000)
                                page.reload(wait_until="domcontentloaded", timeout=30000)
                                result_data.clear()
                                prev_url["value"] = page.url
                                # 刷新后再等一轮让数据加载
                                for _j in range(4):
                                    page.wait_for_timeout(5000)
                                    for resp_url, data in result_data.items():
                                        if isinstance(data, dict):
                                            _ls = data.get("liveStream")
                                            if not _ls:
                                                _dd = data.get("data", {})
                                                if isinstance(_dd, dict):
                                                    _ls = _dd.get("liveStream")
                                            if _ls and isinstance(_ls, dict) and _ls.get("playUrls"):
                                                _streams = _ks_parse_playurls_adaptation(_ls.get("playUrls"))
                                                if not _streams:
                                                    _streams = _ks_parse_livestream(_ls)
                                                if _streams:
                                                    context.close()
                                                    return {
                                                        "platform": "快手",
                                                        "title": _ls.get("userEid", room_id),
                                                        "uploader": _ls.get("userEid", room_id),
                                                        "is_live": True,
                                                        "streams": _streams,
                                                        "method": "Playwright浏览器解析",
                                                    }
                                continue  # 如果刷新后仍无数据，继续主循环等待

                            if ls and isinstance(ls, dict) and ls.get("playUrls"):
                                streams = _ks_parse_playurls_adaptation(ls.get("playUrls"))
                                if not streams:
                                    streams = _ks_parse_livestream(ls)
                                if streams:
                                    author = item.get("author", {})
                                    context.close()
                                    return {
                                        "platform": "快手",
                                        "title": author.get("name", ""),
                                        "uploader": author.get("name", ""),
                                        "is_live": True,
                                        "streams": streams,
                                        "method": "Playwright浏览器解析",
                                    }

            context.close()

    except Exception as e:
        print(f"[Playwright] 浏览器解析失败: {e}")
        pass

    return None


def fetch_kuaishou(url, proxy=""):
    """
    快手直播专属解析（四策略）
    策略0：livedetail API 直接判断直播状态（最可靠）
    策略1：PC端页面 + livedetail API 联合解析
    策略2：移动端页面提取
    策略3：Playwright 浏览器自动化（风控降级）
    """
    room_id = _ks_extract_room_id(url)
    if not room_id:
        raise Exception("无法从URL中提取快手直播间ID")

    did = "web_" + uuid.uuid4().hex[:24]
    session = make_requests_session(proxy)

    # ─── 预处理：用户名 → 数字 principalId ─────────
    # 如果 room_id 不是纯数字（是用户名），先从 PC 页面提取数字 ID
    principal_id = None
    if not room_id.isdigit():
        try:
            pc_headers = {
                **HEADERS_PC,
                "Referer": "https://live.kuaishou.com/",
                "Cookie": f"did={did}",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            resp = session.get(url, headers=pc_headers, timeout=15, allow_redirects=True)
            if resp.status_code == 200:
                state = _ks_extract_state(resp.text)
                if state:
                    playlist = state.get("liveroom", {}).get("playList", [])
                    if playlist:
                        author = playlist[0].get("author", {})
                        pid = author.get("id")
                        if pid:
                            principal_id = str(pid)
                            # 如果页面直接有直播流，直接返回
                            ls = playlist[0].get("liveStream", {})
                            err = playlist[0].get("errorType")
                            if not err and ls and isinstance(ls, dict) and ls.get("playUrls"):
                                streams = _ks_parse_playurls_adaptation(ls.get("playUrls"))
                                if not streams:
                                    streams = _ks_parse_livestream(ls)
                                if streams:
                                    return {
                                        "platform": "快手",
                                        "title": author.get("name", ""),
                                        "uploader": author.get("name", ""),
                                        "is_live": True,
                                        "streams": streams,
                                        "method": "PC端页面提取",
                                    }
        except Exception:
            pass

    # ─── 策略0：livedetail API 直接检查直播状态 ─────────
    # 优先使用数字 principalId，如果没有则用 room_id
    detail_id = principal_id or room_id
    detail_data = _ks_fetch_livedetail(session, detail_id, url)
    if detail_data and detail_data.get("data"):
        data = detail_data["data"]
        api_result = data.get("result", -1)
        author = data.get("author", {})
        is_living = author.get("living", False)
        live_stream = data.get("liveStream", {})
        author_name = author.get("name", "")

        # result != 1 说明 API 调用失败（如 principalId 无效），不判断直播状态，静默降级
        if api_result != 1:
            pass  # 降级到策略1
        # result==1 且 living==False → 不直接报错，静默降级到后续策略
        # （livedetail API 对未登录/低权限用户可能返回不准确的 living 状态）
        elif not is_living:
            pass  # 降级到策略1，让 PC 页面/Playwright 重新判断
        # 在直播但 playUrls 有内容 → 直接解析
        elif live_stream and live_stream.get("playUrls"):
            streams = _ks_parse_playurls_adaptation(live_stream.get("playUrls"))
            if not streams:
                streams = _ks_parse_livestream(live_stream)
            if streams:
                return {
                    "platform": "快手",
                    "title": author_name,
                    "uploader": author_name,
                    "is_live": True,
                    "streams": streams,
                    "method": "livedetail API",
                }

    # ─── 策略1：PC端页面 + livedetail API 联合 ───────────
    pc_headers = {
        **HEADERS_PC,
        "Referer": "https://live.kuaishou.com/",
        "Cookie": f"did={did}",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        session.get("https://live.kuaishou.com/", headers=pc_headers, timeout=10)
        time.sleep(0.5)
        resp = session.get(url, headers=pc_headers, timeout=15, allow_redirects=True)
        if resp.status_code == 200:
            text = resp.text
            state = _ks_extract_state(text)
            if state:
                playlist = state.get("liveroom", {}).get("playList", [])
                if playlist:
                    err = playlist[0].get("errorType")
                    ls = playlist[0].get("liveStream", {})
                    author = playlist[0].get("author", {})
                    is_living = playlist[0].get("isLiving", False)

                    if ls and isinstance(ls, dict) and ls.get("playUrls"):
                        streams = _ks_parse_livestream(ls)
                        if streams:
                            return {
                                "platform": "快手",
                                "title": author.get("name", ""),
                                "uploader": author.get("name", ""),
                                "is_live": True,
                                "streams": streams,
                                "method": "PC端页面提取",
                            }

                    # 无风控且有 author ID → 尝试 livedetail
                    if not err and author.get("id"):
                        principal_id = author.get("id")
                        time.sleep(0.3)
                        detail_resp = session.get(
                            "https://live.kuaishou.com/live_api/liveroom/livedetail",
                            params={"principalId": principal_id},
                            headers={
                                "Referer": url,
                                "Accept": "application/json, text/plain, */*",
                            },
                            timeout=15,
                        )
                        if detail_resp.status_code == 200:
                            dd = detail_resp.json()
                            ls2 = dd.get("data", {}).get("liveStream", {})
                            if ls2 and ls2.get("playUrls"):
                                streams = _ks_parse_playurls_adaptation(ls2.get("playUrls"))
                                if not streams:
                                    streams = _ks_parse_livestream(ls2)
                                if streams:
                                    a2 = dd.get("data", {}).get("author", {})
                                    return {
                                        "platform": "快手",
                                        "title": a2.get("name", ""),
                                        "uploader": a2.get("name", ""),
                                        "is_live": True,
                                        "streams": streams,
                                        "method": "livedetail API",
                                    }

                    # 风控拦截但仍有 author ID → 尝试 livedetail
                    if err and err.get("type") == 2 and author.get("id"):
                        principal_id = author.get("id")
                        time.sleep(0.3)
                        detail_resp = session.get(
                            "https://live.kuaishou.com/live_api/liveroom/livedetail",
                            params={"principalId": principal_id},
                            headers={
                                "Referer": url,
                                "Accept": "application/json, text/plain, */*",
                            },
                            timeout=15,
                        )
                        if detail_resp.status_code == 200:
                            dd = detail_resp.json()
                            ls2 = dd.get("data", {}).get("liveStream", {})
                            if ls2 and ls2.get("playUrls"):
                                streams = _ks_parse_playurls_adaptation(ls2.get("playUrls"))
                                if not streams:
                                    streams = _ks_parse_livestream(ls2)
                                if streams:
                                    a2 = dd.get("data", {}).get("author", {})
                                    return {
                                        "platform": "快手",
                                        "title": a2.get("name", ""),
                                        "uploader": a2.get("name", ""),
                                        "is_live": True,
                                        "streams": streams,
                                        "method": "livedetail API",
                                    }

    except Exception:
        pass  # 继续尝试

    # ─── 策略2：移动端页面提取 ────────────────────────────
    mobile_headers = {
        **HEADERS_MOBILE,
        "Referer": "https://m.gifshow.com/",
        "Cookie": f"did={did}",
    }
    mobile_url = f"https://m.gifshow.com/fw/live/{room_id}"
    try:
        resp = session.get(mobile_url, headers=mobile_headers, timeout=15, allow_redirects=True)
        if resp.status_code == 200:
            text = resp.text
            for pattern in [
                r'liveStream["\']?\s*:\s*(\{.*?\})\s*,\s*["\']?obfuseData',
                r'"liveStream"\s*:\s*(\{.*?\})\s*,\s*"obfuseData"',
                r'liveStream\s*=\s*(\{.*?\})\s*;',
            ]:
                m = re.search(pattern, text, re.DOTALL)
                if m:
                    try:
                        stream_data = json.loads(m.group(1))
                        if stream_data and stream_data.get("playUrls"):
                            streams = _ks_parse_livestream(stream_data)
                            if streams:
                                return {
                                    "platform": "快手",
                                    "title": "",
                                    "uploader": "",
                                    "is_live": True,
                                    "streams": streams,
                                    "method": "移动端页面提取",
                                }
                    except (json.JSONDecodeError, TypeError):
                        pass
    except Exception:
        pass

    # ─── 策略3：Playwright 浏览器自动化（风控降级）──────
    pw_result = _ks_fetch_via_playwright(url, room_id)
    if pw_result and pw_result.get("streams"):
        return pw_result

    raise Exception(
        "快手专属解析失败。\n"
        "可能原因：\n"
        "  1) 该直播间当前未在直播\n"
        "  2) 快手风控拦截（请求过快）\n"
        "  3) 需要使用代理IP\n"
        "建议：\n"
        "  - 在浏览器中确认直播间是否正在直播\n"
        "  - 添加代理后重试\n"
        "  - 等待1-2分钟后重试"
    )


def _is_system_proxy_on() -> bool:
    """检测 Windows 系统代理是否已启用"""
    try:
        import winreg
        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            return bool(enabled)
    except Exception:
        return False


def _get_current_proxy_server() -> str:
    """获取当前 Windows 系统代理服务器地址"""
    try:
        import winreg
        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            return server or ""
    except Exception:
        return ""


def _ks_parse_livestream(stream_data: dict) -> list:
    """解析快手 liveStream 数据中的播放地址（兼容多种返回格式）"""
    streams = []

    # 格式1：multiResolutionHlsPlayUrls（旧版移动端）
    hls_list = stream_data.get("multiResolutionHlsPlayUrls", [])
    if hls_list:
        for i, hls_item in enumerate(hls_list):
            urls = hls_item.get("urls", [])
            for url_info in urls:
                url = url_info.get("url", "")
                quality_name = hls_item.get("name", f"分辨率{i}")
                if url:
                    streams.append({
                        "quality": quality_name,
                        "format": "M3U8",
                        "url": url,
                        "source": "HLS直播流",
                    })

    # 格式2：playUrls（livedetail API 返回格式）
    # 结构: { h264: { "流畅": {url, qualityType}, "高清": {...} }, hevc: {...} }
    # 或: { h264: { "name1": "url1", "name2": "url2" } }
    play_urls = stream_data.get("playUrls")
    if play_urls and isinstance(play_urls, dict):
        codec_labels = {"h264": "H264", "hevc": "HEVC/H265"}
        for codec, quality_map in play_urls.items():
            if not quality_map or not isinstance(quality_map, dict):
                continue
            codec_label = codec_labels.get(codec, codec.upper())
            for quality_name, url_data in quality_map.items():
                url = ""
                if isinstance(url_data, dict):
                    url = url_data.get("url", "") or url_data.get("completeUrl", "")
                elif isinstance(url_data, str):
                    url = url_data
                if url:
                    streams.append({
                        "quality": f"{quality_name}({codec_label})",
                        "format": guess_format(url),
                        "url": url,
                        "source": f"playUrls-{codec_label}",
                    })

    # 格式3：adaptationSet（旧版PC端）
    adaptation_set = stream_data.get("adaptationSet", [])
    if adaptation_set:
        for item in adaptation_set:
            url = item.get("url", "")
            if url:
                streams.append({
                    "quality": item.get("name", "默认"),
                    "format": guess_format(url),
                    "url": url,
                    "source": "AdaptationSet",
                })

    # 格式4：直接 url 字段（部分旧接口）
    direct_url = stream_data.get("url", "")
    if direct_url and not streams:
        streams.append({
            "quality": "默认",
            "format": guess_format(direct_url),
            "url": direct_url,
            "source": "直接URL",
        })

    return streams


# ─── 抖音 ────────────────────────────────────────────────

def _dy_extract_web_rid(url: str) -> str:
    """从抖音直播URL中提取 web_rid (数字短ID)
    支持格式:
      https://live.douyin.com/886548644476
      https://live.douyin.com/860999028055?activity_name=...&anchor_id=... (带参数长链接)
      https://www.douyin.com/follow/live/886548644476?anchor_id=xxx
      https://www.douyin.com/live/886548644476
    """
    # live.douyin.com/<rid>?... (支持带 ? 参数的完整 URL)
    m = re.search(r'live\.douyin\.com/(\d+)', url)
    if m:
        return m.group(1)
    # www.douyin.com/follow/live/<rid> 或 www.douyin.com/live/<rid>
    m = re.search(r'douyin\.com/(?:follow/)?live/(\d+)', url)
    if m:
        return m.group(1)
    # slug 格式 (live.douyin.com/<slug>)
    m = re.search(r'live\.douyin\.com/([a-zA-Z0-9_-]+)', url)
    if m:
        return m.group(1)
    # 尝试从 anchor_id 参数提取（部分 URL 用 anchor_id 而非路径）
    m = re.search(r'anchor_id=(\d+)', url)
    if m:
        return m.group(1)
    return ""


# ─── 抖音 Playwright 浏览器解析 ─────────────────────────

def _get_dy_browser_data_dir():
    """获取抖音浏览器持久化缓存目录（cookie / session / localStorage）"""
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(base, "LiveStreamFetcher", "douyin_browser_data")
    os.makedirs(d, exist_ok=True)
    return d


def _check_dy_login_status():
    """检测抖音浏览器持久化目录中是否存在有效的登录 Cookie。

    通过直接读取 SQLite 数据库，检查是否含有 .douyin.com 域名下的关键登录 Cookie。
    关键 cookie 名称：sessionid / sid_guard / uid_tt / passport_csrf_token 等，
    只有这些认证类 cookie 存在才真正表示已登录（避免 ttwid 等非登录 cookie 导致误判）。
    返回:
        "logged_in"  - 检测到有效登录 Cookie
        "never"      - 未检测到登录 Cookie
    """
    import sqlite3

    data_dir = _get_dy_browser_data_dir()
    default_dir = os.path.join(data_dir, "Default")
    if not os.path.isdir(default_dir):
        return "never"

    # 抖音关键登录认证 cookie（只有这些存在才说明真正登录了）
    dy_auth_cookie_names = ("sessionid", "sid_guard", "uid_tt", "passport_csrf_token",
                             "sid_client", "odin_tt")

    # 在两个可能的 Cookie 存储位置中检查
    cookie_paths = [
        os.path.join(default_dir, "Cookies"),
        os.path.join(default_dir, "Network", "Cookies"),
    ]

    for db_path in cookie_paths:
        if not os.path.isfile(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            # 方案1：精确匹配 .douyin.com 域名 + 关键认证 cookie 名称
            for name in dy_auth_cookie_names:
                cursor.execute(
                    "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%.douyin.com' AND name=?",
                    (name,),
                )
                if cursor.fetchone()[0] > 0:
                    conn.close()
                    return "logged_in"
            # 方案2：兜底——至少有 .douyin.com 域名的 sessionid 类 cookie
            cursor.execute(
                "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%.douyin.com' "
                "AND (name LIKE 'session%' OR name='sid_guard' OR name LIKE 'uid_tt%' OR name LIKE 'odin_%')"
            )
            count = cursor.fetchone()[0]
            conn.close()
            if count > 0:
                return "logged_in"
        except Exception:
            pass

    # 没有找到任何有效的抖音登录 Cookie
    return "never"


def _clear_dy_cookies():
    """删除抖音浏览器整个持久化目录，强制重新登录。"""
    import shutil
    data_dir = _get_dy_browser_data_dir()
    if not os.path.exists(data_dir):
        return True
    try:
        shutil.rmtree(data_dir)
        return True
    except Exception:
        return False


def _dy_fetch_via_playwright(url: str) -> dict:
    """通过 Playwright 浏览器自动化获取抖音直播流。

    抖音 2025-2026 年反爬升级：
    - webcast/room/web/enter API 需要签名（_signature / X-Bogus）
    - 纯请求方式获取 ttwid + 调 API 的成功率大幅下降
    - 部分直播间返回"无房间信息"（实际是 API 被风控拦截）

    策略：
    1. 使用 persistent_context 保持登录态（cookie / session 持久化到本地）
    2. 用非 headless 模式打开浏览器（让用户可以手动操作）
    3. 打开抖音直播间页面，监听 webcast room API 和 m3u8/flv 流请求
    4. 从拦截到的数据中提取直播流地址
    5. 未登录时跳转抖音扫码/验证码登录页
    6. 登录成功后自动刷新直播间页面
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[抖音Playwright] playwright 未安装，跳过浏览器解析")
        return None

    try:
        with sync_playwright() as p:
            user_data_dir = _get_dy_browser_data_dir()
            print(f"[抖音Playwright] 使用浏览器缓存目录: {user_data_dir}")

            # ── 浏览器启动参数 ──
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "no_viewport": False,
            }

            # ── 启动浏览器（优先嵌入式 Chromium → Playwright Chromium → Chrome → Edge）──
            launch_errors = []
            context = None

            embedded_chromium = _ensure_chromium_ready()
            if embedded_chromium:
                try:
                    print(f"[抖音Playwright] 使用嵌入式 Chromium: {embedded_chromium}")
                    context = p.chromium.launch_persistent_context(
                        user_data_dir,
                        executable_path=os.path.join(embedded_chromium, "chrome.exe"),
                        **launch_kwargs,
                    )
                except Exception as e_embed:
                    launch_errors.append(f"Embedded Chromium: {e_embed}")
                    print(f"[抖音Playwright] 嵌入式 Chromium 启动失败: {e_embed}")

            if not context:
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir, channel=None, **launch_kwargs,
                    )
                except Exception as e1:
                    launch_errors.append(f"Chromium: {e1}")
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir, channel="chrome", **launch_kwargs,
                        )
                    except Exception as e2:
                        launch_errors.append(f"Chrome: {e2}")
                        try:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="msedge", **launch_kwargs,
                            )
                        except Exception as e3:
                            launch_errors.append(f"Edge: {e3}")

            if not context:
                print(f"[抖音Playwright] 无法启动浏览器: {'; '.join(launch_errors)}")
                return None

            page = context.pages[0] if context.pages else context.new_page()

            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
            """)

            result_data = {}

            def on_response(response):
                resp_url = response.url
                # 监听抖音直播间相关 API + 流媒体请求
                if any(k in resp_url for k in [
                    "webcast/room",
                    "webcast/im",
                    ".m3u8", ".flv",
                    "pull.f.muscdn.com",
                    "flv-livesl.douyinvod.com",
                ]):
                    try:
                        ct = response.body()
                        if ct and len(ct) < 500000:
                            result_data[resp_url] = json.loads(ct.decode("utf-8", errors="replace"))
                    except Exception:
                        pass

            page.on("response", on_response)

            # ── 登录检测：监听页面跳转 ──
            login_detected = {"value": False}
            prev_url = {"value": page.url if page.url.startswith("http") else ""}

            def on_navigate(navigation):
                try:
                    nav_url = navigation.url
                    if not nav_url or nav_url == "about:blank":
                        return
                    # 检测登录成功：从登录页面跳出
                    old_prev = prev_url.get("value", "")
                    if ("passport" in old_prev or "login" in old_prev.lower()):
                        if "passport" not in nav_url and "login" not in nav_url.lower():
                            login_detected["value"] = True
                            print(f"[抖音Playwright] 检测到登录成功，将自动刷新直播间页面")
                    prev_url["value"] = nav_url
                except Exception:
                    pass

            page.on("framenavigated", on_navigate)

            # ── 检查 cookie 判断是否已登录 ──
            already_logged_in = False
            try:
                cookies = context.cookies()
                dy_cookies = [c for c in cookies if "douyin" in c.get("domain", "") or "bytedance" in c.get("domain", "")]
                cookie_names = [c.get("name", "") for c in dy_cookies]
                # sessionid 或 passport_csrf_token 存在说明已登录
                if any(n in cookie_names for n in ("sessionid", "sid_guard", "passport_csrf_token", "odin_tt")):
                    already_logged_in = True
                    print("[抖音Playwright] 检测到已有抖音登录态，直接访问直播间")
            except Exception:
                pass

            # ── 访问直播间页面 ──
            DY_LIVE_URL = url
            page.goto(DY_LIVE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            need_login = False
            if not already_logged_in:
                current_url = page.url
                # 检查是否被重定向到了登录页面
                if "sso.douyin.com" in current_url or "passport" in current_url.lower():
                    need_login = True
                    print("[抖音Playwright] 被重定向到登录页面，等待用户操作...")
                else:
                    # 检查页面是否有登录提示（通过 SSR state 检测）
                    try:
                        state = page.evaluate("""() => {
                            if (window.__RENDER_DATA__) return window.__RENDER_DATA__;
                            return null;
                        }""")
                        if not state:
                            cookies = context.cookies()
                            dy_cookies = [c for c in cookies if "douyin" in c.get("domain", "") or "bytedance" in c.get("domain", "")]
                            if not dy_cookies:
                                need_login = True
                                print("[抖音Playwright] 未检测到登录态，尝试继续解析（游客模式）")
                    except Exception:
                        pass

            # ── 需要登录：等待用户扫码 ──
            if need_login:
                print("[抖音Playwright] 等待用户扫码登录...")
                prev_url["value"] = page.url
                # 等待用户扫码，最长 120 秒
                for wait_i in range(24):
                    page.wait_for_timeout(5000)
                    if login_detected["value"]:
                        login_detected["value"] = False
                        result_data.clear()
                        print("[抖音Playwright] 登录成功！正在跳转回直播间...")
                        page.goto(DY_LIVE_URL, wait_until="domcontentloaded", timeout=30000)
                        prev_url["value"] = page.url
                        break
                else:
                    print("[抖音Playwright] 等待登录超时（120秒），尝试继续解析...")

            # ── 等待足够长时间让页面加载 + 用户过验证码 ──
            # 最长等 60 秒（12轮 × 5秒）
            for i in range(12):
                page.wait_for_timeout(5000)

                # 检测登录成功 → 自动刷新
                if login_detected["value"]:
                    login_detected["value"] = False
                    result_data.clear()
                    print("[抖音Playwright] 检测到登录成功，正在刷新直播间页面...")
                    page.goto(DY_LIVE_URL, wait_until="domcontentloaded", timeout=30000)
                    prev_url["value"] = page.url
                    # 刷新后再等一轮
                    for j in range(6):
                        page.wait_for_timeout(5000)
                        streams = _dy_try_extract_from_result(result_data)
                        if streams:
                            title, uploader = _dy_get_page_info(page)
                            context.close()
                            return {
                                "platform": "抖音",
                                "title": title,
                                "uploader": uploader,
                                "is_live": True,
                                "streams": streams,
                                "method": "Playwright浏览器解析",
                            }
                    continue

                # 尝试从已收集的数据中提取流
                streams = _dy_try_extract_from_result(result_data)
                if streams:
                    title, uploader = _dy_get_page_info(page)
                    context.close()
                    return {
                        "platform": "抖音",
                        "title": title,
                        "uploader": uploader,
                        "is_live": True,
                        "streams": streams,
                        "method": "Playwright浏览器解析",
                    }

                # 尝试从页面 __INITIAL_STATE__ / __RENDER_DATA__ 中提取
                streams = _dy_try_extract_from_page(page)
                if streams:
                    context.close()
                    return {
                        "platform": "抖音",
                        "title": "",
                        "uploader": "",
                        "is_live": True,
                        "streams": streams,
                        "method": "Playwright浏览器解析(SSR)",
                    }

            context.close()

    except Exception as e:
        print(f"[抖音Playwright] 异常: {e}")
        import traceback
        traceback.print_exc()

    return None


def _dy_try_extract_from_result(result_data: dict) -> list:
    """从 Playwright 收集的响应数据中提取抖音直播流"""
    streams = []

    # 方式1：从 webcast/room API 响应中提取
    for resp_url, data in result_data.items():
        if not isinstance(data, dict):
            continue
        # 直接找 stream_url 结构
        stream_url = data.get("stream_url") or data.get("data", {}).get("stream_url")
        if stream_url and isinstance(stream_url, dict):
            parsed = _dy_parse_stream_url(stream_url)
            if parsed:
                streams.extend(parsed)

        # 找 data.data 数组结构（webcast/room/web/enter 格式）
        room_list = data.get("data", {}).get("data", [])
        if isinstance(room_list, list) and room_list:
            room = room_list[0]
            su = room.get("stream_url", {})
            if su:
                parsed = _dy_parse_stream_url(su)
                if parsed:
                    streams.extend(parsed)

    # 方式2：从 .m3u8/.flv 原始 URL 提取
    for resp_url, data in result_data.items():
        if not isinstance(data, dict):
            continue
        # 有些流数据直接就是 URL 字符串或包含 flv/hls 地址
        if ".m3u8" in resp_url or ".flv" in resp_url:
            fmt = "M3U8" if ".m3u8" in resp_url else "FLV"
            if resp_url not in [s.get("url", "") for s in streams]:
                streams.append({
                    "quality": "原画",
                    "format": fmt,
                    "url": resp_url,
                    "source": "网络拦截",
                })

    return streams


def _dy_try_extract_from_page(page) -> list:
    """从抖音页面 JS 变量中提取直播流（SSR 渲染数据）"""
    try:
        render_data = page.evaluate("""() => {
            // 尝试获取 RENDER_DATA（SSR 数据）
            if (window.__RENDER_DATA__) {
                try {
                    var decoded = decodeURIComponent(window.__RENDER_DATA__);
                    return JSON.parse(decoded);
                } catch(e) {}
            }
            // 尝试 INITIAL_STATE
            if (window.__INITIAL_STATE__) {
                return window.__INITIAL_STATE__;
            }
            return null;
        }""")
    except Exception:
        return []

    if not render_data or not isinstance(render_data, dict):
        return []

    streams = []
    # 深度搜索 stream_url
    su = _deep_search_key(render_data, "stream_url")
    if su and isinstance(su, dict):
        streams = _dy_parse_stream_url(su)

    # 备用：搜索 pull_url / flv_pull_url / hls_pull_url
    if not streams:
        for key in ["pull_url", "flv_pull_url", "hls_pull_url_map"]:
            val = _deep_search_key(render_data, key)
            if val and isinstance(val, dict):
                for qk, qv in val.items():
                    if isinstance(qv, str) and qv.startswith("http"):
                        streams.append({
                            "quality": qk,
                            "format": guess_format(qv),
                            "url": qv,
                            "source": f"SSR.{key}",
                        })

    return streams


def _dy_get_page_info(page) -> tuple:
    """从抖音页面提取标题和主播名"""
    title = ""
    uploader = ""
    try:
        info = page.evaluate("""() => {
            // 尝试从 SSR 数据获取
            if (window.__RENDER_DATA__) {
                try { var d = JSON.parse(decodeURIComponent(window.__RENDER_DATA__)); return d; }
                catch(e) {}
            }
            if (window.__INITIAL_STATE__) return window.__INITIAL_STATE__;
            return null;
        }""")
        if info and isinstance(info, dict):
            # 尝试多种路径找标题
            for path in ["title", "roomInfo.title", "room.name"]:
                parts = path.split(".")
                obj = info
                found = True
                for part in parts:
                    if isinstance(obj, dict) and part in obj:
                        obj = obj[part]
                    else:
                        found = False
                        break
                if found and isinstance(obj, str):
                    title = obj
                    break
            # 找主播名
            owner = info.get("owner", {}) or info.get("anchorInfo", {}) or info.get("roomOwner", {})
            uploader = owner.get("nickname", "") or owner.get("name", "") or ""
    except Exception:
        # 从页面 <title> 标签兜底
        try:
            title_text = page.title() or ""
            if "抖音直播" in title_text or "直播" in title_text:
                title = title_text.replace(" - 抖音直播", "").replace(" - 抖音", "")
        except Exception:
            pass

    return title or "抖音直播", uploader or "未知"


def _dy_parse_stream_url(stream_url: dict) -> list:
    """解析抖音 stream_url 对象中的所有流地址"""
    streams = []
    if not stream_url or not isinstance(stream_url, dict):
        return streams

    # FLV 流
    flv_pull = stream_url.get("flv_pull_url", {})
    if isinstance(flv_pull, dict):
        for quality_name, url in flv_pull.items():
            if isinstance(url, str) and url.startswith("http"):
                streams.append({
                    "quality": quality_name,
                    "format": "FLV",
                    "url": url,
                    "source": "FLV直播流",
                })

    # HLS 流 (m3u8)
    hls_pull = stream_url.get("hls_pull_url_map", {})
    if isinstance(hls_pull, dict):
        for quality_name, url in hls_pull.items():
            if isinstance(url, str) and url.startswith("http"):
                streams.append({
                    "quality": quality_name,
                    "format": "M3U8",
                    "url": url,
                    "source": "HLS直播流",
                })

    # 备用：遍历所有 key
    quality_map = {
        "FULL_HD1": "原画",
        "HD1": "高清",
        "SD1": "标清",
        "SD2": "流畅",
        "origin": "原画",
        "uhd": "超高清",
        "hd": "高清",
        "sd": "标清",
        "ld": "流畅",
    }
    for key, val in stream_url.items():
        if key in ("flv_pull_url", "hls_pull_url_map"):
            continue
        if isinstance(val, dict):
            for qk, qv in val.items():
                if isinstance(qv, str) and qv.startswith("http"):
                    if qv not in [s["url"] for s in streams]:
                        streams.append({
                            "quality": quality_map.get(qk, qk),
                            "format": guess_format(qv),
                            "url": qv,
                            "source": f"stream_url.{key}",
                        })

    return streams


def fetch_douyin(url: str, proxy: str = "") -> dict:
    """
    抖音直播解析 v5（Playwright 优先）

    策略：
    1. **优先**：使用 Playwright 浏览器自动化打开直播间页面
       - 和快手/小红书/淘宝一致，通过真实浏览器环境获取流地址
       - 支持登录态持久化，未登录自动弹出扫码页
       - 监听 webcast room API 响应和 m3u8/flv 流请求
    2. **降级**：如果 Playwright 不可用，回退到旧版 API 方式（ttwid + webcast/room）
    """
    # ── Step 1: 尝试 Playwright 浏览器解析 ──
    print("[抖音] 正在使用 Playwright 浏览器解析...")
    pw_result = _dy_fetch_via_playwright(url)
    if pw_result and pw_result.get("streams"):
        print(f"[抖音] Playwright 解析成功！获取到 {len(pw_result['streams'])} 个流")
        return pw_result

    # Playwright 返回了结果但无流（可能是直播间未开播）
    if pw_result is not None:
        raise Exception(
            "浏览器已成功打开直播间，但未检测到直播流。\n"
            "\n可能原因："
            "\n  - 直播间尚未开始直播或已结束"
            "\n  - 页面加载超时（请稍后重试）"
            "\n  - 需要登录才能观看此直播间（点击状态栏「抖音未登录」扫码登录）"
        )

    print("[抖音] Playwright 解析未返回结果，尝试 API 方式...")

    # ── Step 2: 降级到旧版 API 方式 ──
    web_rid = _dy_extract_web_rid(url)
    if not web_rid:
        raise Exception("无法从URL中提取抖音直播间ID")

    session = make_requests_session(proxy)

    # Step 1: 访问页面获取 cookie (ttwid)
    # ttwid 由 live.douyin.com 域名下发，所以访问页面必须走 live.douyin.com
    # 如果传入的是 www.douyin.com/follow/live/ 格式，先访问原始 URL（可能重定向），
    # 然后再访问 live.douyin.com/<web_rid> 确保拿到 ttwid
    page_headers = {
        **HEADERS_PC,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    live_page_url = f"https://live.douyin.com/{web_rid}"
    # 如果原始 URL 不在 live.douyin.com，先访问原始 URL（拿通用 cookie），再访问 live 域
    if "live.douyin.com" not in url:
        try:
            session.get(url, headers=page_headers, timeout=15, allow_redirects=True)
        except requests.RequestException:
            pass  # 忽略，继续用 live.douyin.com

    try:
        resp = session.get(live_page_url, headers=page_headers, timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            raise Exception(f"抖音页面请求返回状态码 {resp.status_code}")
    except requests.RequestException as e:
        raise Exception(f"抖音页面请求失败: {e}")

    # 获取 ttwid cookie
    ttwid = session.cookies.get("ttwid", "")
    if not ttwid:
        raise Exception("未能获取抖音 ttwid cookie，可能被反爬限制")

    # Step 2: 调用 webcast room enter API
    api_url = (
        f"https://live.douyin.com/webcast/room/web/enter/?"
        f"web_rid={web_rid}"
        f"&aid=6383&live_id=1"
        f"&device_platform=web&language=zh-CN"
        f"&browser_language=zh-CN&browser_platform=Win32"
        f"&browser_name=Chrome&browser_version=125.0.0.0"
    )

    api_headers = {
        **HEADERS_PC,
        "Accept": "application/json, text/plain, */*",
        "Referer": live_page_url,
        "Cookie": f"ttwid={ttwid}",
    }

    streams = []
    title = ""
    uploader = ""
    status = 0

    try:
        resp_api = session.get(api_url, headers=api_headers, timeout=10)
        if resp_api.status_code != 200:
            raise Exception(f"API请求返回状态码 {resp_api.status_code}")

        data = resp_api.json()

        # 解析返回数据
        room_list = data.get("data", {}).get("data", [])
        if not room_list:
            raise Exception("API返回数据中无房间信息，可能直播间不存在或已结束")

        room = room_list[0]
        status = int(room.get("status", 0))
        title = room.get("title", "")
        owner = room.get("owner", {})
        uploader = owner.get("nickname", "")

        # 解析流地址
        stream_url = room.get("stream_url", {})
        streams = _dy_parse_stream_url(stream_url)

        # 如果没有流，尝试从备用字段获取
        if not streams:
            # 尝试 pull_url 字段
            for pull_key in ["pull_url", "pull_urls", "stream_urls"]:
                pull_data = room.get(pull_key, {})
                if isinstance(pull_data, dict):
                    for qk, qv in pull_data.items():
                        if isinstance(qv, str) and qv.startswith("http"):
                            streams.append({
                                "quality": qk,
                                "format": guess_format(qv),
                                "url": qv,
                                "source": pull_key,
                            })
                elif isinstance(pull_data, str) and pull_data.startswith("http"):
                    streams.append({
                        "quality": "默认",
                        "format": guess_format(pull_data),
                        "url": pull_data,
                        "source": pull_key,
                    })
                if streams:
                    break

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise Exception(f"抖音API数据解析失败: {e}")
    except requests.RequestException as e:
        raise Exception(f"抖音API请求失败: {e}")

    if not streams:
        if status != 2:
            raise Exception(
                f"当前直播间状态非直播中(status={status})。\n"
                f"可能原因：直播未开始或已结束"
            )
        raise Exception("抖音专属解析成功获取房间信息，但未找到流地址")

    return {
        "platform": "抖音",
        "title": title,
        "uploader": uploader,
        "is_live": status == 2,
        "streams": streams,
        "method": "抖音webcast API",
    }


def _deep_search_key(data: dict, target_key: str, depth: int = 5) -> dict:
    """深度搜索字典中含目标key的值"""
    if depth <= 0 or not isinstance(data, dict):
        return None
    if target_key in data:
        return data[target_key]
    for k, v in data.items():
        if isinstance(v, dict):
            result = _deep_search_key(v, target_key, depth - 1)
            if result:
                return result
    return None


# ─── 小红书 ───────────────────────────────────────────────

def _get_xhs_browser_data_dir():
    """获取小红书浏览器持久化缓存目录（cookie / session / localStorage）"""
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(base, "LiveStreamFetcher", "xiaohongshu_browser_data")
    os.makedirs(d, exist_ok=True)
    return d


def _check_xhs_login_status():
    """检测小红书浏览器持久化目录中是否存在有效的登录 Cookie。

    通过直接读取 SQLite 数据库，检查是否含有 xiaohongshu.com 域名的 Cookie。
    返回:
        "logged_in"  - 检测到有效登录 Cookie
        "expired"    - 目录存在但无有效 Cookie（可能过期）
        "never"      - 目录不存在，从未登录过
    """
    import sqlite3
    data_dir = _get_xhs_browser_data_dir()
    cookies_path = os.path.join(data_dir, "Default", "Cookies")

    if not os.path.exists(data_dir):
        return "never"

    if os.path.exists(cookies_path):
        try:
            conn = sqlite3.connect(cookies_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%xiaohongshu%'"
            )
            count = cursor.fetchone()[0]
            conn.close()
            if count > 0:
                return "logged_in"
        except Exception:
            pass

    # 也检查 Network/Cookies（部分 Chromium 版本）
    network_cookies = os.path.join(data_dir, "Default", "Network", "Cookies")
    if os.path.exists(network_cookies):
        try:
            conn = sqlite3.connect(network_cookies)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%xiaohongshu%'"
            )
            count = cursor.fetchone()[0]
            conn.close()
            if count > 0:
                return "logged_in"
        except Exception:
            pass

    # 目录存在但无有效 Cookie
    return "expired"


def _clear_xhs_cookies():
    """删除小红书浏览器整个持久化目录，强制重新登录。"""
    import shutil
    data_dir = _get_xhs_browser_data_dir()
    if not os.path.exists(data_dir):
        return True
    try:
        shutil.rmtree(data_dir)
        return True
    except Exception:
        return False


def _xhs_fetch_via_playwright(url: str) -> dict:
    """通过 Playwright 浏览器自动化获取小红书直播流。

    小红书 2025+ 反爬升级：
    - 直播间页面需要登录态才能获取流地址（游客/未登录无法获取 pullConfig）
    - __INITIAL_STATE__ 中 roomData.roomInfo.pullConfig 为 null（SSR 占位）
    - 真正的流数据通过 edith.xiaohongshu.com API 动态请求或 Vue Pinia store 获取

    策略：
    1. 使用 persistent_context 保持登录态（cookie / session 持久化到本地）
    2. 打开直播间后，通过 API 检查是否为游客身份（guest=true）
    3. 如果未登录，弹出浏览器让用户扫码登录，登录后自动刷新
    4. 监听所有 edith API 响应，从响应中提取直播流地址
    5. 从页面 Vue Pinia store 获取 roomData.roomInfo.pullConfig
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[小红书Playwright] playwright 未安装，跳过浏览器解析")
        return None

    try:
        with sync_playwright() as p:
            user_data_dir = _get_xhs_browser_data_dir()
            print(f"[小红书Playwright] 使用浏览器缓存目录: {user_data_dir}")

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "no_viewport": False,
            }

            launch_errors = []
            context = None

            embedded_chromium = _ensure_chromium_ready()
            if embedded_chromium:
                try:
                    print(f"[小红书Playwright] 使用嵌入式 Chromium: {embedded_chromium}")
                    context = p.chromium.launch_persistent_context(
                        user_data_dir,
                        executable_path=os.path.join(embedded_chromium, "chrome.exe"),
                        **launch_kwargs,
                    )
                except Exception as e_embed:
                    launch_errors.append(f"Embedded Chromium: {e_embed}")
                    print(f"[小红书Playwright] 嵌入式 Chromium 启动失败: {e_embed}")

            if not context:
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir, channel=None, **launch_kwargs,
                    )
                except Exception as e1:
                    launch_errors.append(f"Chromium: {e1}")
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir, channel="chrome", **launch_kwargs,
                        )
                    except Exception as e2:
                        launch_errors.append(f"Chrome: {e2}")
                        try:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="msedge", **launch_kwargs,
                            )
                        except Exception as e3:
                            launch_errors.append(f"Edge: {e3}")

            if not context:
                print(f"[小红书Playwright] 无法启动浏览器: {'; '.join(launch_errors)}")
                return None

            page = context.pages[0] if context.pages else context.new_page()

            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
            """)

            result_data = {}  # url -> latest parsed JSON
            result_data_list = []  # [(url, parsed_json), ...] 保留所有历史
            title_info = {"title": "", "uploader": ""}

            def on_response(response):
                resp_url = response.url
                # 拦截所有 edith API 响应（小红书所有业务 API）
                if "edith.xiaohongshu.com" in resp_url:
                    try:
                        ct = response.body()
                        if ct and len(ct) < 2000000:
                            parsed = json.loads(ct.decode("utf-8", errors="replace"))
                            result_data[resp_url] = parsed
                            result_data_list.append((resp_url, parsed))
                            # 只打印关键 API 或包含直播信息的 API
                            is_important = any(k in resp_url for k in [
                                "user/me", "qrcode", "liveStream", "roomInfo",
                                "live/info", "live/room", "homefeed", "getRoomInfo"
                            ])
                            if is_important:
                                print(f"[小红书Playwright] 捕获API: {resp_url[:120]}... ({len(ct)} bytes)")
                                # 如果是 user/me，打印关键信息
                                if "user/me" in resp_url and isinstance(parsed, dict):
                                    data = parsed.get("data", {})
                                    print(f"[小红书Playwright]   user/me: guest={data.get('guest')}, user_id={data.get('user_id')}, nickname={data.get('nickname')}")
                    except Exception:
                        pass

            page.on("response", on_response)

            # ── 检查登录状态（通过已捕获的 API 响应）──
            def check_login_via_api():
                """从已捕获的 on_response 数据中检查是否已登录，返回 (is_logged_in, user_info)"""
                # 从后往前遍历（最新的 user/me 优先）
                for resp_url, resp_data in reversed(result_data_list):
                    if "/api/sns/web/v2/user/me" in resp_url and isinstance(resp_data, dict):
                        data = resp_data.get("data", {})
                        if not data:
                            continue
                        guest = data.get("guest", True)
                        user_id = data.get("user_id", "")
                        nickname = data.get("nickname", "")
                        if not guest and bool(user_id):
                            print(f"[小红书Playwright] 确认已登录: user_id={user_id}, nickname={nickname}")
                            return True, resp_data
                        else:
                            print(f"[小红书Playwright] user/me 显示未登录: guest={guest}, user_id={user_id}")
                            return False, resp_data
                print("[小红书Playwright] 未捕获到 user/me 响应，默认未登录")
                return False, None

            # ── 打开直播间页面 ──
            print(f"[小红书Playwright] 正在打开直播间: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)  # 等待 JS 加载

            # ── 检查是否需要登录 ──
            is_logged_in, user_resp = check_login_via_api()
            print(f"[小红书Playwright] 登录状态: {'已登录' if is_logged_in else '未登录/游客'}")
            if user_resp:
                print(f"[小红书Playwright] 用户信息: {user_resp.get('data', {})}")

            if not is_logged_in:
                # ── 需要登录：跳转到小红书首页让用户扫码 ──
                print("[小红书Playwright] 正在打开小红书登录页面，请扫码登录...")
                login_url = "https://www.xiaohongshu.com"
                page.goto(login_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)

                # 等待登录相关元素出现
                try:
                    page.wait_for_selector(
                        "img[src*='qrcode'], .qrcode-img, .login-panel, [class*='qrcode'], [class*='login']",
                        timeout=15000,
                    )
                except Exception:
                    pass
                page.wait_for_timeout(2000)

                # 等待用户扫码登录，最长 120 秒
                login_success = False
                for wait_i in range(24):
                    page.wait_for_timeout(5000)
                    is_logged_now, _ = check_login_via_api()
                    if is_logged_now:
                        login_success = True
                        print("[小红书Playwright] 登录成功！正在跳转回直播间...")
                        break

                if login_success:
                    # 刷新直播间页面，清空之前捕获的数据
                    result_data.clear()
                    result_data_list.clear()
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(5000)
                else:
                    print("[小红书Playwright] 等待登录超时（120秒），尝试继续解析...")

            # ── 等待直播流数据 ──
            print("[小红书Playwright] 正在监听网络请求，等待直播流数据...")
            for i in range(20):  # 20 次，共 80 秒
                page.wait_for_timeout(4000)

                # 每 20 秒打印一次状态
                if i % 5 == 4:
                    print(f"[小红书Playwright] 等待中... 已等待 {(i+1)*4} 秒, 捕获 {len(result_data)} 个API响应")

                # 从拦截到的 API 数据提取流地址
                streams = _xhs_try_extract_streams(result_data, title_info, page)
                if streams:
                    context.close()
                    return {
                        "platform": "小红书",
                        "title": title_info.get("title", ""),
                        "uploader": title_info.get("uploader", ""),
                        "is_live": True,
                        "streams": streams,
                        "method": "Playwright浏览器解析",
                    }

                # 从页面 JS 变量 / video 元素 / Vue store 提取
                try:
                    page_data = page.evaluate("""() => {
                        const result = {};
                        
                        // 1. 检查 video 元素源地址
                        const videos = document.querySelectorAll('video');
                        const sources = [];
                        videos.forEach(v => {
                            if(v.src) sources.push(v.src);
                            if(v.currentSrc) sources.push(v.currentSrc);
                        });
                        if (sources.length > 0) {
                            result.videoSources = sources;
                        }
                        result.videoCount = videos.length;

                        // 2. 从 Vue 3 app 获取 Pinia store（多种方式尝试）
                        try {
                            const appEl = document.querySelector('#app') || document.querySelector('[id]');
                            if (appEl && appEl.__vue_app__) {
                                const app = appEl.__vue_app__;
                                // 方式 A：通过 _context.provides（Vue 3 Pinia 标准）
                                const provides = app._context && app._context.provides;
                                if (provides) {
                                    for (const key of Object.getOwnPropertySymbols(provides)) {
                                        const store = provides[key];
                                        if (store && store.$id === 'liveStream') {
                                            result.vueStore = {
                                                source: 'vue3-pinia-symbol',
                                                storeId: store.$id,
                                                roomDataKeys: store.roomData ? Object.keys(store.roomData) : null,
                                            };
                                            if (store.roomData && store.roomData.roomInfo) {
                                                const ri = store.roomData.roomInfo;
                                                if (ri.pullConfig) {
                                                    result.pullConfig = ri.pullConfig;
                                                    result.roomId = ri.roomId;
                                                    result.roomTitle = ri.roomTitle;
                                                    result.source = 'vue3-pinia-symbol';
                                                }
                                            }
                                        }
                                    }
                                }
                                // 方式 B：通过 globalProperties.$pinia
                                const pinia = app.config.globalProperties.$pinia;
                                if (pinia && pinia._s) {
                                    const liveStore = pinia._s.get('liveStream') || pinia._s['liveStream'];
                                    if (liveStore) {
                                        result.vueStoreAlt = { storeId: liveStore.$id || 'liveStream' };
                                        if (liveStore.roomData && liveStore.roomData.roomInfo) {
                                            const ri = liveStore.roomData.roomInfo;
                                            if (ri.pullConfig) {
                                                result.pullConfig = ri.pullConfig;
                                                result.roomId = ri.roomId;
                                                result.source = 'vue3-pinia-globalProps';
                                            }
                                        }
                                    }
                                }
                            }
                        } catch(e) {
                            result.vueError = e.toString();
                        }

                        // 3. __INITIAL_STATE__ 检查
                        if (window.__INITIAL_STATE__) {
                            const ls = window.__INITIAL_STATE__.liveStream;
                            if (ls && ls.roomData && ls.roomData.roomInfo) {
                                const ri = ls.roomData.roomInfo;
                                result.initialStateRoomId = ri.roomId;
                                result.initialStateHasPullConfig = !!ri.pullConfig;
                                if (ri.pullConfig && ri.roomId > 0) {
                                    result.pullConfig = ri.pullConfig;
                                    result.source = '__INITIAL_STATE__';
                                }
                            }
                        }

                        return result;
                    }""")

                    # 打印调试信息（关键节点）
                    if i == 0 or i == 4 or (page_data and (page_data.get('pullConfig') or page_data.get('vueStore'))):
                        print(f"[小红书Playwright] 页面状态: videoCount={page_data.get('videoCount')}, "
                              f"videoSources={len(page_data.get('videoSources', []))}, "
                              f"vueStore={page_data.get('vueStore')}, "
                              f"initialStateRoomId={page_data.get('initialStateRoomId')}, "
                              f"initialStateHasPullConfig={page_data.get('initialStateHasPullConfig')}")
                        if page_data.get('vueError'):
                            print(f"[小红书Playwright] Vue store 访问错误: {page_data['vueError']}")

                    if page_data and page_data.get("pullConfig"):
                        try:
                            pc = json.loads(page_data["pullConfig"]) if isinstance(page_data["pullConfig"], str) else page_data["pullConfig"]
                            if isinstance(pc, dict):
                                source = page_data.get('source', 'unknown')
                                streams.extend(_xhs_parse_pull_config(pc, f"page.{source}"))
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if page_data and page_data.get("roomTitle"):
                        title_info["title"] = page_data["roomTitle"]
                    if streams:
                        context.close()
                        return {
                            "platform": "小红书",
                            "title": title_info.get("title", ""),
                            "uploader": title_info.get("uploader", ""),
                            "is_live": True,
                            "streams": streams,
                            "method": "Playwright浏览器解析",
                        }
                except Exception as e:
                    print(f"[小红书Playwright] 页面 JS 提取失败: {e}")

            # 最终状态报告
            print(f"[小红书Playwright] 等待超时(80秒)")
            print(f"[小红书Playwright] 最终: 捕获 {len(result_data)} 个API响应")
            for resp_url in result_data:
                data = result_data[resp_url]
                keys_info = ""
                if isinstance(data, dict):
                    keys_info = f" keys={list(data.keys())[:8]}"
                print(f"  - {resp_url[:100]}... ({keys_info})")
                # 搜索 roomInfo/pullConfig
                if isinstance(data, dict):
                    room_info = _deep_search_key(data, "roomInfo")
                    if isinstance(room_info, dict):
                        print(f"    >> 找到 roomInfo! pullConfig={str(room_info.get('pullConfig', 'MISSING'))[:100]}")
                        print(f"    >> roomId={room_info.get('roomId')}, keys={list(room_info.keys())[:10]}")
            context.close()

    except Exception as e:
        print(f"[小红书Playwright] 浏览器解析失败: {e}")
        pass

    return None


def _xhs_try_extract_streams(result_data: dict, title_info: dict, page) -> list:
    """从拦截到的 API 数据和页面 JS 变量中提取直播流地址。"""
    streams = []

    # 1. 从 API 拦截数据中提取（新版本 edith API）
    for resp_url, data in result_data.items():
        if not isinstance(data, dict):
            continue

        # 1a. 新版 edith API：roomInfo.pullConfig 是 JSON 字符串
        room_info = _deep_search_key(data, "roomInfo")
        if isinstance(room_info, dict) and room_info.get("pullConfig"):
            try:
                pull_config = json.loads(room_info["pullConfig"])
                if isinstance(pull_config, dict):
                    print(f"[小红书Playwright] 从 API pullConfig 提取流地址")
                    streams.extend(_xhs_parse_pull_config(pull_config, "pullConfig"))
            except (json.JSONDecodeError, TypeError):
                pass

        # 1b. 旧版 API：liveInfo / liveRoom
        live_info = _deep_search_key(data, "liveInfo")
        if not live_info:
            live_info = _deep_search_key(data, "liveRoom")
        if live_info:
            streams.extend(_xhs_parse_live_info(live_info, title_info))

        # 从 data 字段中查找
        data_field = data.get("data", {})
        if isinstance(data_field, dict):
            live_info = _deep_search_key(data_field, "liveInfo")
            if not live_info:
                live_info = _deep_search_key(data_field, "liveRoom")
            if live_info:
                streams.extend(_xhs_parse_live_info(live_info, title_info))

        # 从 data 字段查找 roomInfo.pullConfig
        if isinstance(data_field, dict):
            room_info2 = data_field.get("roomInfo")
            if isinstance(room_info2, dict) and room_info2.get("pullConfig"):
                try:
                    pull_config2 = json.loads(room_info2["pullConfig"])
                    if isinstance(pull_config2, dict):
                        streams.extend(_xhs_parse_pull_config(pull_config2, "data.pullConfig"))
                except (json.JSONDecodeError, TypeError):
                    pass

    # 2. 从页面 Vue store 提取（新版小红书用 Vue Pinia）
    try:
        # 尝试从 Vue app 的 store 获取 liveStream 数据
        vue_data = page.evaluate("""() => {
            // 尝试从 __INITIAL_STATE__ 获取
            const state = window.__INITIAL_STATE__;
            if (state && state.liveStream && state.liveStream.roomData) {
                const roomData = state.liveStream.roomData;
                if (roomData.roomInfo && roomData.roomInfo.pullConfig) {
                    return {source: '__INITIAL_STATE__', pullConfig: roomData.roomInfo.pullConfig, roomInfo: roomData.roomInfo};
                }
            }
            return null;
        }""")
        if vue_data:
            print(f"[小红书Playwright] 从页面 state 获取到 pullConfig")
            try:
                pc = json.loads(vue_data["pullConfig"]) if isinstance(vue_data["pullConfig"], str) else vue_data["pullConfig"]
                if isinstance(pc, dict):
                    streams.extend(_xhs_parse_pull_config(pc, "state.pullConfig"))
            except (json.JSONDecodeError, TypeError):
                pass
    except Exception as e:
        print(f"[小红书Playwright] 页面 state 提取失败: {e}")

    # 3. 从页面 __INITIAL_STATE__ 提取（旧版兼容）
    try:
        state = page.evaluate("() => window.__INITIAL_STATE__ || null")
        if state and isinstance(state, dict):
            streams_from_state = _xhs_extract_from_state(state, title_info)
            streams.extend(streams_from_state)
    except Exception:
        pass

    # 去重
    seen_urls = set()
    unique_streams = []
    for s in streams:
        if s["url"] not in seen_urls:
            seen_urls.add(s["url"])
            unique_streams.append(s)

    return unique_streams


def _xhs_extract_from_state(state: dict, title_info: dict) -> list:
    """从小红书 __INITIAL_STATE__ 数据中提取直播流。"""
    streams = []
    live_info = _deep_search_key(state, "liveInfo")
    if not live_info:
        live_info = _deep_search_key(state, "liveRoom")
    if live_info:
        streams.extend(_xhs_parse_live_info(live_info, title_info))
    return streams


def _xhs_parse_pull_config(pull_config: dict, source_prefix: str = "pullConfig") -> list:
    """从小红书 pullConfig 中解析流地址（统一处理新旧两种格式）。

    新版结构: {ver, error_code, media, streams}  — streams 是列表
    旧版结构: {streamUrl, hlsStreamUrl, flvStreamUrl, ...} — 值是 URL 字符串
    """
    streams = []

    if not isinstance(pull_config, dict):
        return streams

    # 打印 pullConfig 内容便于调试
    pc_str = json.dumps(pull_config, ensure_ascii=False)
    print(f"[小红书] pullConfig({source_prefix}) 内容: {pc_str[:600]}")

    # 新版结构: streams 列表
    if "streams" in pull_config and isinstance(pull_config["streams"], list):
        for stream_item in pull_config["streams"]:
            if isinstance(stream_item, dict):
                url = (stream_item.get("url") or stream_item.get("streamUrl")
                       or stream_item.get("completeUrl") or stream_item.get("pushUrl")
                       or stream_item.get("master_url"))
                quality = (stream_item.get("quality") or stream_item.get("qualityType")
                           or stream_item.get("quality_type_name")
                           or stream_item.get("name") or stream_item.get("streamType", ""))
                codec = stream_item.get("codec", "")
                if url:
                    stream_entry = {
                        "quality": quality or "未知",
                        "format": guess_format(url),
                        "url": url,
                        "source": f"{source_prefix}.streams",
                    }
                    if codec:
                        stream_entry["codec"] = codec
                    streams.append(stream_entry)
                # 备用源（backup_urls）
                backup_urls = stream_item.get("backup_urls", [])
                if isinstance(backup_urls, list):
                    for burl in backup_urls:
                        if isinstance(burl, str) and burl.startswith("http"):
                            stream_entry = {
                                "quality": f"{quality or '未知'}(备用)",
                                "format": guess_format(burl),
                                "url": burl,
                                "source": f"{source_prefix}.streams.backup",
                            }
                            if codec:
                                stream_entry["codec"] = codec
                            streams.append(stream_entry)
            elif isinstance(stream_item, str) and stream_item.startswith("http"):
                streams.append({
                    "quality": "未知",
                    "format": guess_format(stream_item),
                    "url": stream_item,
                    "source": f"{source_prefix}.streams",
                })

    # 处理 media 字段
    if "media" in pull_config and isinstance(pull_config["media"], dict):
        for key, val in pull_config["media"].items():
            if isinstance(val, str) and val.startswith("http"):
                streams.append({
                    "quality": f"media.{key}",
                    "format": guess_format(val),
                    "url": val,
                    "source": f"{source_prefix}.media.{key}",
                })
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        url = (item.get("url") or item.get("streamUrl")
                               or item.get("completeUrl"))
                        if url:
                            streams.append({
                                "quality": f"media.{key}.{item.get('quality', item.get('name', '未知'))}",
                                "format": guess_format(url),
                                "url": url,
                                "source": f"{source_prefix}.media.{key}",
                            })

    # 兼容旧版：顶层直接是 URL 字符串或 dict（跳过已知非 URL 字段）
    skip_keys = {"streams", "media", "ver", "error_code", "version", "errcode"}
    for key, val in pull_config.items():
        if key in skip_keys:
            continue
        if isinstance(val, str) and val.startswith("http"):
            streams.append({
                "quality": key,
                "format": guess_format(val),
                "url": val,
                "source": f"{source_prefix}.{key}",
            })
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, str) and sub_val.startswith("http"):
                    streams.append({
                        "quality": f"{key}.{sub_key}",
                        "format": guess_format(sub_val),
                        "url": sub_val,
                        "source": f"{source_prefix}.{key}.{sub_key}",
                    })

    return streams


def _xhs_parse_live_info(live_info: dict, title_info: dict) -> list:
    """从小红书 liveInfo/liveRoom 数据中解析流地址。"""
    streams = []
    if not isinstance(live_info, dict):
        return streams

    # 提取标题和主播
    if not title_info.get("title"):
        title_info["title"] = (
            live_info.get("title", "")
            or live_info.get("name", "")
            or live_info.get("liveTitle", "")
        )
    if not title_info.get("uploader"):
        title_info["uploader"] = (
            live_info.get("nickname", "")
            or live_info.get("anchorName", "")
            or live_info.get("anchor", {}).get("nickname", "")
            or live_info.get("author", {}).get("nickname", "")
        )

    # 流地址字段
    pull_url = live_info.get("pullUrl", "")
    if pull_url:
        streams.append({
            "quality": "默认",
            "format": guess_format(pull_url),
            "url": pull_url,
            "source": "pullUrl",
        })

    hls_url = live_info.get("hlsUrl", "") or live_info.get("m3u8Url", "") or live_info.get("hlsPullUrl", "")
    if hls_url:
        streams.append({
            "quality": "HLS",
            "format": "M3U8",
            "url": hls_url,
            "source": "HLS直播流",
        })

    flv_url = live_info.get("flvUrl", "") or live_info.get("flvPullUrl", "")
    if flv_url:
        streams.append({
            "quality": "FLV",
            "format": "FLV",
            "url": flv_url,
            "source": "FLV直播流",
        })

    # 搜索所有可能的 URL 字段
    for key in ["pullStreamUrl", "streamUrl", "playUrl", "liveUrl", "rtmpUrl", "liveStreamUrl"]:
        val = live_info.get(key, "")
        if val and isinstance(val, str) and val.startswith("http"):
            if not any(s["url"] == val for s in streams):
                streams.append({
                    "quality": key,
                    "format": guess_format(val),
                    "url": val,
                    "source": key,
                })

    # 递归搜索嵌套的流地址
    for key in ["liveStream", "streamInfo", "pullStream", "playUrls"]:
        sub = live_info.get(key, {})
        if isinstance(sub, dict):
            streams.extend(_xhs_parse_live_info(sub, title_info))
        elif isinstance(sub, list):
            for item in sub:
                if isinstance(item, dict):
                    streams.extend(_xhs_parse_live_info(item, title_info))

    return streams


def fetch_xiaohongshu(url: str, proxy: str = "") -> dict:
    """
    小红书直播专属解析（双策略）
    策略1：Playwright 浏览器自动化（优先，需要登录态）
    策略2：纯 HTTP 请求解析（降级，可能被反爬拦截）
    """
    # ─── 策略1：Playwright 浏览器自动化（优先）───
    print("[小红书] 尝试 Playwright 浏览器自动化解析...")
    pw_result = _xhs_fetch_via_playwright(url)
    if pw_result and pw_result.get("streams"):
        return pw_result

    # ─── 策略2：纯 HTTP 请求解析（降级）───
    print("[小红书] Playwright 未获取到流，尝试纯 HTTP 解析...")
    session = make_requests_session(proxy)
    headers = {**HEADERS_MOBILE, "Referer": "https://www.xiaohongshu.com/"}

    # 如果是短链接，先跟踪重定向
    try:
        resp = session.get(url, headers=headers, timeout=15, allow_redirects=True)
        final_url = resp.url
        text = resp.text
    except Exception as e:
        raise Exception(f"小红书页面请求失败: {e}")

    streams = []
    title = ""
    uploader = ""

    # 从 __INITIAL_STATE__ 提取
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>', text, re.DOTALL)
    if m:
        try:
            init_state = json.loads(m.group(1))
            # 搜索直播相关数据
            live_info = _deep_search_key(init_state, "liveInfo")
            if not live_info:
                live_info = _deep_search_key(init_state, "liveRoom")

            if live_info:
                # 流地址
                pull_url = live_info.get("pullUrl", "")
                if pull_url:
                    streams.append({
                        "quality": "默认",
                        "format": guess_format(pull_url),
                        "url": pull_url,
                        "source": "pullUrl",
                    })
                # HLS地址
                hls_url = live_info.get("hlsUrl", "") or live_info.get("m3u8Url", "")
                if hls_url:
                    streams.append({
                        "quality": "HLS",
                        "format": "M3U8",
                        "url": hls_url,
                        "source": "HLS直播流",
                    })
                # flv地址
                flv_url = live_info.get("flvUrl", "") or live_info.get("flvPullUrl", "")
                if flv_url:
                    streams.append({
                        "quality": "FLV",
                        "format": "FLV",
                        "url": flv_url,
                        "source": "FLV直播流",
                    })

                title = live_info.get("title", "") or live_info.get("name", "")
                uploader = live_info.get("nickname", "") or live_info.get("anchorName", "")
        except (json.JSONDecodeError, KeyError):
            pass

    # 备用：从SSR数据提取
    if not streams:
        m2 = re.search(r'window\.__INITIAL_SSR_STATE__\s*=\s*(\{.*?\});\s*</script>', text, re.DOTALL)
        if m2:
            try:
                ssr_data = json.loads(m2.group(1))
                live_data = _deep_search_key(ssr_data, "liveRoom")
                if live_data:
                    pull_url = live_data.get("pullUrl", "")
                    if pull_url:
                        streams.append({
                            "quality": "默认",
                            "format": guess_format(pull_url),
                            "url": pull_url,
                            "source": "SSR pullUrl",
                        })
            except (json.JSONDecodeError, KeyError):
                pass

    if not streams:
        raise Exception(
            "小红书专属解析失败。\n"
            "可能原因：\n"
            "  1) 直播间未开始或已结束\n"
            "  2) 未登录小红书账号（需要登录才能获取流地址）\n"
            "  3) Playwright 浏览器未安装或启动失败\n"
            "  4) 小红书反爬拦截\n\n"
            "解决方案：\n"
            "  - 点击状态栏「小红书」标注登录账号\n"
            "  - 确保电脑已安装 Chromium 浏览器\n"
            "  - 等页面加载完成后工具会自动提取流地址\n"
            "  - 如遇到验证码，请在弹出的浏览器中手动完成"
        )

    return {
        "platform": "小红书",
        "title": title,
        "uploader": uploader,
        "is_live": True,
        "streams": streams,
        "method": "小红书专属解析",
    }


# ─── 淘宝直播 ─────────────────────────────────────────────

def _get_tb_browser_data_dir():
    """获取淘宝浏览器持久化缓存目录"""
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(base, "LiveStreamFetcher", "taobao_browser_data")
    os.makedirs(d, exist_ok=True)
    return d


def _tb_extract_live_id(url: str) -> str:
    """从淘宝直播 URL 中提取 liveId"""
    m = re.search(r'liveId=(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'live\.taobao\.com/live/(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'taolive/video\.html\?[^"]*id=(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'[?&]id=(\d+)', url)
    if m:
        return m.group(1)
    return ""


def _tb_fetch_via_playwright(url: str, live_id: str) -> dict:
    """通过 Playwright 浏览器自动化获取淘宝直播流

    淘宝直播反爬严格：
    - PC 端需要登录态（未登录跳转 login.taobao.com）
    - H5 端是纯 SPA，数据 JS 动态加载
    - 流地址通过 alicdn.com 域名传输

    策略：
    1. 使用 persistent_context 保持登录态
    2. 非 headless 模式让用户可以手动登录
    3. 监听网络请求中的 m3u8/flv/alicdn 流地址
    4. 监听淘宝直播相关 API（mtop.taobao.*）
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[Playwright] playwright 未安装，跳过浏览器解析")
        return None

    try:
        with sync_playwright() as p:
            user_data_dir = _get_tb_browser_data_dir()
            print(f"[淘宝Playwright] 使用浏览器缓存目录: {user_data_dir}")

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "no_viewport": False,
            }

            launch_errors = []
            context = None

            embedded_chromium = _ensure_chromium_ready()
            if embedded_chromium:
                try:
                    print(f"[淘宝Playwright] 使用嵌入式 Chromium: {embedded_chromium}")
                    context = p.chromium.launch_persistent_context(
                        user_data_dir,
                        executable_path=os.path.join(embedded_chromium, "chrome.exe"),
                        **launch_kwargs,
                    )
                except Exception as e_embed:
                    launch_errors.append(f"Embedded: {e_embed}")
                    print(f"[淘宝Playwright] 嵌入式 Chromium 启动失败: {e_embed}")

            if not context:
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir, channel=None, **launch_kwargs,
                    )
                except Exception as e1:
                    launch_errors.append(f"Chromium: {e1}")
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir, channel="chrome", **launch_kwargs,
                        )
                    except Exception as e2:
                        launch_errors.append(f"Chrome: {e2}")
                        try:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="msedge", **launch_kwargs,
                            )
                        except Exception as e3:
                            launch_errors.append(f"Edge: {e3}")

            if not context:
                print(f"[淘宝Playwright] 无法启动浏览器: {'; '.join(launch_errors)}")
                return None

            page = context.pages[0] if context.pages else context.new_page()

            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
            """)

            stream_urls = []
            api_data = {}
            title_info = {"title": "", "uploader": ""}

            def on_response(response):
                resp_url = response.url

                # 1. 拦截 m3u8/flv 流地址（来自 alicdn.com 等域名）
                if any(kw in resp_url.lower() for kw in [".m3u8", ".flv"]) and \
                   any(domain in resp_url for domain in ["alicdn.com", "tbcdn.cn", "taobaocdn.com"]):
                    if resp_url not in [s["url"] for s in stream_urls]:
                        fmt = "M3U8" if ".m3u8" in resp_url.lower() else "FLV"
                        stream_urls.append({
                            "quality": "默认",
                            "format": fmt,
                            "url": resp_url,
                            "source": "网络拦截",
                        })

                # 2. 拦截淘宝 API 响应
                if "mtop.taobao" in resp_url or "mtop.alibaba" in resp_url:
                    try:
                        body = response.body()
                        if body and len(body) < 500000:
                            data = json.loads(body.decode("utf-8", errors="replace"))
                            api_data[resp_url] = data
                            def find_streams(obj, depth=0):
                                results = []
                                if depth > 15:
                                    return results
                                if isinstance(obj, dict):
                                    for k, v in obj.items():
                                        if isinstance(v, str) and v.startswith("http") and \
                                           any(kw in v.lower() for kw in [".m3u8", ".flv", "hls", "flv", "stream", "pull", "play"]):
                                            results.append((k, v))
                                        elif isinstance(v, (dict, list)):
                                            results.extend(find_streams(v, depth + 1))
                                elif isinstance(obj, list):
                                    for item in obj:
                                        if isinstance(item, (dict, list)):
                                            results.extend(find_streams(item, depth + 1))
                                return results
                            found = find_streams(data)
                            seen = {s["url"] for s in stream_urls}
                            for key, s_url in found:
                                if s_url not in seen:
                                    seen.add(s_url)
                                    stream_urls.append({
                                        "quality": key,
                                        "format": guess_format(s_url),
                                        "url": s_url,
                                        "source": "API拦截",
                                    })
                            if not title_info["title"] or not title_info["uploader"]:
                                result_obj = data.get("data", {})
                                if isinstance(result_obj, dict):
                                    title_info["title"] = result_obj.get("title", "") or result_obj.get("liveTitle", "")
                                    title_info["uploader"] = result_obj.get("anchorName", "") or result_obj.get("nickName", "") or result_obj.get("userName", "")
                    except Exception:
                        pass

            page.on("response", on_response)

            # ── 登录检测 ──
            login_detected = {"value": False}
            prev_url = {"value": ""}

            def on_navigate(navigation):
                try:
                    nav_url = navigation.url
                    if not nav_url or nav_url == "about:blank":
                        return
                    if "login.taobao.com" in prev_url.get("value", ""):
                        if "login.taobao.com" not in nav_url:
                            login_detected["value"] = True
                            print("[淘宝Playwright] 检测到登录成功，将自动刷新直播间")
                    prev_url["value"] = nav_url
                except Exception:
                    pass

            page.on("framenavigated", on_navigate)

            # ── 打开直播间 ──
            print("[淘宝Playwright] 正在打开淘宝直播间...")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            need_login = False
            current_url = page.url
            if "login.taobao.com" in current_url:
                need_login = True
                print("[淘宝Playwright] 被重定向到登录页面")

            if not need_login:
                try:
                    cookies = context.cookies()
                    tb_cookies = [c for c in cookies if "taobao.com" in c.get("domain", "")]
                    cookie_names = [c.get("name", "") for c in tb_cookies]
                    login_cookies = ["_tb_token_", "cookie2", "sgcookie", "unb", "lgc", "nk"]
                    if not any(n in cookie_names for n in login_cookies):
                        page_content = page.content()
                        if "login" in page_content.lower() and len(page_content) < 20000:
                            need_login = True
                            print("[淘宝Playwright] 页面要求登录")
                except Exception:
                    pass

            # ── 登录流程 ──
            if need_login:
                print("[淘宝Playwright] 正在打开淘宝登录页面，请扫码登录...")
                login_url = "https://login.taobao.com/member/login.jhtml"
                page.goto(login_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                prev_url["value"] = page.url
                for wait_i in range(24):
                    page.wait_for_timeout(5000)
                    if login_detected["value"]:
                        login_detected["value"] = False
                        api_data.clear()
                        stream_urls.clear()
                        print("[淘宝Playwright] 登录成功！正在跳转回直播间...")
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        prev_url["value"] = page.url
                        break
                else:
                    print("[淘宝Playwright] 等待登录超时（120秒），尝试继续解析...")

            # ── 等待流数据 ──
            print("[淘宝Playwright] 正在监听网络请求，等待直播流数据...")
            for i in range(15):
                page.wait_for_timeout(4000)

                if login_detected["value"]:
                    login_detected["value"] = False
                    api_data.clear()
                    stream_urls.clear()
                    print("[淘宝Playwright] 检测到登录成功，正在刷新...")
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    prev_url["value"] = page.url
                    for _j in range(6):
                        page.wait_for_timeout(5000)
                        if stream_urls:
                            break
                    continue

                if stream_urls:
                    context.close()
                    return {
                        "platform": "淘宝直播",
                        "title": title_info.get("title", ""),
                        "uploader": title_info.get("uploader", ""),
                        "is_live": True,
                        "streams": stream_urls,
                        "method": "Playwright浏览器解析",
                    }

                # 从页面 JS 获取数据
                try:
                    page_data = page.evaluate("""() => {
                        if (window.__INITIAL_DATA__) return JSON.stringify(window.__INITIAL_DATA__);
                        if (window.__INITIAL_STATE__) return JSON.stringify(window.__INITIAL_STATE__);
                        if (window.__data__) return JSON.stringify(window.__data__);
                        const videos = document.querySelectorAll('video');
                        const audio = document.querySelectorAll('audio');
                        const sources = [];
                        videos.forEach(v => { if(v.src) sources.push(v.src); if(v.currentSrc) sources.push(v.currentSrc); });
                        audio.forEach(a => { if(a.src) sources.push(a.src); });
                        if (sources.length > 0) return JSON.stringify({mediaSources: sources});
                        return '';
                    }""")
                    if page_data and page_data.strip():
                        data = json.loads(page_data)
                        if isinstance(data, dict):
                            media = data.get("mediaSources", [])
                            for src in media:
                                if src.startswith("http") and src not in [s["url"] for s in stream_urls]:
                                    stream_urls.append({
                                        "quality": "默认",
                                        "format": guess_format(src),
                                        "url": src,
                                        "source": "页面媒体标签",
                                    })
                            if not media:
                                def find_in_data(obj, depth=0):
                                    results = []
                                    if depth > 15:
                                        return results
                                    if isinstance(obj, dict):
                                        for k, v in obj.items():
                                            if isinstance(v, str) and v.startswith("http") and \
                                               any(kw in v.lower() for kw in [".m3u8", ".flv"]):
                                                results.append((k, v))
                                            elif isinstance(v, (dict, list)):
                                                results.extend(find_in_data(v, depth + 1))
                                    elif isinstance(obj, list):
                                        for item in obj:
                                            if isinstance(item, (dict, list)):
                                                results.extend(find_in_data(item, depth + 1))
                                    return results
                                found = find_in_data(data)
                                for key, s_url in found:
                                    if s_url not in [s["url"] for s in stream_urls]:
                                        stream_urls.append({
                                            "quality": key,
                                            "format": guess_format(s_url),
                                            "url": s_url,
                                            "source": "页面JS数据",
                                        })
                            if not title_info["title"]:
                                title_info["title"] = data.get("title", "") or data.get("liveTitle", "") or ""
                            if not title_info["uploader"]:
                                title_info["uploader"] = data.get("anchorName", "") or data.get("nickName", "") or ""
                except Exception:
                    pass

                if stream_urls:
                    context.close()
                    return {
                        "platform": "淘宝直播",
                        "title": title_info.get("title", ""),
                        "uploader": title_info.get("uploader", ""),
                        "is_live": True,
                        "streams": stream_urls,
                        "method": "Playwright浏览器解析",
                    }

            context.close()

    except Exception as e:
        print(f"[淘宝Playwright] 浏览器解析失败: {e}")
        pass

    return None


def fetch_taobao_live(url: str, proxy: str = "") -> dict:
    """
    淘宝直播专属解析
    策略：
      1. 尝试从 HTML 中提取 __INITIAL_DATA__ / JSON 数据
      2. 尝试淘宝直播 API（通过 liveId 获取流地址）
      3. 提取页面中的所有 m3u8/flv 链接
    """
    session = make_requests_session(proxy)
    headers = {
        **HEADERS_PC,
        "Referer": "https://live.taobao.com/",
    }

    try:
        resp = session.get(url, headers=headers, timeout=15, allow_redirects=True)
        text = resp.text
        final_url = resp.url
    except Exception as e:
        raise Exception(f"淘宝页面请求失败: {e}")

    streams = []
    title = ""
    uploader = ""

    # ── 1. 从 __INITIAL_DATA__ 提取（优先级最高）──
    m = re.search(r'window\.__INITIAL_DATA__\s*=\s*(\{.*?\});\s*</script>', text, re.DOTALL)
    if not m:
        # 也尝试 JSON.parse 的格式
        m = re.search(r'__INITIAL_DATA__\s*=\s*(\{.*?\})\s*;?\s*$', text, re.DOTALL)

    if m:
        try:
            raw = m.group(1)
            data = json.loads(raw)

            # 递归搜索流地址
            def find_stream_urls(obj, depth=0):
                """递归搜索对象中所有可能的流地址"""
                if depth > 15:
                    return []
                results = []
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if isinstance(v, str) and v.startswith("http") and any(
                            kw in v.lower() for kw in [".m3u8", ".flv", "hls", "flv", "live", "stream", "pull", "play"]
                        ):
                            results.append((k, v))
                        elif isinstance(v, (dict, list)):
                            results.extend(find_stream_urls(v, depth + 1))
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, (dict, list)):
                            results.extend(find_stream_urls(item, depth + 1))
                return results

            found = find_stream_urls(data)
            seen_urls = set()
            for key, stream_url in found:
                if stream_url not in seen_urls:
                    seen_urls.add(stream_url)
                    streams.append({
                        "quality": key,
                        "format": guess_format(stream_url),
                        "url": stream_url,
                        "source": "INITIAL_DATA",
                    })

            # 尝试从 liveData / liveRoom 中获取信息
            live_data = _deep_search_key(data, "liveData") or \
                        _deep_search_key(data, "liveRoom") or \
                        _deep_search_key(data, "roomInfo") or \
                        _deep_search_key(data, "liveInfo") or \
                        _deep_search_key(data, "playInfo")
            if live_data:
                for key in ["playUrl", "m3u8Url", "flvUrl", "liveUrl", "pullUrl", "hlsUrl", "streamUrl", "url"]:
                    val = live_data.get(key, "")
                    if val and isinstance(val, str) and val.startswith("http"):
                        if val not in seen_urls:
                            seen_urls.add(val)
                            streams.append({
                                "quality": key,
                                "format": guess_format(val),
                                "url": val,
                                "source": "INITIAL_DATA-live",
                            })
                title = live_data.get("title", "") or live_data.get("name", "") or live_data.get("liveTitle", "")
                uploader = live_data.get("nickName", "") or live_data.get("anchorName", "") or live_data.get("userName", "")

        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # ── 2. 从页面 script 标签中搜索流地址 ──
    patterns_to_search = [
        r'"playUrl"\s*:\s*"([^"]+)"',
        r'"m3u8Url"\s*:\s*"([^"]+)"',
        r'"flvUrl"\s*:\s*"([^"]+)"',
        r'"liveUrl"\s*:\s*"([^"]+)"',
        r'"streamUrl"\s*:\s*"([^"]+)"',
        r'"pullUrl"\s*:\s*"([^"]+)"',
        r'"hlsUrl"\s*:\s*"([^"]+)"',
        r'"url"\s*:\s*"(https?://[^"]*\.(?:m3u8|flv)[^"]*)"',
    ]

    seen_urls = {s["url"] for s in streams}
    for pattern in patterns_to_search:
        matches = re.findall(pattern, text)
        for m_url in matches:
            if m_url.startswith("http") and m_url not in seen_urls:
                seen_urls.add(m_url)
                streams.append({
                    "quality": "默认",
                    "format": guess_format(m_url),
                    "url": m_url,
                    "source": "页面提取",
                })

    # ── 3. 从所有 script 标签内容中提取 JSON 数据 ──
    script_blocks = re.findall(r'<script[^>]*>(.*?)</script>', text, re.DOTALL)
    for block in script_blocks:
        # 寻找包含 URL 的 JSON 对象
        json_matches = re.findall(r'\{[^{}]*"(?:url|playUrl|streamUrl|hlsUrl|m3u8Url|pullUrl)"\s*:\s*"(https?://[^"]+)"[^{}]*\}', block)
        for jm in json_matches:
            try:
                jd = json.loads(jm)
                for key, val in jd.items():
                    if isinstance(val, str) and val.startswith("http") and val not in seen_urls:
                        seen_urls.add(val)
                        streams.append({
                            "quality": key,
                            "format": guess_format(val),
                            "url": val,
                            "source": "Script-JSON",
                        })
            except json.JSONDecodeError:
                pass

    # ── 4. 从标题提取 ──
    if not title:
        title_match = re.search(r'<title[^>]*>(.*?)</title>', text, re.DOTALL)
        if title_match:
            title = title_match.group(1).strip().replace(" - 淘宝直播", "").replace("-淘宝直播", "").replace("—淘宝直播", "").strip()

    if not streams:
        # 纯请求解析失败，尝试 Playwright 浏览器自动化
        live_id = _tb_extract_live_id(url)
        pw_result = _tb_fetch_via_playwright(url, live_id)
        if pw_result and pw_result.get("streams"):
            return pw_result
        raise Exception(
            "淘宝直播解析失败。\n"
            "可能原因：\n"
            "  1) 直播间未开始或已结束\n"
            "  2) 需要登录淘宝账号才能获取流地址\n"
            "  3) Playwright 浏览器启动失败\n\n"
            "建议：\n"
            "  - 确认直播间正在直播中\n"
            "  - 如弹出浏览器，请扫码登录淘宝账号\n"
            "  - 等待页面加载完成后再尝试"
        )

    return {
        "platform": "淘宝直播",
        "title": title,
        "uploader": uploader,
        "is_live": True,
        "streams": streams,
        "method": "淘宝直播专属解析",
    }


# ═══════════════════════════════════════════════════════
# YY 直播
# ═══════════════════════════════════════════════════════

def _get_yy_browser_data_dir():
    """获取YY浏览器持久化缓存目录"""
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    return os.path.join(base, "LiveStreamFetcher", "yy_browser_data")


def _yy_extract_room_id(url: str):
    """从YY直播 URL 中提取房间号"""
    m = re.search(r'yy\.com/(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'channel=(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'rid=(\d+)', url)
    if m:
        return m.group(1)
    # 移动端 wap.yy.com/mobileweb/993 格式
    m = re.search(r'mobileweb/(\d+)', url)
    if m:
        return m.group(1)
    return None


def _yy_check_login_status():
    """检查YY浏览器持久化目录中是否存在有效的登录 Cookie。"""
    user_data_dir = _get_yy_browser_data_dir()
    cookie_db = os.path.join(user_data_dir, "Default", "Cookies")
    if not os.path.isfile(cookie_db):
        # 也检查 Network/Cookies
        cookie_db = os.path.join(user_data_dir, "Default", "Network", "Cookies")
    if not os.path.isfile(cookie_db):
        return "never"
    try:
        import sqlite3
        conn = sqlite3.connect(cookie_db)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%yy.com%'")
        count = cur.fetchone()[0]
        conn.close()
        if count > 0:
            return "logged_in"
    except Exception:
        pass
    return "never"


def _yy_fetch_via_playwright(url: str, room_id: str):
    """通过 Playwright 浏览器自动化获取YY直播流

    YY直播特点：
    - PC端网页版可直接观看直播（不一定需要登录）
    - 流地址通过 yy.com 域名传输 FLV 格式
    - 页面可能使用 JS 动态加载播放器

    策略：
    1. 使用 persistent_context 保持登录态
    2. 非 headless 模式让用户可以手动登录
    3. 监听网络请求中的 m3u8/flv 流地址
    4. 尝试从页面 JS 中提取播放器配置
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[YY Playwright] playwright 未安装，跳过浏览器解析")
        return None

    try:
        with sync_playwright() as p:
            user_data_dir = _get_yy_browser_data_dir()
            print(f"[YY Playwright] 使用浏览器缓存目录: {user_data_dir}")

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "no_viewport": False,
            }

            launch_errors = []
            context = None

            embedded_chromium = _ensure_chromium_ready()
            if embedded_chromium:
                try:
                    print(f"[YY Playwright] 使用嵌入式 Chromium: {embedded_chromium}")
                    context = p.chromium.launch_persistent_context(
                        user_data_dir,
                        executable_path=os.path.join(embedded_chromium, "chrome.exe"),
                        **launch_kwargs,
                    )
                except Exception as e_embed:
                    launch_errors.append(f"Embedded: {e_embed}")
                    print(f"[YY Playwright] 嵌入式 Chromium 启动失败: {e_embed}")

            if not context:
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir, channel=None, **launch_kwargs,
                    )
                except Exception as e1:
                    launch_errors.append(f"Chromium: {e1}")
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir, channel="chrome", **launch_kwargs,
                        )
                    except Exception as e2:
                        launch_errors.append(f"Chrome: {e2}")
                        try:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="msedge", **launch_kwargs,
                            )
                        except Exception as e3:
                            launch_errors.append(f"Edge: {e3}")

            if not context:
                print(f"[YY Playwright] 无法启动浏览器: {'; '.join(launch_errors)}")
                return None

            page = context.pages[0] if context.pages else context.new_page()

            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
            """)

            stream_urls = []
            title_info = {"title": "", "uploader": ""}

            def on_response(response):
                resp_url = response.url
                # 拦截 FLV/M3U8 流地址（yy.com 域名）
                if any(kw in resp_url.lower() for kw in [".flv", ".m3u8"]) and \
                   any(domain in resp_url for domain in ["yy.com", "yystatic.com", "yycloud.com"]):
                    if resp_url not in [s["url"] for s in stream_urls]:
                        fmt = "M3U8" if ".m3u8" in resp_url.lower() else "FLV"
                        stream_urls.append({
                            "quality": "默认",
                            "format": fmt,
                            "url": resp_url,
                            "source": "网络拦截",
                        })
                # 也拦截其他 CDN 域名的直播流
                elif any(kw in resp_url.lower() for kw in [".flv"]) and \
                     "live" in resp_url.lower():
                    if resp_url not in [s["url"] for s in stream_urls]:
                        stream_urls.append({
                            "quality": "默认",
                            "format": "FLV",
                            "url": resp_url,
                            "source": "网络拦截",
                        })

            page.on("response", on_response)

            # ── 打开直播间 ──
            print(f"[YY Playwright] 正在打开YY直播间 (room_id={room_id})...")
            # 使用PC网页版
            if not url.startswith("http"):
                url = f"https://www.yy.com/{room_id}"
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            # 尝试从页面提取标题和主播名
            try:
                page_title = page.title()
                if page_title:
                    title_info["title"] = page_title.replace(" - YY直播", "").replace("-YY直播", "").replace("—YY直播", "").strip()
            except Exception:
                pass

            try:
                # 尝试从页面元素获取主播名
                anchor_el = page.query_selector(".nick-name, .hostname, .anchor-name, [class*='nick'], [class*='anchor']")
                if anchor_el:
                    name_text = anchor_el.inner_text().strip()
                    if name_text:
                        title_info["uploader"] = name_text
            except Exception:
                pass

            # 等待流数据出现（最多等 60 秒）
            for wait_i in range(60):
                if stream_urls:
                    print(f"[YY Playwright] 成功拦截到 {len(stream_urls)} 个流地址")
                    break
                if wait_i > 0 and wait_i % 10 == 0:
                    print(f"[YY Playwright] 等待流数据... ({wait_i}s)")
                page.wait_for_timeout(1000)

                # 30 秒后尝试刷新
                if wait_i == 30 and not stream_urls:
                    print("[YY Playwright] 30秒未获取到流，尝试刷新页面...")
                    page.reload()
                    page.wait_for_timeout(5000)

            # 尝试从页面 JS 变量中提取流地址
            if not stream_urls:
                try:
                    js_result = page.evaluate("""() => {
                        // 尝试从全局变量中查找流地址
                        const results = [];
                        const searchObj = (obj, depth) => {
                            if (depth > 10) return;
                            if (typeof obj === 'string') {
                                if (obj.match(/https?:\\/\\/[^\\s]+\\.(flv|m3u8)/i)) {
                                    results.push(obj);
                                }
                            } else if (typeof obj === 'object' && obj) {
                                for (const key of Object.keys(obj)) {
                                    try { searchObj(obj[key], depth + 1); } catch(e) {}
                                }
                            }
                        };
                        // 常见的播放器配置变量
                        if (window.__INITIAL_STATE__) searchObj(window.__INITIAL_STATE__, 0);
                        if (window.__INITIAL_DATA__) searchObj(window.__INITIAL_DATA__, 0);
                        if (window.liveData) searchObj(window.liveData, 0);
                        if (window.playerConfig) searchObj(window.playerConfig, 0);
                        if (window.videoInfo) searchObj(window.videoInfo, 0);
                        if (window.streamUrl) results.push(window.streamUrl);
                        if (window.playUrl) results.push(window.playUrl);
                        return [...new Set(results)];
                    }""")
                    for js_url in js_result:
                        if js_url and js_url not in [s["url"] for s in stream_urls]:
                            fmt = "M3U8" if ".m3u8" in js_url.lower() else "FLV"
                            stream_urls.append({
                                "quality": "默认",
                                "format": fmt,
                                "url": js_url,
                                "source": "JS提取",
                            })
                except Exception as e:
                    print(f"[YY Playwright] JS提取失败: {e}")

            # 尝试从 <video> 或 <source> 元素获取
            if not stream_urls:
                try:
                    video_src = page.evaluate("""() => {
                        const videos = document.querySelectorAll('video');
                        const sources = document.querySelectorAll('source');
                        const urls = [];
                        videos.forEach(v => { if (v.src) urls.push(v.src); if (v.currentSrc) urls.push(v.currentSrc); });
                        sources.forEach(s => { if (s.src) urls.push(s.src); });
                        return [...new Set(urls)];
                    }""")
                    for vs in video_src:
                        if vs and vs not in [s["url"] for s in stream_urls]:
                            fmt = "M3U8" if ".m3u8" in vs.lower() else "FLV"
                            stream_urls.append({
                                "quality": "默认",
                                "format": fmt,
                                "url": vs,
                                "source": "Video元素",
                            })
                except Exception:
                    pass

            # 尝试从页面源码中正则匹配
            if not stream_urls:
                try:
                    page_content = page.content()
                    url_patterns = [
                        r'(https?://[^\s"\'<>]+\.flv[^\s"\'<>]*)',
                        r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
                    ]
                    seen = set()
                    for pat in url_patterns:
                        for m_url in re.findall(pat, page_content):
                            clean_url = m_url.split('"')[0].split("'")[0].split("\\")[0]
                            if clean_url.startswith("http") and clean_url not in seen:
                                seen.add(clean_url)
                                fmt = "M3U8" if ".m3u8" in clean_url.lower() else "FLV"
                                stream_urls.append({
                                    "quality": "默认",
                                    "format": fmt,
                                    "url": clean_url,
                                    "source": "页面正则",
                                })
                except Exception:
                    pass

            page.remove_listener("response", on_response)

            if stream_urls:
                # 等待 2 秒收集更多流
                page.wait_for_timeout(2000)

                result = {
                    "platform": "YY直播",
                    "title": title_info.get("title", "") or f"YY直播间 {room_id}",
                    "uploader": title_info.get("uploader", ""),
                    "is_live": True,
                    "streams": stream_urls,
                    "method": "YY直播 Playwright解析",
                }
                try:
                    context.close()
                except Exception:
                    pass
                return result

            try:
                context.close()
            except Exception:
                pass

    except Exception as e:
        print(f"[YY Playwright] 浏览器解析失败: {e}")
        pass

    return None


def fetch_yy_live(url: str, proxy: str = "") -> dict:
    """YY直播专属解析
    策略：
      1. 尝试从网页 HTML 中提取流地址
      2. 尝试 Playwright 浏览器自动化监听网络请求
    """
    session = make_requests_session(proxy)
    headers = {
        **HEADERS_PC,
        "Referer": "https://www.yy.com/",
    }

    room_id = _yy_extract_room_id(url)
    if not room_id:
        raise Exception("无法从URL中提取YY直播间ID，请检查链接格式")

    # 尝试多个URL格式
    possible_urls = [
        f"https://www.yy.com/{room_id}",
        f"https://wap.yy.com/mobileweb/{room_id}",
        url,
    ]

    streams = []
    title = ""
    uploader = ""

    for try_url in possible_urls:
        try:
            resp = session.get(try_url, headers=headers, timeout=15, allow_redirects=True)
            text = resp.text
            final_url = resp.url
        except Exception as e:
            print(f"[YY] 请求 {try_url} 失败: {e}")
            continue

        # ── 1. 从页面正则提取流地址 ──
        url_patterns = [
            r'(https?://[^\s"\'<>]+\.flv[^\s"\'<>]*)',
            r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
            r'"playUrl"\s*:\s*"([^"]+)"',
            r'"streamUrl"\s*:\s*"([^"]+)"',
            r'"liveUrl"\s*:\s*"([^"]+)"',
            r'"pullUrl"\s*:\s*"([^"]+)"',
            r'"hlsUrl"\s*:\s*"([^"]+)"',
            r'"flvUrl"\s*:\s*"([^"]+)"',
            r'"m3u8Url"\s*:\s*"([^"]+)"',
            r'"url"\s*:\s*"(https?://[^"]*\.(?:m3u8|flv)[^"]*)"',
        ]

        seen_urls = set()
        for pattern in url_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for m_url in matches:
                clean_url = m_url.strip()
                # 清理转义字符
                clean_url = clean_url.replace("\\/", "/").replace("\\u002F", "/")
                if clean_url.startswith("http") and clean_url not in seen_urls:
                    seen_urls.add(clean_url)
                    fmt = "M3U8" if ".m3u8" in clean_url.lower() else "FLV"
                    streams.append({
                        "quality": "默认",
                        "format": fmt,
                        "url": clean_url,
                        "source": "页面提取",
                    })

        # ── 2. 从 script 标签提取 JSON 数据 ──
        script_blocks = re.findall(r'<script[^>]*>(.*?)</script>', text, re.DOTALL)
        for block in script_blocks:
            try:
                # 搜索包含URL的JSON对象
                json_matches = re.findall(r'\{[^{}]*"(?:url|playUrl|streamUrl|hlsUrl|m3u8Url|pullUrl|flvUrl|liveUrl)"\s*:\s*"(https?://[^"]+)"[^{}]*\}', block)
                for jm in json_matches:
                    jd = json.loads(jm)
                    for key, val in jd.items():
                        if isinstance(val, str) and val.startswith("http") and val not in seen_urls:
                            seen_urls.add(val)
                            streams.append({
                                "quality": key,
                                "format": guess_format(val),
                                "url": val,
                                "source": "Script-JSON",
                            })
            except json.JSONDecodeError:
                pass

        # ── 3. 从 __INITIAL_STATE__ / __INITIAL_DATA__ 提取 ──
        for state_name in ["__INITIAL_STATE__", "__INITIAL_DATA__"]:
            m = re.search(rf'window\.{state_name}\s*=\s*(\{{.*?\}})\s*;', text, re.DOTALL)
            if not m:
                m = re.search(rf'{state_name}\s*=\s*(\{{.*?\}})\s*;?\s*$', text, re.DOTALL)
            if m:
                try:
                    raw = m.group(1).replace("undefined", "null")
                    data = json.loads(raw)

                    def find_stream_urls(obj, depth=0):
                        if depth > 15:
                            return []
                        results = []
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if isinstance(v, str) and v.startswith("http") and any(
                                    kw in v.lower() for kw in [".m3u8", ".flv", "hls", "flv", "live", "stream", "pull", "play"]
                                ):
                                    results.append((k, v))
                                elif isinstance(v, (dict, list)):
                                    results.extend(find_stream_urls(v, depth + 1))
                        elif isinstance(obj, list):
                            for item in obj:
                                if isinstance(item, (dict, list)):
                                    results.extend(find_stream_urls(item, depth + 1))
                        return results

                    found = find_stream_urls(data)
                    for key, stream_url in found:
                        if stream_url not in seen_urls:
                            seen_urls.add(stream_url)
                            streams.append({
                                "quality": key,
                                "format": guess_format(stream_url),
                                "url": stream_url,
                                "source": state_name,
                            })

                    # 提取标题和主播信息
                    live_data = None
                    for search_key in ["liveData", "liveRoom", "roomInfo", "liveInfo", "playInfo", "anchorInfo"]:
                        live_data = _deep_search_key(data, search_key)
                        if live_data:
                            break
                    if live_data:
                        if not title:
                            title = live_data.get("title", "") or live_data.get("name", "") or live_data.get("liveTitle", "")
                        if not uploader:
                            uploader = live_data.get("nickName", "") or live_data.get("anchorName", "") or live_data.get("userName", "") or live_data.get("name", "")
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
            if streams:
                break

        # ── 4. 从标题提取 ──
        if not title:
            title_match = re.search(r'<title[^>]*>(.*?)</title>', text, re.DOTALL)
            if title_match:
                title = title_match.group(1).strip().replace(" - YY直播", "").replace("-YY直播", "").replace("—YY直播", "").strip()

        if streams:
            break

    if not streams:
        # 纯请求解析失败，尝试 Playwright 浏览器自动化
        pw_result = _yy_fetch_via_playwright(url, room_id)
        if pw_result and pw_result.get("streams"):
            return pw_result
        raise Exception(
            "YY直播解析失败。\n"
            "可能原因：\n"
            "  1) 直播间未开始或已结束\n"
            "  2) Playwright 浏览器启动失败\n\n"
            "建议：\n"
            "  - 确认直播间正在直播中\n"
            "  - 如弹出浏览器，请等待页面加载完成\n"
            "  - 等1-2分钟后重试"
        )

    return {
        "platform": "YY直播",
        "title": title or f"YY直播间 {room_id}",
        "uploader": uploader,
        "is_live": True,
        "streams": streams,
        "method": "YY直播专属解析",
    }


def _set_system_proxy(port: int) -> str:
    """设置 Windows 系统代理"""
    import winreg
    proxy_addr = f"127.0.0.1:{port}"
    try:
        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_addr)
        import ctypes
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
        return proxy_addr
    except Exception as e:
        raise RuntimeError(f"设置系统代理失败: {e}")


def _clear_system_proxy() -> None:
    """关闭 Windows 系统代理"""
    import winreg
    try:
        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        import ctypes
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# 平台解析器注册表
# ═══════════════════════════════════════════════════════

PLATFORM_FETCHERS = {
    "快手": fetch_kuaishou,
    "抖音": fetch_douyin,
    "小红书": fetch_xiaohongshu,
    "淘宝直播": fetch_taobao_live,
    "YY直播": fetch_yy_live,
}


# ─── yt-dlp 降级方案 ──────────────────────────────────────

def fetch_streams_ytdlp(url: str, proxy: str = "") -> dict:
    """yt-dlp 降级解析"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "http_headers": HEADERS_PC,
        "socket_timeout": 15,
    }
    if proxy:
        ydl_opts["proxy"] = proxy

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info


def parse_stream_info(info: dict) -> list:
    """从 yt-dlp 信息中解析视频流"""
    streams = []

    if info and info.get("url"):
        url = info["url"]
        streams.append({
            "quality": info.get("resolution", "") or info.get("format", "") or "默认",
            "format": info.get("ext", "").upper() or guess_format(url),
            "url": url,
            "source": "直接流",
        })

    if info and info.get("formats"):
        seen = set()
        for fmt in info["formats"]:
            furl = fmt.get("url", "")
            if not furl or furl in seen:
                continue
            vcodec = fmt.get("vcodec", "none")
            if vcodec == "none":
                continue
            seen.add(furl)
            height = fmt.get("height", 0)
            streams.append({
                "quality": f"{height}p" if height else (fmt.get("resolution", "") or "未知"),
                "format": fmt.get("ext", "").upper() or guess_format(furl),
                "url": furl,
                "source": "yt-dlp格式列表",
            })

    if info and info.get("requested_formats"):
        existing = {s["url"] for s in streams}
        for fmt in info["requested_formats"]:
            furl = fmt.get("url", "")
            if furl and furl not in existing:
                existing.add(furl)
                streams.append({
                    "quality": f"{fmt.get('height', 0)}p" or "未知",
                    "format": fmt.get("ext", "").upper() or guess_format(furl),
                    "url": furl,
                    "source": "yt-dlp DASH",
                })

    return streams


# ─── 主提取函数 ──────────────────────────────────────────

def extract_streams(url: str, proxy: str = "") -> dict:
    """
    主提取函数：优先使用平台专属解析器，失败后降级到 yt-dlp
    """
    platform = detect_platform(url)

    # 1. 尝试平台专属解析器
    if platform in PLATFORM_FETCHERS:
        try:
            log_msg = f"[专属解析] 使用{platform}专属解析器..."
            result = PLATFORM_FETCHERS[platform](url, proxy)
            if result.get("streams"):
                # 去重 + 打清晰度标签
                unique = _dedup_streams(result["streams"])
                result["streams"] = _tag_streams_with_quality(_sort_streams(unique))
                result["platform"] = platform
                result["method_used"] = "平台专属解析器"
                return result
        except FetchUserError:
            # 用户可理解的错误（如未直播），直接抛出不降级
            raise
        except Exception as e:
            # 快手/淘宝直播/视频号/小红书等 yt-dlp 不支持的平台，不要降级到 yt-dlp
            if platform in ("快手", "淘宝直播", "小红书", "抖音", "YY直播"):
                raise Exception(
                    f"{platform}专属解析失败。\n"
                    f"错误信息: {e}\n\n"
                    f"解析依赖浏览器自动化（Playwright）。\n"
                    f"可能原因：\n"
                    f"  1) Playwright 浏览器未安装或启动失败\n"
                    f"  2) 反爬拦截（请求过快）\n"
                    f"  3) 未登录账号（需要登录才能获取流地址）\n"
                    f"  4) 浏览器被关闭或超时\n\n"
                    f"解决方案：\n"
                    f"  - 确保电脑已安装 Chromium 浏览器\n"
                    f"  - 点击状态栏平台标注登录账号\n"
                    f"  - 点击解析后等待浏览器弹出并加载页面\n"
                    f"  - 如遇到验证码，请在弹出的浏览器中手动完成\n"
                    f"  - 等1-2分钟后重试"
                )
            log_msg = f"[专属解析] {platform}解析失败: {e}，降级到yt-dlp..."

    # 2. 降级到 yt-dlp
    info = fetch_streams_ytdlp(url, proxy)
    streams = parse_stream_info(info)
    unique = _dedup_streams(streams)
    sorted_streams = _tag_streams_with_quality(_sort_streams(unique))

    return {
        "platform": platform,
        "title": (info or {}).get("title", "") or "",
        "uploader": (info or {}).get("uploader", "") or "",
        "is_live": (info or {}).get("is_live", False),
        "streams": sorted_streams,
        "method_used": "yt-dlp降级解析",
    }


# ─── 清晰度分类 ──────────────────────────────────────────

# 清晰度优先级（从高到低）
QUALITY_LEVELS = {
    "UHD":  ("UHD",    "超高清", "#ff6b6b"),  # 4K / 超高清
    "OR4":  ("OR4",    "原画",   "#f0883e"),  # 原画
    "HD":   ("HD",     "高清",   "#3fb950"),  # 高清
    "SD":   ("SD",     "标清",   "#58a6ff"),  # 标清
    "LD":   ("LD",     "流畅",   "#8b949e"),  # 流畅/低清
    "OTHER":("OTHER",  "其他",   "#bc8cff"),
}

# quality 字段到清晰度分类的映射规则
_QUALITY_PATTERNS = [
    # (匹配关键词列表, 归属分类key)
    (["uhd", "4k", "超高清", "蓝光", "bd"],          "UHD"),
    (["or4", "origin", "原画", "full_hd", "full_hd1", "1080p"], "OR4"),
    (["hd1", "hd", "high", "高清", "720p", "high_def"], "HD"),
    (["sd1", "sd", "sd2", "标清", "480p", "standard"], "SD"),
    (["ld", "流畅", "低清", "low", "360p", "240p"],   "LD"),
]

def classify_quality(quality_str: str, url: str = "") -> str:
    """根据 quality 文本和 URL 推断清晰度分类，返回 QUALITY_LEVELS 的 key"""
    text = (quality_str + " " + url).lower()

    # 优先匹配关键词
    for keywords, level_key in _QUALITY_PATTERNS:
        for kw in keywords:
            if kw in text:
                return level_key

    # 通过分辨率数字推断
    nums = re.findall(r"(\d+)p", text)
    if nums:
        h = int(nums[0])
        if h >= 1080:
            return "OR4"
        elif h >= 720:
            return "HD"
        elif h >= 480:
            return "SD"
        else:
            return "LD"

    return "OTHER"


def _tag_streams_with_quality(streams: list) -> list:
    """给每个流打上 quality_tag 字段"""
    for s in streams:
        s["quality_tag"] = classify_quality(s.get("quality", ""), s.get("url", ""))
    return streams


def _dedup_streams(streams: list) -> list:
    seen = {}
    for s in streams:
        key = s["url"].split("?")[0]
        if key not in seen:
            seen[key] = s
    return list(seen.values())


def _sort_streams(streams: list) -> list:
    def key_fn(s):
        nums = re.findall(r"(\d+)p", s["quality"])
        return -int(nums[0]) if nums else 0
    return sorted(streams, key=key_fn)


# ─── GUI 界面 ────────────────────────────────────────────

# ─── 颜色系统 ────────────────────────────────────────────
class Colors:
    """主题色板"""
    BG_DARK = "#0d1117"
    BG_CARD = "#161b22"
    BG_INPUT = "#0d1117"
    BG_HOVER = "#1c2333"
    BG_RESULT_CARD = "#1a2232"
    BORDER = "#30363d"
    BORDER_FOCUS = "#58a6ff"
    TEXT_PRIMARY = "#e6edf3"
    TEXT_SECONDARY = "#8b949e"
    TEXT_MUTED = "#6e7681"
    ACCENT_BLUE = "#58a6ff"
    ACCENT_GREEN = "#3fb950"
    ACCENT_RED = "#f85149"
    ACCENT_ORANGE = "#d29922"
    ACCENT_PURPLE = "#bc8cff"
    ACCENT_CYAN = "#39d2c0"
    GRADIENT_START = "#58a6ff"
    GRADIENT_END = "#bc8cff"


# ═══════════════════════════════════════════════════════
# 本地代理服务（用于淘宝直播 alicdn 链接 OBS 推流）
# ═══════════════════════════════════════════════════════
#
# 淘宝直播的 FLV 流可能使用 HEVC/H.265 编码（codec_id=12），
# OBS 的 FLV 拉流不支持 HEVC（有声音无画面）。
#
# 方案：用 ffmpeg 做转码代理 —— 拉取原始流 + 注入 Referer + 转码为 H.264 + HTTP 输出。
# OBS 填本地 ffmpeg HTTP 地址即可。
#
# 优点：ffmpeg 全链路处理（解码 HEVC → 编码 H.264），兼容性最好
# 缺点：需要系统安装 ffmpeg（程序启动时自动检测）

def _find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件路径（嵌入式优先，系统兜底）。

    查找顺序（和嵌入式 Chromium 一致）：
    1. EXE 同目录 embedded_ffmpeg/ffmpeg.exe（便携部署）
    2. PyInstaller _MEIPASS 临时目录 embedded_ffmpeg/ffmpeg.exe
    3. %APPDATA%/LiveStreamFetcher/embedded_ffmpeg/ffmpeg.exe（已释放）
    4. 系统 PATH 中的 ffmpeg（shutil.which）
    """
    import shutil

    # 路径1: EXE 同目录（便携部署）
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    portable_path = os.path.join(exe_dir, "embedded_ffmpeg", "ffmpeg.exe")
    if os.path.isfile(portable_path):
        return portable_path

    # 路径2: PyInstaller _MEIPASS 临时目录
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        meipass_path = os.path.join(sys._MEIPASS, "embedded_ffmpeg", "ffmpeg.exe")
        if os.path.isfile(meipass_path):
            return meipass_path

    # 路径3: 已释放到 AppData
    appdata_path = os.path.join(os.environ.get("APPDATA", ""), "LiveStreamFetcher", "embedded_ffmpeg", "ffmpeg.exe")
    if os.path.isfile(appdata_path):
        return appdata_path

    # 路径4: 系统 PATH
    return shutil.which("ffmpeg") or ""


def _find_wechat_video_tool():
    """查找微信视频号下载工具 EXE：便携目录 → _MEIPASS → AppData。"""
    exe_name = "微信视频号下载工具2.6.exe"
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

    for base in [exe_dir,
                 os.path.join(sys._MEIPASS, "..") if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS') else "",
                 os.path.join(os.environ.get("APPDATA", ""), "LiveStreamFetcher")]:
        if not base:
            continue
        p = os.path.join(base, "wechat_video_tool", exe_name)
        if os.path.isfile(p):
            return p
    return None


def _extract_embedded_wechat_video_tool():
    """从 _MEIPASS 释放微信视频号下载工具到 AppData（首次运行）。"""
    if not getattr(sys, 'frozen', False) or not hasattr(sys, '_MEIPASS'):
        return None
    src_dir = os.path.join(sys._MEIPASS, "wechat_video_tool")
    if not os.path.isdir(src_dir):
        return None
    dst_dir = os.path.join(os.environ.get("APPDATA", ""), "LiveStreamFetcher", "wechat_video_tool")
    if os.path.isfile(os.path.join(dst_dir, "微信视频号下载工具2.6.exe")):
        return os.path.join(dst_dir, "微信视频号下载工具2.6.exe")
    print("[视频号工具] 首次运行，正在释放到本地...")
    try:
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        result = os.path.join(dst_dir, "微信视频号下载工具2.6.exe")
        print(f"[视频号工具] 释放完成: {result}")
        return result
    except Exception as e:
        print(f"[视频号工具] 释放失败: {e}")
        return None


def _ensure_wechat_video_tool():
    """确保微信视频号下载工具可用：查找 → 释放。返回 EXE 路径或 None。"""
    path = _find_wechat_video_tool()
    if path:
        return path
    return _extract_embedded_wechat_video_tool()


def _extract_embedded_ffmpeg():
    """从 PyInstaller _MEIPASS 释放 ffmpeg.exe 到 %APPDATA%/LiveStreamFetcher/embedded_ffmpeg/

    仅在首次运行时执行。返回 ffmpeg.exe 路径，失败返回 None。
    """
    if not getattr(sys, 'frozen', False) or not hasattr(sys, '_MEIPASS'):
        return None

    src_dir = os.path.join(sys._MEIPASS, "embedded_ffmpeg")
    if not os.path.isdir(src_dir):
        return None

    dst_base = os.path.join(os.environ.get("APPDATA", ""), "LiveStreamFetcher")
    dst_dir = os.path.join(dst_base, "embedded_ffmpeg")

    # 已存在则不重复释放
    if os.path.isfile(os.path.join(dst_dir, "ffmpeg.exe")):
        return os.path.join(dst_dir, "ffmpeg.exe")

    print("[ffmpeg] 首次运行，正在释放嵌入式 ffmpeg 到本地...")
    try:
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        result = os.path.join(dst_dir, "ffmpeg.exe")
        print(f"[ffmpeg] 释放完成: {result}")
        return result
    except Exception as e:
        print(f"[ffmpeg] 释放失败: {e}")
        return None


def _ensure_ffmpeg_ready() -> str:
    """确保 ffmpeg 可用：检查便携目录 → 检查 AppData → 从 _MEIPASS 释放 → 系统 PATH。

    返回 ffmpeg.exe 完整路径，不可用返回空字符串。
    """
    # 先查找已有路径（便携 / MEIPASS / AppData / 系统）
    path = _find_ffmpeg()
    if path:
        return path

    # 尝试从 _MEIPASS 释放到 AppData
    path = _extract_embedded_ffmpeg()
    if path:
        return path

    return ""

# FLV video codec ID 映射
_FLV_VIDEO_CODEC_NAMES = {
    1: "JPEG", 2: "Sorenson H.263", 3: "Screen Video",
    4: "VP6", 5: "VP6 Alpha", 6: "Screen Video V2",
    7: "AVC/H.264", 12: "HEVC/H.265",
}


class LocalStreamProxy:
    """本地转码代理：用 ffmpeg 将直播流转码为 H.264 FLV 后通过 HTTP 提供给 OBS。

    使用场景：
    - 淘宝直播 / 小红书直播 FLV 链接需要 Referer 头 → ffmpeg -headers 注入
    - 直播流可能用 HEVC 编码 → ffmpeg -c:v libx264 转码
    - OBS 不支持 HEVC FLV → 转码后 OBS 可正常播放

    前提：系统需安装 ffmpeg（版本 >= 4.0）

    用法：
        proxy = LocalStreamProxy(platform="淘宝直播")
        proxy.start("https://livecb.alicdn.com/...flv?auth_key=...")
        # OBS 中填入 proxy.get_url() 即可
        proxy.stop()
    """

    # 不同平台的 Referer 配置
    _PLATFORM_CONFIGS = {
        "淘宝直播": {
            "referer": "https://live.taobao.com/",
            "origin": "https://live.taobao.com",
        },
        "小红书": {
            "referer": "https://www.xiaohongshu.com/",
            "origin": "https://www.xiaohongshu.com",
        },
        "通用": {
            "referer": "",   # 转码工具手动输入的链接，不注入 Referer
            "origin": "",
        },
    }

    def __init__(self, port: int = 0, platform: str = "淘宝直播", codec_hint: str = ""):
        """port=0 表示随机端口；platform 指定平台用于设置正确的 Referer

        codec_hint: 从 pullConfig 等来源预先知道的编码（如 "h265"/"hevc"/"h264"），
                     有值时跳过 ffprobe 探测直接决定是否转码，避免阻塞 OBS 连接。
        """
        self._port = port
        self._actual_port = 0
        self._server = None
        self._thread = None
        self._target_url = ""
        self._running = False
        self._bytes_served = 0
        self._ffmpeg_process = None
        self._ffmpeg_available = bool(_ensure_ffmpeg_ready())
        self._is_hevc = False
        self._platform = platform
        self._codec_hint = (codec_hint or "").lower().strip()
        config = self._PLATFORM_CONFIGS.get(platform, self._PLATFORM_CONFIGS["淘宝直播"])
        self._referer = config["referer"]
        self._origin = config["origin"]

    def start(self, target_url: str) -> str:
        """启动代理服务，返回本地 URL。

        Args:
            target_url: 淘宝 alicdn 的原始流链接

        Returns:
            本地代理 URL，如 http://127.0.0.1:18888/live
        """
        self._target_url = target_url
        self._bytes_served = 0

        # 启动 HTTP 服务（随机端口）
        self._server = _StreamProxyHTTPServer(("127.0.0.1", self._port), self._handle_request)
        self._actual_port = self._server.server_address[1]
        self._running = True

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        return self.get_url()

    def get_url(self) -> str:
        """返回当前代理 URL"""
        return f"http://127.0.0.1:{self._actual_port}/live"

    def get_target_url(self) -> str:
        return self._target_url

    def is_running(self) -> bool:
        return self._running and self._server is not None

    def get_bytes_served(self) -> int:
        return self._bytes_served

    def is_hevc(self) -> bool:
        """当前转码是否因为 HEVC 编码"""
        return self._is_hevc

    def stop(self):
        """停止代理服务"""
        self._running = False
        if self._ffmpeg_process:
            try:
                self._ffmpeg_process.terminate()
                self._ffmpeg_process.wait(timeout=5)
            except Exception:
                try:
                    self._ffmpeg_process.kill()
                except Exception:
                    pass
            self._ffmpeg_process = None
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
        self._server = None
        self._thread = None

    def update_target(self, new_url: str):
        """更新目标 URL（用于链接刷新场景）"""
        self._target_url = new_url
        self._is_hevc = False

    def _detect_hevc(self, data: bytes) -> bool:
        """检测 FLV 数据是否包含 HEVC 编码的视频 tag。

        FLV Video Tag 的第一个字节：(FrameType << 4) | CodecID
        CodecID = 12 即 HEVC/H.265。

        Args:
            data: FLV 流的前几 KB 数据

        Returns:
            True 如果检测到 HEVC 视频 tag
        """
        if len(data) < 20 or data[:3] != b'FLV':
            return False

        # FLV header: 3(sig) + 1(ver) + 1(flags) + 4(prevTagSize0) = 9 bytes
        # 但有些实现 PrevTagSize0 不为 0，按 9 字节偏移
        offset = 9

        # 如果 offset 9 不是有效 tag type，尝试跳过
        while offset < min(len(data) - 11, 30):
            tag_type = data[offset]
            if tag_type in (8, 9, 18):
                break
            offset += 1

        for _ in range(30):  # 最多检查 30 个 tag
            if offset + 11 > len(data):
                break

            tag_type = data[offset]
            data_size = (data[offset+1] << 16) | (data[offset+2] << 8) | data[offset+3]

            if data_size > 5000000:  # 5MB 以上不合理，停止
                break

            if tag_type == 9 and offset + 12 <= len(data):
                # Video tag: 检查 codec_id
                fb = data[offset + 11]
                codec_id = fb & 0x0f
                if codec_id == 12:  # HEVC
                    return True
                if codec_id == 7:  # AVC/H.264
                    return False  # 确认是 H.264，不需要转码

            offset += 11 + data_size + 4

        return False

    def _handle_request(self, client_sock, method: str, path: str, headers: dict):
        """处理客户端请求。

        策略：
        - 小红书平台：统一走 ffmpeg 拉流（兼容性最好，稳定注入 Referer）
        - 其他平台（淘宝）：先探测编码，HEVC 走 ffmpeg，H.264 直接转发
        """
        print(f"[代理] 收到请求: {method} {path} (平台={self._platform})")
        if not self._target_url:
            self._send_error(client_sock, 503, "Proxy: no target URL set")
            return

        # 小红书统一走 ffmpeg（xhscdn.com CDN 兼容性问题 + 鉴权头需求）
        # HEVC 转码模式也统一走 ffmpeg
        if self._platform == "小红书" or self._codec_hint == "hevc":
            reason = "小红书平台" if self._platform == "小红书" else "HEVC转码模式"
            print(f"[代理] {reason}，统一使用 ffmpeg 拉流...")
            self._serve_via_ffmpeg(client_sock, force_transcode=(self._codec_hint == "hevc"))
            return

        try:
            req_headers = {
                "Referer": self._referer,
                "Origin": self._origin,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "identity",
            }

            if "Range" in headers:
                req_headers["Range"] = headers["Range"]

            print(f"[代理] 正在拉取上游: {self._target_url[:80]}...")
            resp = requests.request(
                method,
                self._target_url,
                headers=req_headers,
                stream=True,
                timeout=30,
                allow_redirects=True,
            )

            print(f"[代理] 上游响应: status={resp.status_code}, content-type={resp.headers.get('content-type', 'N/A')}, content-length={resp.headers.get('content-length', 'N/A')}")
            if resp.status_code != 200:
                self._send_error(client_sock, 502, f"Proxy: upstream returned {resp.status_code}")
                return

            # 读取前 50KB 用于检测
            probe_data = b''
            for chunk in resp.iter_content(chunk_size=8192):
                probe_data += chunk
                if len(probe_data) >= 50000:
                    break

            print(f"[代理] 探测数据: {len(probe_data)} bytes, 前3字节={probe_data[:3]}, FLV={probe_data[:3] == b'FLV'}")
            resp.close()  # 不再需要这个连接

            # 检测是否 HEVC
            if self._detect_hevc(probe_data):
                self._is_hevc = True
                print(f"[代理] 检测到 HEVC 编码，启动 ffmpeg 转码...")
                self._serve_via_ffmpeg(client_sock)
            else:
                self._is_hevc = False
                print(f"[代理] 非 HEVC 编码，直接转发...")
                # H.264 等兼容编码：重新拉流并直接转发（注入 Referer 头）
                self._serve_passthrough(client_sock, req_headers)

        except requests.exceptions.ConnectionError:
            self._send_error(client_sock, 502, "Proxy: upstream connection failed (auth_key expired?)")
        except requests.exceptions.Timeout:
            self._send_error(client_sock, 504, "Proxy: upstream timeout")
        except Exception as e:
            try:
                self._send_error(client_sock, 500, f"Proxy error: {e}")
            except Exception:
                pass

    def _serve_via_ffmpeg(self, client_sock, force_transcode: bool = True):
        """用 ffmpeg 直接拉取直播流并输出 FLV 给客户端。

        ffmpeg 自己拉流（通过 -headers 注入 Referer），不需要 Python 中转数据。

        Args:
            client_sock: 客户端 socket
            force_transcode: True=强制 libx264 转码（HEVC 场景）
                             False=先探测编码，H.264 则 copy（小红书智能模式）
        """
        if not self._ffmpeg_available:
            self._send_error(client_sock, 503,
                "ffmpeg not found!\n"
                "ffmpeg is required for stream proxy.")
            return

        ffmpeg_path = _ensure_ffmpeg_ready()
        print(f"[代理-ffmpeg] ffmpeg 路径: {ffmpeg_path}, 可用: {self._ffmpeg_available}")

        # 构建 ffmpeg 命令：
        # -headers: 注入 Referer 和 UA（CDN 需要）
        # -i URL: 直接拉取原始流
        # -f flv pipe:1: 输出 FLV 到 stdout → 写入 client_sock
        header_lines = []
        if self._referer:
            header_lines.append(f"Referer: {self._referer}")
        header_lines.append(
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        custom_headers = "\r\n".join(header_lines) + "\r\n"

        # 编码判断策略（按优先级）：
        #   1. codec_hint: 调用方从 pullConfig 预传的编码 → 零延迟，直接决定
        #   2. force_transcode: 强制转码参数 → 不探测
        #   3. ffprobe 探测: 小红书智能模式 fallback → 有网络耗时（3~15s），会阻塞 OBS 连接
        need_transcode = force_transcode
        if not need_transcode and self._codec_hint:
            # 策略1: 用预知编码直接判断，跳过 ffprobe（关键优化！）
            if self._codec_hint in ("h264", "avc", "h.264"):
                need_transcode = False
                self._is_hevc = False
                print(f"[代理-ffmpeg] codec_hint={self._codec_hint} → H.264 源流，使用 copy 模式（不转码）")
            elif self._codec_hint in ("hevc", "h265", "h.265"):
                need_transcode = True
                self._is_hevc = True
                print(f"[代理-ffmpeg] codec_hint={self._codec_hint} → HEVC 源流，使用 libx264 转码")
            else:
                # 未知 hint，fallback 到 ffprobe
                print(f"[代理-ffmpeg] codec_hint='{self._codec_hint}' 无法识别，fallback 到 ffprobe 探测")
        elif not force_transcode and self._platform == "小红书" and not self._codec_hint:
            # 策略3: 无 hint 时才走 ffprobe（旧逻辑，保留兼容）
            try:
                # ffprobe 通常和 ffmpeg 在同一目录
                import os
                if ffmpeg_path:
                    ffprobe_path = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe" + (".exe" if sys.platform == 'win32' else ""))
                else:
                    ffprobe_path = "ffprobe"
                probe_cmd = [
                    ffprobe_path,
                    "-hide_banner",
                    "-headers", custom_headers,
                    "-i", self._target_url,
                    "-v", "quiet",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=codec_name",
                    "-of", "csv=p=0",
                ]
                probe_result = subprocess.run(
                    probe_cmd, capture_output=True, timeout=15,
                    creationflags=0x08000000 if sys.platform == 'win32' else 0,
                )
                codec_name = probe_result.stdout.decode().strip()
                print(f"[代理-ffmpeg] ffprobe 探测到视频编码: '{codec_name}'")
                if codec_name == "h264":
                    need_transcode = False
                    self._is_hevc = False
                    print(f"[代理-ffmpeg] H.264 源流，使用 copy 模式（不转码）")
                elif codec_name in ("hevc", "h265"):
                    need_transcode = True
                    self._is_hevc = True
                    print(f"[代理-ffmpeg] HEVC 源流，使用 libx264 转码")
                else:
                    # 未知编码，默认转码保底
                    need_transcode = True
                    print(f"[代理-ffmpeg] 未知编码 '{codec_name}'，默认转码")
            except Exception as e:
                print(f"[代理-ffmpeg] ffprobe 编码探测失败: {e}，默认转码")
                need_transcode = True

        if need_transcode:
            self._is_hevc = True
            cmd = [
                ffmpeg_path,
                "-hide_banner", "-loglevel", "warning",
                "-headers", custom_headers,
                "-i", self._target_url,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-g", "30",           # 关键帧间隔 30 帧（~1秒），OBS seek 需要
                "-sc_threshold", "0", # 禁用场景切换检测，保证固定关键帧间隔
                "-c:a", "copy",
                "-f", "flv",
                "-flush_packets", "1",  # 立即刷新输出，降低延迟
                "pipe:1",
            ]
        else:
            self._is_hevc = False
            cmd = [
                ffmpeg_path,
                "-hide_banner", "-loglevel", "warning",
                "-headers", custom_headers,
                "-i", self._target_url,
                "-c:v", "copy",
                "-c:a", "copy",
                "-f", "flv",
                "-flush_packets", "1",
                "pipe:1",
            ]

        print(f"[代理-ffmpeg] 命令: {' '.join(cmd[:8])}...")
        try:
            creation_flags = 0
            if sys.platform == 'win32':
                creation_flags = 0x08000000  # CREATE_NO_WINDOW

            self._ffmpeg_process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creation_flags,
            )
            print(f"[代理-ffmpeg] 进程已启动: pid={self._ffmpeg_process.pid}")

            # 发送 HTTP 响应头给 OBS
            response_header = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: video/x-flv\r\n"
                "Cache-Control: no-cache\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            client_sock.sendall(response_header.encode("utf-8"))

            # 读 ffmpeg stdout 写入客户端
            total = 0
            while self._running:
                chunk = self._ffmpeg_process.stdout.read(65536)
                if not chunk:
                    print(f"[代理-ffmpeg] ffmpeg 输出结束 (已发送 {total} bytes)")
                    break
                try:
                    client_sock.sendall(chunk)
                    self._bytes_served += len(chunk)
                    total += len(chunk)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    print(f"[代理-ffmpeg] 客户端断开 (已发送 {total} bytes)")
                    break

            # 等待 ffmpeg 结束，检查 stderr
            self._ffmpeg_process.wait(timeout=5)
            stderr_output = self._ffmpeg_process.stderr.read().decode(errors="replace")
            if stderr_output.strip():
                print(f"[代理-ffmpeg] stderr: {stderr_output[:500]}")

        except FileNotFoundError:
            self._send_error(client_sock, 503, f"ffmpeg not found: '{ffmpeg_path}'")
        except Exception as e:
            try:
                self._send_error(client_sock, 500, f"ffmpeg error: {e}")
            except Exception:
                pass
        finally:
            if self._ffmpeg_process:
                try:
                    self._ffmpeg_process.terminate()
                except Exception:
                    pass
            self._ffmpeg_process = None

    def _serve_passthrough(self, client_sock, req_headers: dict):
        """H.264 等兼容编码：直接转发，注入 Referer/Origin 头。"""
        try:
            resp = requests.request(
                "GET",
                self._target_url,
                headers=req_headers,
                stream=True,
                timeout=30,
                allow_redirects=True,
            )

            print(f"[代理-passthrough] 上游响应: status={resp.status_code}")
            if resp.status_code != 200:
                self._send_error(client_sock, 502, f"Proxy: upstream {resp.status_code}")
                return

            # 发送响应头
            status_text = {200: "OK", 206: "Partial Content", 302: "Found"}.get(resp.status_code, "Unknown")
            response_header = f"HTTP/1.1 {resp.status_code} {status_text}\r\n"
            for k, v in resp.headers.items():
                kl = k.lower()
                if kl in ("transfer-encoding", "connection", "keep-alive"):
                    continue
                response_header += f"{k}: {v}\r\n"
            response_header += "\r\n"
            client_sock.sendall(response_header.encode("utf-8"))
            print(f"[代理-passthrough] 已发送响应头给客户端，开始流式转发...")

            # 流式转发
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if not self._running:
                    break
                try:
                    client_sock.sendall(chunk)
                    self._bytes_served += len(chunk)
                    total += len(chunk)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
            print(f"[代理-passthrough] 转发完成: 总共 {total} bytes")

        except requests.exceptions.ConnectionError:
            self._send_error(client_sock, 502, "Proxy: upstream connection failed")
        except requests.exceptions.Timeout:
            self._send_error(client_sock, 504, "Proxy: upstream timeout")
        except Exception as e:
            try:
                self._send_error(client_sock, 500, f"Proxy error: {e}")
            except Exception:
                pass

    def _send_error(self, client_sock, status: int, message: str):
        """发送错误响应"""
        try:
            status_text = {502: "Bad Gateway", 503: "Service Unavailable",
                           504: "Gateway Timeout", 500: "Internal Server Error"}.get(status, "Error")
            body = message.encode("utf-8")
            resp = f"HTTP/1.1 {status} {status_text}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\n\r\n"
            client_sock.sendall(resp.encode("utf-8") + body)
        except Exception:
            pass


class _StreamProxyHTTPServer:
    """轻量级 HTTP 服务器，用于本地流代理。

    不使用 http.server 标准库（它在 Python 线程中不友好），
    改用 socket 直接实现 HTTP/1.1 流式转发。
    """

    def __init__(self, address, handler):
        self.address = address
        self.handler = handler  # handler(method, path, headers) -> (status, headers, body)
        self._running = False

        # 在 __init__ 中就完成 bind，这样 server_address 属性立即可用
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(self.address)
        self._addr = self.server_socket.getsockname()
        self.server_socket.listen(5)
        self.server_socket.settimeout(1.0)

    @property
    def server_address(self):
        return self._addr

    def serve_forever(self):
        self._running = True

        while self._running:
            try:
                client_sock, client_addr = self.server_socket.accept()
                t = threading.Thread(target=self._handle_client, args=(client_sock,), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except OSError:
                break

    def shutdown(self):
        self._running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass

    def _handle_client(self, client_sock):
        """处理单个客户端连接"""
        try:
            client_sock.settimeout(30)
            # 读取请求头
            request_data = b""
            while True:
                chunk = client_sock.recv(4096)
                if not chunk:
                    return
                request_data += chunk
                if b"\r\n\r\n" in request_data:
                    break

            # 解析 HTTP 请求
            request_str = request_data.decode("utf-8", errors="replace")
            lines = request_str.split("\r\n")
            if not lines:
                client_sock.close()
                return

            # 请求行：GET /live HTTP/1.1
            request_line = lines[0]
            parts = request_line.split(" ")
            if len(parts) < 2:
                client_sock.close()
                return
            method = parts[0]
            path = parts[1]

            # 解析请求头
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip()] = v.strip()

            # 调用 handler（直接传入 client_sock，由 handler 流式写入）
            self.handler(client_sock, method, path, headers)

        except Exception:
            pass
        finally:
            try:
                client_sock.close()
            except Exception:
                pass


class LiveStreamFetcherApp:
    """多平台直播视频流获取工具 - 重构UI"""

    def __init__(self, root):
        self.root = root
        self.root.title("LiveStreamFetcher v6.3 — by LONGSHAO")
        self.root.geometry("960x780")
        self.root.minsize(860, 680)
        self.root.configure(bg=Colors.BG_DARK)

        # 圆角窗口（Windows 10+）
        try:
            from ctypes import windll
            windll.dwmapi.DwmSetWindowAttribute(
                windll.user32.GetParent(self.root.winfo_id()),
                20, byref := __import__('ctypes').byref(__import__('ctypes').c_int(2)), 4)
        except Exception:
            pass

        self._last_result = None
        self._last_stream_urls = []
        self._all_streams = []  # 存储所有流数据（含分类信息）
        self._stream_cards = []
        self._filter_var = tk.StringVar(value="全部")  # 当前筛选分类
        self._filter_dimension = "quality"  # 当前筛选维度: "quality" | "format"

        # 本地代理（淘宝直播 alicdn 链接用）
        self._stream_proxies = {}  # {原始流URL: LocalStreamProxy 实例}
        self._proxy_urls = {}  # {原始流URL: 代理本地URL}
        self._proxy_ready = False  # 代理服务是否已启动
        self._proxy_hevc_checked = False  # HEVC 检测是否已完成
        self._proxy_platform = ""  # 当前代理的平台（淘宝直播/小红书）
        self._ks_login_status = "never"  # "logged_in" | "never" | "expired"
        # 淘宝登录状态跟踪
        self._tb_login_status = "never"  # "logged_in" | "never" | "expired"
        # 小红书登录状态跟踪
        self._xhs_login_status = "never"  # "logged_in" | "never" | "expired"
        # 抖音登录状态跟踪
        self._dy_login_status = "never"  # "logged_in" | "never" | "expired"

        self._build_ui()
        # 初始化时检测快手/淘宝/小红书/抖音登录状态
        self._refresh_ks_login_display()
        self._refresh_tb_login_display()
        self._refresh_xhs_login_display()
        self._refresh_dy_login_display()

    def _build_ui(self):
        # ═══ 顶部标题栏 ═══
        header = tk.Frame(self.root, bg=Colors.BG_CARD, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        # 左侧标题
        tk.Label(
            header, text="⬡  LiveStreamFetcher",
            font=("Microsoft YaHei UI", 15, "bold"),
            bg=Colors.BG_CARD, fg=Colors.ACCENT_BLUE
        ).pack(side="left", padx=(20, 8))

        tk.Label(
            header, text="v6.3",
            font=("Consolas", 10),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED
        ).pack(side="left")

        # 右侧支持平台入口（超链接按钮）
        platforms_frame = tk.Frame(header, bg=Colors.BG_CARD)
        platforms_frame.pack(side="right", padx=(10, 20))

        # 导航链接定义：(显示名称, URL, 背景色, 文字色)
        nav_links = [
            ("抖音", "https://www.douyin.com/", "#FE2C55", "white"),
            ("快手", "https://live.kuaishou.com/", "#FF6A00", "white"),
            ("小红书", "https://www.xiaohongshu.com/livelist", "#FE2C55", "white"),
            ("淘宝", "https://tbzb.taobao.com/", "#FF6A00", "white"),
            ("YY直播", "https://www.yy.com/", "#FFD700", "#1a1a2e"),
            ("捐赠", "https://ifdian.net/a/livestreamfetcher", "#FF6B6B", "white"),
        ]
        for i, (name, url, bg_color, fg_color) in enumerate(nav_links):
            btn = tk.Label(
                platforms_frame, text=f"  {name}  ",
                font=("Microsoft YaHei UI", 9),
                bg=bg_color, fg=fg_color,
                padx=8, pady=3,
                cursor="hand2",
                relief="flat",
            )
            btn.pack(side="left", padx=3)
            btn.bind("<Button-1>", lambda e, u=url: self._open_url_with_chromium(u))
            btn.bind("<Enter>", lambda e, b=btn, c=bg_color, fc=fg_color: b.configure(bg=self._lighten_color(c), font=("Microsoft YaHei UI", 9, "underline")))
            btn.bind("<Leave>", lambda e, b=btn, c=bg_color, fc=fg_color: b.configure(bg=c, fg=fc, font=("Microsoft YaHei UI", 9)))

        # ═══ 分隔线 ═══
        tk.Frame(self.root, bg=Colors.BORDER, height=1).pack(fill="x")

        # ═══ 输入区域 ═══
        input_area = tk.Frame(self.root, bg=Colors.BG_DARK)
        input_area.pack(fill="x", padx=24, pady=(16, 8))

        # URL 输入行
        url_row = tk.Frame(input_area, bg=Colors.BG_DARK)
        url_row.pack(fill="x")

        tk.Label(
            url_row, text="直播间链接",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=Colors.BG_DARK, fg=Colors.TEXT_PRIMARY
        ).pack(side="left", padx=(0, 10))

        # URL 输入框容器
        url_container = tk.Frame(url_row, bg=Colors.BORDER, bd=1, relief="solid")
        url_container.pack(side="left", fill="x", expand=True, ipady=1)

        self.url_var = tk.StringVar()
        self.url_var.trace_add("write", self._on_url_change)
        self.url_entry = tk.Entry(
            url_container, textvariable=self.url_var,
            font=("Consolas", 11),
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.ACCENT_BLUE,
            selectbackground=Colors.ACCENT_BLUE,
            selectforeground="#ffffff",
            relief="flat", bd=0,
            highlightthickness=0,
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(2, 0), pady=6)

        # 清除按钮（×）
        self._url_clear_btn = tk.Label(
            url_container, text="×", font=("Arial", 12),
            bg=Colors.BORDER, fg=Colors.TEXT_SECONDARY,
            cursor="hand2",
        )
        self._url_clear_btn.pack(side="right", padx=(0, 4), pady=6)
        self._url_clear_btn.bind("<Button-1>", lambda e: self.url_var.set(""))
        self._url_clear_btn.bind("<Enter>", lambda e: (
            self._url_clear_btn.config(fg=Colors.ACCENT_RED),
            self._url_clear_btn.config(bg=Colors.BORDER)
        ))
        self._url_clear_btn.bind("<Leave>", lambda e: (
            self._url_clear_btn.config(fg=Colors.TEXT_SECONDARY),
            self._url_clear_btn.config(bg=Colors.BORDER)
        ))

        # 按钮行
        btn_row = tk.Frame(input_area, bg=Colors.BG_DARK)
        btn_row.pack(fill="x", pady=(10, 0))

        # 获取按钮
        self.fetch_btn = tk.Button(
            btn_row, text="  获取流链接  ",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg="#238636", fg="white",
            activebackground="#2ea043", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=20, pady=7,
            command=self._on_fetch,
        )
        self.fetch_btn.pack(side="left")
        self.fetch_btn.bind("<Enter>", lambda e: self.fetch_btn.configure(bg="#2ea043"))
        self.fetch_btn.bind("<Leave>", lambda e: self.fetch_btn.configure(bg="#238636"))

        # ── HEVC→H264 转码按钮 ──
        self.transcode_btn = tk.Button(
            btn_row, text="  HEVC 转码  ",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg="#8b5cf6", fg="white",
            activebackground="#7c3aed", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=20, pady=7,
            command=self._on_transcode_click,
        )
        self.transcode_btn.pack(side="left", padx=(8, 0))
        self.transcode_btn.bind("<Enter>", lambda e: self.transcode_btn.configure(bg="#7c3aed"))
        self.transcode_btn.bind("<Leave>", lambda e: self.transcode_btn.configure(bg="#8b5cf6"))

        # 复制全部按钮
        self.copy_all_btn = tk.Button(
            btn_row, text="  复制全部链接  ",
            font=("Microsoft YaHei UI", 10),
            bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
            activebackground=Colors.BG_HOVER, activeforeground=Colors.TEXT_PRIMARY,
            relief="flat", bd=0, cursor="hand2", padx=16, pady=7,
            command=self._on_copy_all,
        )
        self.copy_all_btn.pack(side="left", padx=(8, 0))

        # ── 系统代理开关按钮（独立控制）──
        self.proxy_toggle_btn = tk.Button(
            btn_row, text="  系统代理  ",
            font=("Microsoft YaHei UI", 10),
            bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
            activebackground=Colors.BG_HOVER, activeforeground=Colors.TEXT_PRIMARY,
            relief="flat", bd=0, cursor="hand2", padx=14, pady=7,
            command=self._on_toggle_system_proxy,
        )
        self.proxy_toggle_btn.pack(side="left", padx=(8, 0))
        # 初始状态检测
        self.root.after(500, self._refresh_proxy_btn_state)
        self.copy_all_btn.bind("<Enter>", lambda e: self.copy_all_btn.configure(bg=Colors.BG_HOVER))
        self.copy_all_btn.bind("<Leave>", lambda e: self.copy_all_btn.configure(bg=Colors.BG_CARD))

        # ── 视频号工具按钮 ──
        self.wct_btn = tk.Button(
            btn_row, text="  视频号工具  ",
            font=("Microsoft YaHei UI", 10),
            bg="#07c160", fg="white",
            activebackground="#06ad56", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=16, pady=7,
            command=self._on_open_wechat_video_tool,
        )
        self.wct_btn.pack(side="right", padx=(0, 8))
        self.wct_btn.bind("<Enter>", lambda e: self.wct_btn.configure(bg="#06ad56"))
        self.wct_btn.bind("<Leave>", lambda e: self.wct_btn.configure(bg="#07c160"))

        # 代理设置（折叠式）
        proxy_toggle = tk.Label(
            btn_row, text="代理设置 ▸",
            font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_DARK, fg=Colors.TEXT_MUTED,
            cursor="hand2",
        )
        proxy_toggle.pack(side="right", padx=(0, 4))
        proxy_toggle.bind("<Button-1>", self._toggle_proxy)

        self.proxy_frame = tk.Frame(input_area, bg=Colors.BG_DARK)
        tk.Label(
            self.proxy_frame, text="代理地址",
            font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_DARK, fg=Colors.TEXT_SECONDARY
        ).pack(side="left", padx=(0, 6))
        self.proxy_var = tk.StringVar()
        proxy_container = tk.Frame(self.proxy_frame, bg=Colors.BORDER, bd=1, relief="solid")
        proxy_container.pack(side="left", fill="x", expand=True)
        self.proxy_entry = tk.Entry(
            proxy_container, textvariable=self.proxy_var,
            font=("Consolas", 9),
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.ACCENT_BLUE,
            relief="flat", bd=0, highlightthickness=0,
        )
        self.proxy_entry.pack(fill="x", expand=True, padx=2, pady=4)
        # 默认隐藏

        # ═══ 状态栏 ═══
        status_bar = tk.Frame(self.root, bg=Colors.BG_CARD, height=32)
        status_bar.pack(fill="x")
        status_bar.pack_propagate(False)

        self.status_icon = tk.Label(
            status_bar, text="●",
            font=("Consolas", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        )
        self.status_icon.pack(side="left", padx=(16, 6))

        self.status_var = tk.StringVar(value="就绪 — 粘贴直播间链接开始解析")
        tk.Label(
            status_bar, textvariable=self.status_var,
            font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY
        ).pack(side="left")

        # 右侧：淘宝登录状态标注
        self.tb_login_frame = tk.Frame(status_bar, bg=Colors.BG_CARD)
        self.tb_login_frame.pack(side="right", padx=(0, 4))

        self.tb_login_icon = tk.Label(
            self.tb_login_frame, text="",
            font=("Consolas", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        )
        self.tb_login_icon.pack(side="left", padx=(0, 2))

        self.tb_login_label = tk.Label(
            self.tb_login_frame, text="",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
            cursor="hand2",
        )
        self.tb_login_label.pack(side="left")
        self.tb_login_label.bind("<Button-1>", self._on_tb_login_click)

        # 淘宝 cookie 路径提示
        self.tb_cookie_dir = _get_tb_browser_data_dir()

        # 右侧：小红书登录状态标注
        self.xhs_login_frame = tk.Frame(status_bar, bg=Colors.BG_CARD)
        self.xhs_login_frame.pack(side="right", padx=(0, 4))

        self.xhs_login_icon = tk.Label(
            self.xhs_login_frame, text="",
            font=("Consolas", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        )
        self.xhs_login_icon.pack(side="left", padx=(0, 2))

        self.xhs_login_label = tk.Label(
            self.xhs_login_frame, text="",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
            cursor="hand2",
        )
        self.xhs_login_label.pack(side="left")
        self.xhs_login_label.bind("<Button-1>", self._on_xhs_login_click)

        # 小红书 cookie 路径提示
        self.xhs_cookie_dir = _get_xhs_browser_data_dir()

        # 右侧：抖音登录状态标注
        self.dy_login_frame = tk.Frame(status_bar, bg=Colors.BG_CARD)
        self.dy_login_frame.pack(side="right", padx=(0, 4))

        self.dy_login_icon = tk.Label(
            self.dy_login_frame, text="",
            font=("Consolas", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        )
        self.dy_login_icon.pack(side="left", padx=(0, 2))

        self.dy_login_label = tk.Label(
            self.dy_login_frame, text="",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
            cursor="hand2",
        )
        self.dy_login_label.pack(side="left")
        self.dy_login_label.bind("<Button-1>", self._on_dy_login_click)

        # 抖音 cookie 路径提示
        self.dy_cookie_dir = _get_dy_browser_data_dir()

        # 右侧：快手登录状态标注
        self.ks_login_frame = tk.Frame(status_bar, bg=Colors.BG_CARD)
        self.ks_login_frame.pack(side="right", padx=(0, 4))

        self.ks_login_icon = tk.Label(
            self.ks_login_frame, text="",
            font=("Consolas", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        )
        self.ks_login_icon.pack(side="left", padx=(0, 2))

        self.ks_login_label = tk.Label(
            self.ks_login_frame, text="",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
            cursor="hand2",
        )
        self.ks_login_label.pack(side="left")
        self.ks_login_label.bind("<Button-1>", self._on_ks_login_click)

        # 快手 cookie 路径提示
        self.ks_cookie_dir = _get_ks_browser_data_dir()

        # 右侧作者信息
        tk.Label(
            status_bar, text="by LONGSHAO",
            font=("Consolas", 9),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        ).pack(side="right", padx=16)

        # ═══ 底部访客 WebView（嵌入 WidgetStore 页面截图） ═══
        self._create_visitor_webview()

        # ═══ 结果区域（可滚动） ═══
        self.result_container = tk.Frame(self.root, bg=Colors.BG_DARK)
        self.result_container.pack(fill="both", expand=True, padx=16, pady=(12, 16))

        # 使用 Canvas + Frame 实现平滑滚动
        self.result_canvas = tk.Canvas(
            self.result_container, bg=Colors.BG_DARK,
            highlightthickness=0, bd=0,
        )
        scrollbar = tk.Scrollbar(
            self.result_container, orient="vertical",
            command=self.result_canvas.yview,
            bg=Colors.BG_DARK, troughcolor=Colors.BG_DARK,
            activebackground=Colors.ACCENT_BLUE,
        )
        self.result_inner = tk.Frame(self.result_canvas, bg=Colors.BG_DARK)

        self.result_inner.bind(
            "<Configure>",
            lambda e: self.result_canvas.configure(scrollregion=self.result_canvas.bbox("all"))
        )

        self.canvas_window = self.result_canvas.create_window(
            (0, 0), window=self.result_inner, anchor="nw"
        )
        self.result_canvas.configure(yscrollcommand=scrollbar.set)

        # Canvas 自适应宽度
        self.result_canvas.bind(
            "<Configure>",
            lambda e: self.result_canvas.itemconfig(self.canvas_window, width=e.width)
        )

        # 鼠标滚轮
        self.result_canvas.bind("<Enter>", self._bind_mousewheel)
        self.result_canvas.bind("<Leave>", self._unbind_mousewheel)

        scrollbar.pack(side="right", fill="y")
        self.result_canvas.pack(side="left", fill="both", expand=True)

        # 占位提示
        self._show_placeholder()

    # ─── 鼠标滚轮绑定 ───
    def _bind_mousewheel(self, event):
        self.result_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.result_canvas.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.result_canvas.bind_all("<Button-5>", self._on_mousewheel_linux)

    def _unbind_mousewheel(self, event):
        self.result_canvas.unbind_all("<MouseWheel>")
        self.result_canvas.unbind_all("<Button-4>")
        self.result_canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event):
        self.result_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_linux(self, event):
        if event.num == 4:
            self.result_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.result_canvas.yview_scroll(1, "units")

    # ─── 底部访客 WebView（嵌入真实浏览器窗口） ───
    def _create_visitor_webview(self):
        """底部嵌入 WidgetStore 页面，用 Playwright 非 headless + ctypes 嵌入"""
        WIDGET_HEIGHT = 80
        BORDER_COLOR = "#21262d"

        # 外层容器
        visitor_outer = tk.Frame(self.root, bg=BORDER_COLOR)
        visitor_outer.pack(fill="x", padx=16, pady=(0, 6))

        # 标题栏
        title_bar = tk.Frame(visitor_outer, bg="#161b22", height=18)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)
        tk.Label(
            title_bar, text="  访客统计",
            font=("Microsoft YaHei UI", 7),
            bg="#161b22", fg="#8b949e", anchor="w",
        ).pack(side="left", padx=4)

        # 浏览器嵌入区域
        self._visitor_frame = tk.Frame(visitor_outer, bg="#0d1117", height=WIDGET_HEIGHT)
        self._visitor_frame.pack(fill="both", expand=True)
        self._visitor_frame.pack_propagate(False)

        # 占位提示
        self._visitor_placeholder = tk.Label(
            self._visitor_frame, text="  正在加载访客统计...",
            font=("Microsoft YaHei UI", 9),
            bg="#0d1117", fg=Colors.TEXT_MUTED, anchor="w",
        )
        self._visitor_placeholder.pack(fill="both", expand=True)

        # 后台线程启动浏览器并嵌入
        threading.Thread(
            target=self._embed_browser_widget,
            daemon=True,
        ).start()

    def _embed_browser_widget(self):
        """后台线程：启动 Playwright 浏览器，通过 Win32 API 嵌入 tkinter Frame"""
        import ctypes
        from ctypes import wintypes
        from playwright.sync_api import sync_playwright

        widget_url = (
            "https://cn.widgetstore.net/view/index.html"
            "?q=5b049cc8622189440f31d6307d40e568"
            ".b3c6c3d569de54420449a20254382ae6"
        )

        try:
            # 等待 tkinter 完全渲染
            time.sleep(1)
            self.root.update_idletasks()

            parent_hwnd = self._visitor_frame.winfo_id()
            if not parent_hwnd or parent_hwnd <= 0:
                self.root.after(200, self._embed_browser_widget)
                return

            fw = self._visitor_frame.winfo_width()
            fh = self._visitor_frame.winfo_height()
            if fw < 10 or fh < 10:
                # Frame 尺寸还没就绪，延迟重试
                time.sleep(0.5)
                self.root.update_idletasks()
                fw = self._visitor_frame.winfo_width()
                fh = self._visitor_frame.winfo_height()

            # 记录嵌入前的所有窗口句柄
            _hwnds_before = set()
            def _enum_before(hwnd, _):
                _hwnds_before.add(hwnd)
                return True
            _EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            ctypes.windll.user32.EnumWindows(_EnumProc(_enum_before), 0)

            # 启动 Playwright（使用嵌入式 Chromium，如有）
            browser_path = _ensure_chromium_ready()
            launch_args = {
                "headless": False,
                "args": [
                    "--no-sandbox", "--disable-gpu",
                    "--window-size=960,80",
                    "--no-proxy-server",
                    "--disable-infobars",
                ],
            }
            if browser_path:
                exe_path = os.path.join(browser_path, "chrome.exe")
                if os.path.isfile(exe_path):
                    launch_args["executable_path"] = exe_path

            with sync_playwright() as p:
                browser = p.chromium.launch(**launch_args)
                page = browser.new_page(viewport={"width": 960, "height": 80})
                page.goto(widget_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)

                # 枚举新出现的窗口
                _new_windows = []
                def _enum_after(hwnd, _):
                    if hwnd not in _hwnds_before:
                        style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)
                        if style & 0x10000000:  # visible
                            cls_buf = ctypes.create_unicode_buffer(256)
                            ctypes.windll.user32.GetClassNameW(hwnd, cls_buf, 256)
                            title_buf = ctypes.create_unicode_buffer(512)
                            ctypes.windll.user32.GetWindowTextW(hwnd, title_buf, 512)
                            rect = wintypes.RECT()
                            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                            _new_windows.append({
                                "hwnd": hwnd,
                                "title": title_buf.value,
                                "class": cls_buf.value,
                                "w": rect.right - rect.left,
                                "h": rect.bottom - rect.top,
                            })
                    return True

                ctypes.windll.user32.EnumWindows(_EnumProc(_enum_after), 0)

                # 选择尺寸最大的 Chrome_WidgetWin_1 窗口（主窗口）
                chrome_windows = [
                    w for w in _new_windows
                    if "Chrome_WidgetWin_1" in w["class"]
                ]

                if not chrome_windows:
                    # 尝试其他 Chromium 窗口类名
                    chrome_windows = [
                        w for w in _new_windows
                        if "Chrome" in w["class"] or "Chromium" in w["class"]
                    ]

                if chrome_windows:
                    # 取宽度最大的（主窗口通常比翻译弹窗大）
                    target = max(chrome_windows, key=lambda w: w["w"])
                    browser_hwnd = target["hwnd"]

                    # 移除占位提示
                    self.root.after(0, self._visitor_placeholder.destroy)

                    # 修改窗口样式：从独立窗口变为子窗口
                    GWL_STYLE = -16
                    WS_VISIBLE = 0x10000000
                    WS_CHILD = 0x40000000
                    WS_CLIPCHILDREN = 0x02000000

                    style = ctypes.windll.user32.GetWindowLongW(browser_hwnd, GWL_STYLE)
                    style &= ~WS_VISIBLE
                    style |= WS_CHILD | WS_CLIPCHILDREN
                    ctypes.windll.user32.SetWindowLongW(browser_hwnd, GWL_STYLE, style)

                    # 设为 tkinter Frame 的子窗口
                    ctypes.windll.user32.SetParent(browser_hwnd, parent_hwnd)
                    ctypes.windll.user32.MoveWindow(browser_hwnd, 0, 0, fw, fh, True)
                    ctypes.windll.user32.ShowWindow(browser_hwnd, 9)

                    # 监听 Frame 大小变化，同步调整浏览器窗口
                    def _on_resize(event):
                        if event.widget == self._visitor_frame:
                            try:
                                ctypes.windll.user32.MoveWindow(
                                    browser_hwnd, 0, 0,
                                    event.width, event.height, True,
                                )
                            except Exception:
                                pass
                    self._visitor_frame.bind("<Configure>", _on_resize)

                    # 隐藏滚动条
                    try:
                        page.evaluate(
                            "document.documentElement.style.overflow='hidden';"
                            "document.body.style.overflow='hidden';"
                        )
                    except Exception:
                        pass

                    # 保持线程运行防止浏览器被回收
                    self._visitor_stop = threading.Event()
                    self._visitor_stop.wait()
                else:
                    self.root.after(0, lambda: self._visitor_placeholder.configure(
                        text="  浏览器嵌入失败（未找到窗口）", fg="#ff6b6b",
                    ))
                    browser.close()

        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self.root.after(0, lambda: self._visitor_placeholder.configure(
                    text=f"  加载失败: {e}", fg="#ff6b6b",
                ))
            except Exception:
                pass

    # ─── 颜色辅助 ───
    @staticmethod
    def _lighten_color(hex_color):
        """将颜色变亮，用于 hover 效果"""
        try:
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            factor = 0.7
            r = min(255, int(r + (255 - r) * factor))
            g = min(255, int(g + (255 - g) * factor))
            b = min(255, int(b + (255 - b) * factor))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    # ─── 浏览器调用 ───
    @staticmethod
    def _open_url_with_chromium(url):
        """使用项目封装的 Chromium 浏览器打开 URL，失败则降级到系统浏览器"""
        import subprocess
        chrome_exe = _ensure_chromium_ready()
        if chrome_exe:
            exe_path = os.path.join(chrome_exe, "chrome.exe")
            if os.path.isfile(exe_path):
                try:
                    subprocess.Popen(
                        [exe_path, "--no-first-run", "--no-default-browser-check", url],
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                    )
                    return
                except Exception:
                    pass
        # 降级到系统默认浏览器
        __import__("webbrowser").open(url)

    # ─── 占位提示 ───
    def _show_placeholder(self):
        self._clear_result()
        placeholder = tk.Frame(self.result_inner, bg=Colors.BG_DARK)
        placeholder.pack(fill="both", expand=True, pady=60)

        tk.Label(
            placeholder, text="⬡",
            font=("Segoe UI", 48),
            bg=Colors.BG_DARK, fg=Colors.BORDER,
        ).pack()

        tk.Label(
            placeholder, text="粘贴直播间链接，点击「获取流链接」开始解析",
            font=("Microsoft YaHei UI", 11),
            bg=Colors.BG_DARK, fg=Colors.TEXT_MUTED,
        ).pack(pady=(16, 4))

        tk.Label(
            placeholder, text="支持抖音 · 快手 · 小红书 · 淘宝直播 · YY直播  |  视频号请点击「视频号抓取」按钮",
            font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_DARK, fg=Colors.TEXT_MUTED,
        ).pack()

        # ── 平台官网入口 ──
        link_row = tk.Frame(placeholder, bg=Colors.BG_DARK)
        link_row.pack(pady=(12, 0))
        tk.Label(
            link_row, text="没有链接？去平台搜索直播间：",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_DARK, fg=Colors.TEXT_MUTED,
        ).pack(side="left")
        platform_links = [
            ("抖音直播", "https://live.douyin.com/", "#FE2C55"),
            ("快手直播", "https://live.kuaishou.com/", "#FF6A00"),
            ("小红书直播", "https://www.xiaohongshu.com/livelist", "#FE2C55"),
            ("淘宝直播", "https://tbzb.taobao.com/", "#FF6A00"),
            ("YY直播", "https://www.yy.com/", "#FFD700"),
        ]
        for i, (name, url, color) in enumerate(platform_links):
            lbl = tk.Label(
                link_row, text=f"[{name}]",
                font=("Microsoft YaHei UI", 8, "underline"),
                bg=Colors.BG_DARK, fg=color,
                cursor="hand2",
            )
            lbl.pack(side="left", padx=3)
            lbl.bind("<Button-1>", lambda e, u=url: self._open_url_with_chromium(u))
            lbl.bind("<Enter>", lambda e, l=lbl, c=color: l.configure(fg=self._lighten_color(c)))
            lbl.bind("<Leave>", lambda e, l=lbl, c=color: l.configure(fg=c))

        # ── 快手操作指引卡片 ──
        ks_guide = tk.Frame(
            placeholder, bg=Colors.BG_CARD,
            highlightbackground=Colors.BORDER, highlightthickness=1,
        )
        ks_guide.pack(pady=(40, 0), padx=60, fill="x", ipady=10)

        tk.Label(
            ks_guide, text="  快手直播 · 操作指引  ",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=Colors.ACCENT_ORANGE, fg="white", padx=10, pady=2,
        ).pack(pady=(10, 8))

        tips = [
            ("1.", "粘贴快手直播链接，点击「获取流链接」"),
            ("2.", "等待浏览器自动弹出（Edge 或 Chrome），不要关闭"),
            ("3.", "如页面出现验证码，在弹出的浏览器中手动完成"),
            ("4.", "页面加载完成后工具会自动提取直播流地址"),
        ]
        for num, tip in tips:
            row = tk.Frame(ks_guide, bg=Colors.BG_CARD)
            row.pack(fill="x", padx=20, pady=2)
            tk.Label(
                row, text=num,
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=Colors.BG_CARD, fg=Colors.ACCENT_ORANGE,
                width=2, anchor="e",
            ).pack(side="left", padx=(0, 8))
            tk.Label(
                row, text=tip,
                font=("Microsoft YaHei UI", 9),
                bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
                anchor="w",
            ).pack(side="left")

        tk.Label(
            ks_guide, text="首次会自动打开快手二维码登录页，手机扫码后自动跳转解析，登录状态会自动保存",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        ).pack(pady=(6, 10))

        # ── 淘宝直播操作指引卡片 ──
        tb_guide = tk.Frame(
            placeholder, bg=Colors.BG_CARD,
            highlightbackground=Colors.BORDER, highlightthickness=1,
        )
        tb_guide.pack(pady=(20, 0), padx=60, fill="x", ipady=10)

        tk.Label(
            tb_guide, text="  淘宝直播 · 操作指引  ",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg="#FF6A00", fg="white", padx=10, pady=2,
        ).pack(pady=(10, 8))

        tb_tips = [
            ("1.", "粘贴淘宝直播链接（支持 tbzb.taobao.com / live.taobao.com）"),
            ("2.", "等待浏览器自动弹出，如需登录请扫码淘宝账号"),
            ("3.", "浏览器会自动监听网络请求，提取直播流地址"),
            ("4.", "提取完成后浏览器会自动关闭，流链接显示在列表中"),
        ]
        for num, tip in tb_tips:
            row = tk.Frame(tb_guide, bg=Colors.BG_CARD)
            row.pack(fill="x", padx=20, pady=2)
            tk.Label(
                row, text=num,
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=Colors.BG_CARD, fg="#FF6A00",
                width=2, anchor="e",
            ).pack(side="left", padx=(0, 8))
            tk.Label(
                row, text=tip,
                font=("Microsoft YaHei UI", 9),
                bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
                anchor="w",
            ).pack(side="left")

        tk.Label(
            tb_guide, text="淘宝直播需要浏览器自动化解析，首次使用需登录淘宝账号，登录状态会自动保存",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        ).pack(pady=(6, 10))

        # ── 小红书直播操作指引卡片 ──
        xhs_guide = tk.Frame(
            placeholder, bg=Colors.BG_CARD,
            highlightbackground=Colors.BORDER, highlightthickness=1,
        )
        xhs_guide.pack(pady=(20, 0), padx=60, fill="x", ipady=10)

        tk.Label(
            xhs_guide, text="  小红书直播 · 操作指引  ",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg="#FE2C55", fg="white", padx=10, pady=2,
        ).pack(pady=(10, 8))

        xhs_tips = [
            ("1.", "点击「获取流链接」会自动弹出浏览器"),
            ("2.", "首次使用需登录小红书账号（手机扫码）"),
            ("3.", "登录成功后自动跳转直播间解析"),
            ("4.", "提取完成后浏览器会自动关闭，流链接显示在列表中"),
        ]
        for num, tip in xhs_tips:
            row = tk.Frame(xhs_guide, bg=Colors.BG_CARD)
            row.pack(fill="x", padx=20, pady=2)
            tk.Label(
                row, text=num,
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=Colors.BG_CARD, fg=Colors.ACCENT_RED,
            ).pack(side="left")
            tk.Label(
                row, text=f" {tip}",
                font=("Microsoft YaHei UI", 9),
                bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
            ).pack(side="left")

        tk.Label(
            xhs_guide, text="小红书直播需要浏览器自动化解析，首次使用需登录小红书账号，登录状态会自动保存",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        ).pack(pady=(6, 10))

        # ── 抖音直播操作指引卡片 ──
        dy_guide = tk.Frame(
            placeholder, bg=Colors.BG_CARD,
            highlightbackground=Colors.BORDER, highlightthickness=1,
        )
        dy_guide.pack(pady=(20, 0), padx=60, fill="x", ipady=10)

        tk.Label(
            dy_guide, text="  抖音直播 · 操作指引  ",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg="#FF6A00", fg="white", padx=10, pady=2,
        ).pack(pady=(10, 8))

        dy_tips = [
            ("1.", "粘贴抖音直播链接（支持 live.douyin.com / douyin.com/follow/live）"),
            ("2.", "点击状态栏「抖音未登录」可提前扫码登录，或解析时自动弹出登录"),
            ("3.", "浏览器会自动监听网络请求，提取直播流地址"),
            ("4.", "提取完成后浏览器会自动关闭，流链接显示在列表中"),
        ]
        for num, tip in dy_tips:
            row = tk.Frame(dy_guide, bg=Colors.BG_CARD)
            row.pack(fill="x", padx=20, pady=2)
            tk.Label(
                row, text=num,
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=Colors.BG_CARD, fg="#FF6A00",
                width=2, anchor="e",
            ).pack(side="left", padx=(0, 8))
            tk.Label(
                row, text=tip,
                font=("Microsoft YaHei UI", 9),
                bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
                anchor="w",
            ).pack(side="left")

        tk.Label(
            dy_guide, text="抖音直播需要浏览器自动化解析，首次使用需登录抖音账号（状态栏可扫码），登录状态会自动保存",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        ).pack(pady=(6, 10))

        # ── YY直播操作指引卡片 ──
        yy_guide = tk.Frame(
            placeholder, bg=Colors.BG_CARD,
            highlightbackground=Colors.BORDER, highlightthickness=1,
        )
        yy_guide.pack(pady=(20, 0), padx=60, fill="x", ipady=10)

        tk.Label(
            yy_guide, text="  YY直播 · 操作指引  ",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg="#FFD700", fg="#333333", padx=10, pady=2,
        ).pack(pady=(10, 8))

        yy_tips = [
            ("1.", "粘贴YY直播链接（支持 www.yy.com/xxx 或 wap.yy.com/mobileweb/xxx）"),
            ("2.", "点击「获取流链接」，等待浏览器自动弹出并加载直播间"),
            ("3.", "浏览器会自动监听网络请求，提取直播流地址"),
            ("4.", "提取完成后浏览器会自动关闭，流链接显示在列表中"),
        ]
        for num, tip in yy_tips:
            row = tk.Frame(yy_guide, bg=Colors.BG_CARD)
            row.pack(fill="x", padx=20, pady=2)
            tk.Label(
                row, text=num,
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=Colors.BG_CARD, fg="#DAA520",
                width=2, anchor="e",
            ).pack(side="left", padx=(0, 8))
            tk.Label(
                row, text=tip,
                font=("Microsoft YaHei UI", 9),
                bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
                anchor="w",
            ).pack(side="left")

        tk.Label(
            yy_guide, text="YY直播通过浏览器自动化解析，无需登录即可获取公开直播间流地址",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        ).pack(pady=(6, 10))

    # ─── 代理设置折叠 ───
    def _toggle_proxy(self, event=None):
        if self.proxy_frame.winfo_manager():
            self.proxy_frame.pack_forget()
        else:
            self.proxy_frame.pack(fill="x", pady=(6, 0))

    # ─── 小红书登录状态显示 ───
    def _refresh_xhs_login_display(self):
        """检测小红书登录状态并更新状态栏标注。"""
        self._xhs_login_status = _check_xhs_login_status()
        status = self._xhs_login_status

        if status == "logged_in":
            self.xhs_login_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
            self.xhs_login_label.configure(text="小红书已登录", fg=Colors.ACCENT_GREEN)
        elif status == "expired":
            self.xhs_login_icon.configure(text="●", fg=Colors.ACCENT_ORANGE)
            self.xhs_login_label.configure(text="小红书登录可能失效(点击重登)", fg=Colors.ACCENT_ORANGE)
        else:  # never
            self.xhs_login_icon.configure(text="○", fg=Colors.TEXT_MUTED)
            self.xhs_login_label.configure(text="小红书未登录(点击登录)", fg=Colors.TEXT_MUTED)

    def _on_xhs_login_click(self, event=None):
        """点击状态栏小红书登录标注时的处理。

        - logged_in: 显示 Cookie 路径信息 + 提供退出登录选项
        - expired/never: 启动浏览器自动弹出小红书登录页
        """
        if self._xhs_login_status == "logged_in":
            # 已登录 → 显示信息 + 退出选项
            msg = (
                f"小红书登录状态：已登录\n\n"
                f"Cookie 存储路径：\n{self.xhs_cookie_dir}\n\n"
                f"点击「确定」退出小红书登录（下次解析需重新扫码），\n"
                f"点击「取消」保持当前登录状态。"
            )
            if messagebox.askyesno("小红书登录管理", msg):
                _clear_xhs_cookies()
                self._xhs_login_status = "never"
                self._refresh_xhs_login_display()
                self._show_toast("已退出小红书登录，下次解析将重新扫码")
        else:
            # 未登录/已失效 → 启动浏览器登录
            self._do_xhs_relogin()

    def _do_xhs_relogin(self):
        """启动浏览器打开小红书登录页，让用户扫码登录。

        登录成功后自动更新状态标注。
        """
        self.status_var.set("正在启动小红书登录浏览器，请扫码...")
        self.status_icon.configure(fg=Colors.ACCENT_ORANGE)

        thread = threading.Thread(target=self._xhs_relogin_thread, daemon=True)
        thread.start()

    def _xhs_relogin_thread(self):
        """在后台线程中执行小红书重新登录。"""
        try:
            from playwright.sync_api import sync_playwright

            url = "https://www.xiaohongshu.com"

            # 准备启动参数
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "no_viewport": False,
            }

            user_data_dir = _get_xhs_browser_data_dir()
            login_success = {"value": False}

            with sync_playwright() as p:
                # 尝试启动浏览器
                context = None
                embedded_chromium = _ensure_chromium_ready()
                if embedded_chromium:
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir,
                            executable_path=os.path.join(embedded_chromium, "chrome.exe"),
                            **launch_kwargs,
                        )
                    except Exception:
                        pass

                if not context:
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir, channel=None, **launch_kwargs,
                        )
                    except Exception:
                        try:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="chrome", **launch_kwargs,
                            )
                        except Exception:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="msedge", **launch_kwargs,
                            )

                page = context.pages[0] if context.pages else context.new_page()

                # 监听页面跳转，登录成功后小红书会跳到首页
                def on_frame_navigate(nav):
                    try:
                        nav_url = nav.url
                        if not nav_url or nav_url == "about:blank" or not nav_url.startswith("http"):
                            return
                        # 从登录页跳转到非登录页 = 登录成功
                        if "login" not in nav_url.lower() and login_success["value"] is False:
                            # 检查 cookie 是否已存在
                            cookies = context.cookies()
                            xhs_cookies = [c for c in cookies if "xiaohongshu" in c.get("domain", "")]
                            if xhs_cookies:
                                login_success["value"] = True
                                print(f"[小红书登录] 检测到跳转: {nav_url}，登录成功")
                    except Exception:
                        pass

                page.on("framenavigated", on_frame_navigate)

                # 导航到小红书首页
                page.goto(url, wait_until="networkidle", timeout=60000)

                # 等待页面加载
                page.wait_for_timeout(3000)

                # 如果没有登录弹窗，可能已经登录了
                try:
                    cookies = context.cookies()
                    xhs_cookies = [c for c in cookies if "xiaohongshu" in c.get("domain", "")]
                    if xhs_cookies:
                        login_success["value"] = True
                        print("[小红书登录] 检测到已有登录态")
                except Exception:
                    pass

                if not login_success["value"]:
                    # 尝试点击登录按钮
                    try:
                        login_btn = page.query_selector("div[class*='login'], [class*='sign-in'], a[href*='login']")
                        if login_btn:
                            login_btn.click()
                            page.wait_for_timeout(2000)
                    except Exception:
                        pass

                self.root.after(0, self.status_var.set, "请在弹出的浏览器中用手机小红书扫码登录...")

                # 等待登录成功，最长 120 秒
                for _ in range(24):
                    page.wait_for_timeout(5000)
                    if login_success["value"]:
                        break
                    # 兜底检测：检查 cookie
                    try:
                        cookies = context.cookies()
                        xhs_cookies = [c for c in cookies if "xiaohongshu" in c.get("domain", "")]
                        if xhs_cookies:
                            login_success["value"] = True
                            break
                    except Exception:
                        # page.url 抛异常说明浏览器已被用户关闭
                        break

                # 安全关闭浏览器
                try:
                    context.close()
                except Exception:
                    pass

            if login_success["value"]:
                self.root.after(0, self._on_xhs_relogin_success)
            else:
                self.root.after(0, self._on_xhs_relogin_timeout)

        except Exception as e:
            self.root.after(0, self.status_var.set, f"小红书登录浏览器启动失败: {e}")
            self.root.after(0, lambda: self.status_icon.configure(fg=Colors.ACCENT_RED))

    def _on_xhs_relogin_success(self):
        """小红书重新登录成功回调。"""
        self._xhs_login_status = "logged_in"
        self._refresh_xhs_login_display()
        self.status_var.set("小红书登录成功！可以开始解析直播间了")
        self.status_icon.configure(fg=Colors.ACCENT_GREEN)
        self._show_toast("小红书登录成功！")

    def _on_xhs_relogin_timeout(self):
        """小红书登录等待超时回调。"""
        self.status_var.set("小红书登录等待超时，可稍后再试或直接解析")
        self.status_icon.configure(fg=Colors.TEXT_MUTED)
        # 无论是否超时都刷新一下状态（可能用户已扫码但我们没检测到）
        self._refresh_xhs_login_display()

    # ─── 抖音登录状态显示 ───
    def _refresh_dy_login_display(self):
        """检测抖音登录状态并更新状态栏标注。"""
        self._dy_login_status = _check_dy_login_status()
        status = self._dy_login_status

        if status == "logged_in":
            self.dy_login_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
            self.dy_login_label.configure(text="抖音已登录", fg=Colors.ACCENT_GREEN)
        elif status == "expired":
            self.dy_login_icon.configure(text="●", fg=Colors.ACCENT_BLUE)
            self.dy_login_label.configure(text="抖音登录可能失效(点击重登)", fg=Colors.ACCENT_BLUE)
        else:  # never
            self.dy_login_icon.configure(text="○", fg=Colors.TEXT_MUTED)
            self.dy_login_label.configure(text="抖音未登录(点击登录)", fg=Colors.TEXT_MUTED)

    def _on_dy_login_click(self, event=None):
        """点击状态栏抖音登录标注时的处理。"""
        if self._dy_login_status == "logged_in":
            msg = (
                f"抖音登录状态：已登录\n\n"
                f"Cookie 存储路径：\n{self.dy_cookie_dir}\n\n"
                f"点击「确定」退出抖音登录（下次解析需重新扫码），\n"
                f"点击「取消」保持当前登录状态。"
            )
            if messagebox.askyesno("抖音登录管理", msg):
                _clear_dy_cookies()
                self._dy_login_status = "never"
                self._refresh_dy_login_display()
                self._show_toast("已退出抖音登录，下次解析将重新扫码")
        else:
            self._do_dy_relogin()

    def _do_dy_relogin(self):
        """启动浏览器打开抖音登录页，让用户扫码登录。"""
        self.status_var.set("正在启动抖音登录浏览器，请扫码...")
        self.status_icon.configure(fg="#161823")

        thread = threading.Thread(target=self._dy_relogin_thread, daemon=True)
        thread.start()

    def _dy_relogin_thread(self):
        """在后台线程中执行抖音重新登录。"""
        try:
            from playwright.sync_api import sync_playwright

            url = "https://www.douyin.com"

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "no_viewport": False,
            }

            user_data_dir = _get_dy_browser_data_dir()
            login_success = {"value": False}

            with sync_playwright() as p:
                context = None
                embedded_chromium = _ensure_chromium_ready()
                if embedded_chromium:
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir,
                            executable_path=os.path.join(embedded_chromium, "chrome.exe"),
                            **launch_kwargs,
                        )
                    except Exception:
                        pass

                if not context:
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir, channel=None, **launch_kwargs,
                        )
                    except Exception:
                        try:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="chrome", **launch_kwargs,
                            )
                        except Exception:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="msedge", **launch_kwargs,
                            )

                page = context.pages[0] if context.pages else context.new_page()

                # 监听页面跳转
                prev_url = {"value": ""}
                def on_frame_navigate(nav):
                    try:
                        nav_url = nav.url
                        if not nav_url or nav_url == "about:blank" or not nav_url.startswith("http"):
                            return
                        old = prev_url.get("value", "")
                        # 从登录相关页面跳转到非登录页面 → 登录成功
                        # 覆盖 sso.douyin.com / passport / login 等各种登录域名
                        is_login_domain = any(kw in old for kw in (
                            "sso.douyin.com", "passport", "login",
                        ))
                        if is_login_domain:
                            is_no_login = all(kw not in nav_url for kw in (
                                "sso.douyin.com", "passport", "login",
                            ))
                            if is_no_login:
                                login_success["value"] = True
                                print(f"[抖音登录] 检测到跳转: {nav_url}，登录成功")
                        prev_url["value"] = nav_url
                    except Exception:
                        pass

                page.on("framenavigated", on_frame_navigate)

                # 导航到抖音首页（未登录时自动跳转到 sso.douyin.com 登录页）
                page.goto(url, wait_until="networkidle", timeout=60000)

                # 等待二维码 / 登录元素渲染完成（最多 15 秒）
                try:
                    page.wait_for_selector(
                        "img[src*='qrcode'], .qrcode-img, [class*='login'], "
                        "[class*='Login']",
                        timeout=15000,
                    )
                except Exception:
                    pass
                page.wait_for_timeout(2000)

                self.root.after(0, self.status_var.set, "请在弹出的浏览器中用手机抖音扫码登录...")

                # ── 等待用户扫码登录（最长 180 秒）──
                # 检测策略：通过 Playwright context.cookies() API 获取浏览器运行时 cookie，
                #   检查是否出现 .douyin.com 域名下的 sessionid/sid_guard 等认证 cookie。
                # 这是 Playwright 官方推荐方式，不依赖 SQLite 文件读取，
                #   不受 Chromium 运行期间文件锁影响。
                # 同时保留 URL 跳转检测作为辅助。
                self.root.after(0, lambda: self.status_var.set(
                    "请在弹出的浏览器中用手机抖音扫码登录..."))
                print("[抖音登录] 开始等待用户扫码...")

                for i in range(36):  # 最多 180 秒 (36 * 5s)
                    page.wait_for_timeout(5000)
                    if login_success["value"]:
                        break

                    elapsed = (i + 1) * 5
                    print(f"[抖音登录] 等待中... {elapsed}s / 180s")
                    self.root.after(0, lambda s=f"等待抖音扫码登录 ({elapsed}s)...": self.status_var.set(s))

                    try:
                        # ══ 方式 A：Cookie 精确检测（最可靠）══
                        cookies = context.cookies()
                        dy_auth_names = {
                            "sessionid", "sid_guard", "uid_tt",
                            "passport_csrf_token", "sid_client", "odin_tt",
                        }
                        all_cookie_domains = [c.get("domain", "") for c in cookies]
                        for c in cookies:
                            domain = c.get("domain", "")
                            name = c.get("name", "")
                            if (".douyin.com" in domain or ".bytedance.com" in domain) and name in dy_auth_names:
                                login_success["value"] = True
                                print(f"[抖音登录] ★ Cookie 检测成功! name={name}, value={c['value'][:20]}...")
                                self.root.after(0, lambda: self.status_var.set("检测到登录 Cookie，正在完成..."))
                                break

                        if login_success["value"]:
                            break

                        # ══ 方式 B：URL 跳转辅助检测 ═══
                        cur_url = page.url
                        if not cur_url or cur_url == "about:blank":
                            continue

                        is_on_login_page = any(kw in cur_url.lower() for kw in (
                            "sso.douyin.com", "passport", "/login",
                        ))
                        if not is_on_login_page and cur_url.startswith("http"):
                            login_success["value"] = True
                            print(f"[抖音登录] ★ URL 检测成功! 已离开登录页: {cur_url}")
                            break

                    except Exception as e:
                        # page.url / context.cookies 抛异常 → 浏览器可能已被关闭
                        print(f"[抖音登录] 检测异常，停止等待: {e}")
                        break

                try:
                    context.close()
                except Exception:
                    pass

            if login_success["value"]:
                self.root.after(0, self._on_dy_relogin_success)
            else:
                self.root.after(0, self._on_dy_relogin_timeout)

        except Exception as e:
            self.root.after(0, self.status_var.set, f"抖音登录浏览器启动失败: {e}")
            self.root.after(0, lambda: self.status_icon.configure(fg=Colors.ACCENT_RED))

    def _on_dy_relogin_success(self):
        """抖音重新登录成功回调。"""
        self._dy_login_status = "logged_in"
        self._refresh_dy_login_display()
        self.status_var.set("抖音登录成功！可以开始解析直播间了")
        self.status_icon.configure(fg=Colors.ACCENT_GREEN)
        self._show_toast("抖音登录成功！")

    def _on_dy_relogin_timeout(self):
        """抖音登录等待超时回调。"""
        self.status_var.set("抖音登录等待超时，可稍后再试或直接解析")
        self.status_icon.configure(fg=Colors.TEXT_MUTED)
        self._refresh_dy_login_display()

    # ─── 快手登录状态显示 ───
    def _refresh_ks_login_display(self):
        """检测快手登录状态并更新状态栏标注。"""
        self._ks_login_status = _check_ks_login_status()
        status = self._ks_login_status

        if status == "logged_in":
            self.ks_login_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
            self.ks_login_label.configure(text="快手已登录", fg=Colors.ACCENT_GREEN)
        elif status == "expired":
            self.ks_login_icon.configure(text="●", fg=Colors.ACCENT_ORANGE)
            self.ks_login_label.configure(text="快手登录可能失效(点击重登)", fg=Colors.ACCENT_ORANGE)
        else:  # never
            self.ks_login_icon.configure(text="○", fg=Colors.TEXT_MUTED)
            self.ks_login_label.configure(text="快手未登录(点击登录)", fg=Colors.TEXT_MUTED)

    def _on_ks_login_click(self, event=None):
        """点击状态栏快手登录标注时的处理。

        - logged_in: 显示 Cookie 路径信息 + 提供退出登录选项
        - expired/never: 启动浏览器自动弹出快手登录页
        """
        if self._ks_login_status == "logged_in":
            # 已登录 → 显示信息 + 退出选项
            msg = (
                f"快手登录状态：已登录\n\n"
                f"Cookie 存储路径：\n{self.ks_cookie_dir}\n\n"
                f"点击「确定」退出快手登录（下次解析需重新扫码），\n"
                f"点击「取消」保持当前登录状态。"
            )
            if messagebox.askyesno("快手登录管理", msg):
                _clear_ks_cookies()
                self._ks_login_status = "never"
                self._refresh_ks_login_display()
                self._show_toast("已退出快手登录，下次解析将重新扫码")
        else:
            # 未登录/已失效 → 启动浏览器登录
            self._do_ks_relogin()

    def _do_ks_relogin(self):
        """启动浏览器打开快手登录页，让用户扫码登录。

        登录成功后自动更新状态标注。
        """
        self.status_var.set("正在启动快手登录浏览器，请扫码...")
        self.status_icon.configure(fg=Colors.ACCENT_ORANGE)

        thread = threading.Thread(target=self._ks_relogin_thread, daemon=True)
        thread.start()

    def _ks_relogin_thread(self):
        """在后台线程中执行快手重新登录。"""
        try:
            from playwright.sync_api import sync_playwright

            url = "https://passport.kuaishou.com/pc/account/login"

            # 准备启动参数
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "no_viewport": False,
            }

            user_data_dir = _get_ks_browser_data_dir()
            login_success = {"value": False}

            with sync_playwright() as p:
                # 尝试启动浏览器
                context = None
                embedded_chromium = _ensure_chromium_ready()
                if embedded_chromium:
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir,
                            executable_path=os.path.join(embedded_chromium, "chrome.exe"),
                            **launch_kwargs,
                        )
                    except Exception:
                        pass

                if not context:
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir, channel=None, **launch_kwargs,
                        )
                    except Exception:
                        try:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="chrome", **launch_kwargs,
                            )
                        except Exception:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="msedge", **launch_kwargs,
                            )

                page = context.pages[0] if context.pages else context.new_page()

                # 监听页面跳转，登录成功后快手会跳到首页
                def on_frame_navigate(nav):
                    try:
                        nav_url = nav.url
                        if not nav_url or nav_url == "about:blank" or not nav_url.startswith("http"):
                            return
                        # 从登录页跳转到非登录页 = 登录成功
                        if "passport.kuaishou.com" not in nav_url and login_success["value"] is False:
                            login_success["value"] = True
                            print(f"[快手登录] 检测到跳转: {nav_url}，登录成功")
                    except Exception:
                        pass

                page.on("framenavigated", on_frame_navigate)

                # 导航到快手登录页
                page.goto(url, wait_until="networkidle", timeout=60000)

                # 等待二维码元素出现（最多 15 秒）
                try:
                    page.wait_for_selector(
                        "img[src*='qrcode'], .qrcode-img, .login-panel, [class*='qrcode']",
                        timeout=15000,
                    )
                except Exception:
                    pass
                # 额外兜底等待
                page.wait_for_timeout(2000)

                self.root.after(0, self.status_var.set, "请在弹出的浏览器中用手机快手扫码登录...")

                # 等待登录成功，最长 120 秒
                for _ in range(24):
                    page.wait_for_timeout(5000)
                    if login_success["value"]:
                        break
                    # 兜底检测：如果当前页面已经不在登录域了，也算登录成功
                    try:
                        cur = page.url
                        # 排除 about:blank 等非有效 URL（用户可能已关闭页面）
                        if cur and "passport.kuaishou.com" not in cur and cur != "about:blank" and cur.startswith("http"):
                            login_success["value"] = True
                            break
                    except Exception:
                        # page.url 抛异常说明浏览器已被用户关闭
                        break

                # 安全关闭浏览器（用户可能已经手动关闭）
                try:
                    context.close()
                except Exception:
                    pass

            if login_success["value"]:
                self.root.after(0, self._on_ks_relogin_success)
            else:
                self.root.after(0, self._on_ks_relogin_timeout)

        except Exception as e:
            self.root.after(0, self.status_var.set, f"快手登录浏览器启动失败: {e}")
            self.root.after(0, lambda: self.status_icon.configure(fg=Colors.ACCENT_RED))

    def _on_ks_relogin_success(self):
        """快手重新登录成功回调。"""
        self._ks_login_status = "logged_in"
        self._refresh_ks_login_display()
        self.status_var.set("快手登录成功！可以开始解析直播间了")
        self.status_icon.configure(fg=Colors.ACCENT_GREEN)
        self._show_toast("快手登录成功！")

    def _on_ks_relogin_timeout(self):
        """快手登录等待超时回调。"""
        self.status_var.set("快手登录等待超时，可稍后再试或直接解析")
        self.status_icon.configure(fg=Colors.TEXT_MUTED)
        # 无论是否超时都刷新一下状态（可能用户已扫码但我们没检测到）
        self._refresh_ks_login_display()

    # ─── 淘宝登录状态显示 ───
    def _refresh_tb_login_display(self):
        """检测淘宝登录状态并更新状态栏标注。"""
        self._tb_login_status = _check_tb_login_status()
        status = self._tb_login_status

        if status == "logged_in":
            self.tb_login_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
            self.tb_login_label.configure(text="淘宝已登录", fg=Colors.ACCENT_GREEN)
        elif status == "expired":
            self.tb_login_icon.configure(text="●", fg=Colors.ACCENT_ORANGE)
            self.tb_login_label.configure(text="淘宝登录可能失效(点击重登)", fg=Colors.ACCENT_ORANGE)
        else:  # never
            self.tb_login_icon.configure(text="○", fg=Colors.TEXT_MUTED)
            self.tb_login_label.configure(text="淘宝未登录(点击登录)", fg=Colors.TEXT_MUTED)

    def _on_tb_login_click(self, event=None):
        """点击状态栏淘宝登录标注时的处理。

        - logged_in: 显示 Cookie 路径信息 + 提供退出登录选项
        - expired/never: 启动浏览器自动弹出淘宝登录页
        """
        if self._tb_login_status == "logged_in":
            # 已登录 → 显示信息 + 退出选项
            msg = (
                f"淘宝登录状态：已登录\n\n"
                f"Cookie 存储路径：\n{self.tb_cookie_dir}\n\n"
                f"点击「确定」退出淘宝登录（下次解析需重新扫码），\n"
                f"点击「取消」保持当前登录状态。"
            )
            if messagebox.askyesno("淘宝登录管理", msg):
                _clear_tb_cookies()
                self._tb_login_status = "never"
                self._refresh_tb_login_display()
                self._show_toast("已退出淘宝登录，下次解析将重新扫码")
        else:
            # 未登录/已失效 → 启动浏览器登录
            self._do_tb_relogin()

    def _do_tb_relogin(self):
        """启动浏览器打开淘宝登录页，让用户扫码登录。

        登录成功后自动更新状态标注。
        """
        self.status_var.set("正在启动淘宝登录浏览器，请扫码...")
        self.status_icon.configure(fg=Colors.ACCENT_ORANGE)

        thread = threading.Thread(target=self._tb_relogin_thread, daemon=True)
        thread.start()

    def _tb_relogin_thread(self):
        """在后台线程中执行淘宝重新登录。"""
        try:
            from playwright.sync_api import sync_playwright

            url = "https://login.taobao.com/member/login.jhtml"

            # 准备启动参数
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "no_viewport": False,
            }

            user_data_dir = _get_tb_browser_data_dir()
            login_success = {"value": False}

            with sync_playwright() as p:
                # 尝试启动浏览器
                context = None
                embedded_chromium = _ensure_chromium_ready()
                if embedded_chromium:
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir,
                            executable_path=os.path.join(embedded_chromium, "chrome.exe"),
                            **launch_kwargs,
                        )
                    except Exception:
                        pass

                if not context:
                    try:
                        context = p.chromium.launch_persistent_context(
                            user_data_dir, channel=None, **launch_kwargs,
                        )
                    except Exception:
                        try:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="chrome", **launch_kwargs,
                            )
                        except Exception:
                            context = p.chromium.launch_persistent_context(
                                user_data_dir, channel="msedge", **launch_kwargs,
                            )

                page = context.pages[0] if context.pages else context.new_page()

                # 监听页面跳转，登录成功后淘宝会跳离开登录域
                def on_frame_navigate(nav):
                    try:
                        nav_url = nav.url
                        if not nav_url or nav_url == "about:blank" or not nav_url.startswith("http"):
                            return
                        # 从登录页跳转到非登录页 = 登录成功
                        if "login.taobao.com" not in nav_url and login_success["value"] is False:
                            login_success["value"] = True
                            print(f"[淘宝登录] 检测到跳转: {nav_url}，登录成功")
                    except Exception:
                        pass

                page.on("framenavigated", on_frame_navigate)

                # 导航到淘宝登录页
                page.goto(url, wait_until="networkidle", timeout=60000)

                # 等待二维码/登录元素出现（最多 15 秒）
                try:
                    page.wait_for_selector(
                        "img[src*='qrcode'], .qrcode-img, .login-panel, [class*='qrcode']",
                        timeout=15000,
                    )
                except Exception:
                    pass
                # 额外兜底等待
                page.wait_for_timeout(2000)

                self.root.after(0, self.status_var.set, "请在弹出的浏览器中用手机淘宝扫码登录...")

                # 等待登录成功，最长 120 秒
                for _ in range(24):
                    page.wait_for_timeout(5000)
                    if login_success["value"]:
                        break
                    # 兜底检测：如果当前页面已经不在登录域了，也算登录成功
                    try:
                        cur = page.url
                        if cur and "login.taobao.com" not in cur and cur != "about:blank" and cur.startswith("http"):
                            login_success["value"] = True
                            break
                    except Exception:
                        # page.url 抛异常说明浏览器已被用户关闭
                        break

                # 安全关闭浏览器（用户可能已经手动关闭）
                try:
                    context.close()
                except Exception:
                    pass

            if login_success["value"]:
                self.root.after(0, self._on_tb_relogin_success)
            else:
                self.root.after(0, self._on_tb_relogin_timeout)

        except Exception as e:
            self.root.after(0, self.status_var.set, f"淘宝登录浏览器启动失败: {e}")
            self.root.after(0, lambda: self.status_icon.configure(fg=Colors.ACCENT_RED))

    def _on_tb_relogin_success(self):
        """淘宝重新登录成功回调。"""
        self._tb_login_status = "logged_in"
        self._refresh_tb_login_display()
        self.status_var.set("淘宝登录成功！可以开始解析直播间了")
        self.status_icon.configure(fg=Colors.ACCENT_GREEN)
        self._show_toast("淘宝登录成功！")

    def _on_tb_relogin_timeout(self):
        """淘宝登录等待超时回调。"""
        self.status_var.set("淘宝登录等待超时，可稍后再试或直接解析")
        self.status_icon.configure(fg=Colors.TEXT_MUTED)
        # 无论是否超时都刷新一下状态（可能用户已扫码但我们没检测到）
        self._refresh_tb_login_display()

    # ─── URL 变化检测 ───
    def _on_url_change(self, *args):
        url = self.url_var.get().strip()
        # 清除按钮：有内容时显示，无内容时隐藏
        if hasattr(self, '_url_clear_btn'):
            try:
                self._url_clear_btn.pack_forget() if not url else self._url_clear_btn.pack(side="right", padx=(0, 4), pady=6)
            except tk.TclError:
                pass
        if url:
            platform = detect_platform(url)
            if platform != "未知平台":
                if platform == "快手":
                    self.status_var.set(f"检测到平台：快手 — 将弹出浏览器解析，请勿关闭浏览器窗口")
                elif platform == "淘宝直播":
                    self.status_var.set(f"检测到平台：淘宝直播 — 将弹出浏览器解析，请勿关闭浏览器窗口")
                else:
                    self.status_var.set(f"检测到平台：{platform} — 将使用专属解析器")
                self.status_icon.configure(fg=Colors.ACCENT_BLUE)

    # ─── 获取按钮 ───
    def _on_fetch(self):
        url = self.url_var.get().strip()
        if not url:
            self._show_toast("请先粘贴直播间链接")
            return
        if not url.startswith("http"):
            self._show_toast("请输入有效的 HTTP/HTTPS 链接")
            return

        self.fetch_btn.configure(text="  解析中...  ", bg=Colors.TEXT_MUTED)
        self.status_var.set("正在解析视频流，请稍候...")
        self.status_icon.configure(fg=Colors.ACCENT_ORANGE)

        thread = threading.Thread(target=self._do_fetch, args=(url,), daemon=True)
        thread.start()

    # ─── 后台获取流数据（在线程中运行） ───
    def _do_fetch(self, url: str):
        """后台线程：解析直播流并更新 UI"""
        try:
            result = extract_streams(url, proxy="")
            # 切回主线程更新 UI
            self.root.after(0, lambda: self._show_result(result))
        except Exception as e:
            _err_msg = str(e)
            self.root.after(0, lambda: self._show_error(_err_msg))
        finally:
            self.root.after(0, lambda: self.fetch_btn.configure(
                text="  解析  ", bg=Colors.ACCENT_GREEN))

    # ─── 显示解析结果 ───
    def _show_result(self, result: dict):
        """显示解析到的直播流列表（含分类标签栏、流卡片、OBS/HEVC按钮）"""
        self._clear_result()
        streams = result.get("streams", [])
        platform = result.get("platform", "")
        title = result.get("title", "")

        if not streams:
            self._show_error("未检测到直播流，请确认直播间正在直播")
            return

        # 保存到实例变量（供复制全部、筛选等使用）
        self._all_streams = streams
        self._result_platform = platform

        # ── 标题行 + 流数量 ──
        title_text = f"{platform} - {title}" if title else platform
        header_row = tk.Frame(self.result_inner, bg=Colors.BG_DARK)
        header_row.pack(fill="x", pady=(8, 4), padx=4)
        tk.Label(header_row, text=title_text,
                 font=("Microsoft YaHei UI", 11, "bold"),
                 bg=Colors.BG_DARK, fg=Colors.TEXT_PRIMARY).pack(side="left")
        tk.Label(header_row, text=f"  {len(streams)}",
                 font=("Microsoft YaHei UI", 18, "bold"),
                 bg=Colors.BG_DARK, fg=Colors.ACCENT_BLUE).pack(side="right")
        tk.Label(header_row, text="\n个视频流",
                 font=("Microsoft YaHei UI", 9),
                 bg=Colors.BG_DARK, fg=Colors.TEXT_MUTED).pack(side="right")

        # ── 解析方式提示 ──
        method = result.get("method_used", "")
        method_extra = result.get("method_extra", "")
        method_text = f"解析方式：{method}"
        if method_extra:
            method_text += f"  ·  {method_extra}"
        tk.Label(self.result_inner, text=method_text,
                 font=("Microsoft YaHei UI", 8),
                 bg=Colors.BG_DARK, fg=Colors.TEXT_MUTED,
                 anchor="w").pack(fill="x", padx=4)

        # ── 分类标签栏（清晰度 / 格式 维度切换）──
        filter_bar = tk.Frame(self.result_inner, bg=Colors.BG_DARK)
        filter_bar.pack(fill="x", pady=(10, 6), padx=4)

        # 维度切换按钮
        dim_frame = tk.Frame(filter_bar, bg=Colors.BG_DARK)
        dim_frame.pack(side="left")

        qual_dim_btn = tk.Label(dim_frame, text=" 清晰度 ",
                                 font=("Microsoft YaHei UI", 9, "bold"),
                                 bg=Colors.ACCENT_BLUE, fg="white",
                                 padx=10, pady=4, cursor="hand2")
        qual_dim_btn.pack(side="left")
        qual_dim_btn.bind("<Button-1>",
                          lambda e: self._switch_filter_dimension("quality"))

        fmt_dim_btn = tk.Label(dim_frame, text=" 格式 ",
                                font=("Microsoft YaHei UI", 9),
                                bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
                                padx=10, pady=4, cursor="hand2")
        fmt_dim_btn.pack(side="left")
        fmt_dim_btn.bind("<Button-1>",
                         lambda e: self._switch_filter_dimension("format"))
        self._filter_dim_buttons = (qual_dim_btn, fmt_dim_btn)

        # 动态标签按钮
        tags_frame = tk.Frame(filter_bar, bg=Colors.BG_DARK)
        tags_frame.pack(side="left", padx=(10, 0))
        self._filter_tags_frame = tags_frame

        # 构建分类统计并渲染标签
        self._build_and_render_filter_tags(streams)

        # ── 渲染每条流的卡片 ──
        for i, stream in enumerate(streams):
            self._render_stream_card(stream, i, platform)

        # 状态栏更新
        self.status_var.set(f"解析成功 ({method})，{len(streams)} 条流")
        self.status_icon.configure(fg=Colors.ACCENT_GREEN)

        # 淘宝/小红书自动启动代理
        if platform in ("淘宝直播", "小红书"):
            self._start_stream_proxy(streams, platform)

        # 刷新登录状态
        self.root.after(500, self._refresh_all_login_status)

    def _build_and_render_filter_tags(self, streams):
        """根据当前维度构建分类统计并渲染标签按钮"""
        # 清除旧标签
        for w in self._filter_tags_frame.winfo_children():
            w.destroy()

        dimension = getattr(self, "_filter_dimension", "quality")
        counts = {}
        for s in streams:
            val = s.get(dimension, "其他").strip()
            if not val:
                val = "其他"
            counts[val] = counts.get(val, 0) + 1

        # 排序：按数量降序
        sorted_items = sorted(counts.items(), key=lambda x: -x[1])

        for tag_name, count in sorted_items:
            is_active = (self._filter_var.get() == tag_name)
            btn = tk.Label(
                self._filter_tags_frame,
                text=f" {tag_name} ({count}) ",
                font=("Microsoft YaHei UI", 9, "bold" if is_active else "normal"),
                bg=Colors.ACCENT_PURPLE if is_active else Colors.BG_CARD,
                fg="white" if is_active else Colors.TEXT_SECONDARY,
                padx=10, pady=4, cursor="hand2",
            )
            btn.pack(side="left", padx=(0, 4))
            btn.bind("<Button-1>",
                     lambda e, t=tag_name: self._on_filter_tag_click(t))

    def _switch_filter_dimension(self, dimension: str):
        """切换筛选维度（quality / format）"""
        self._filter_dimension = dimension
        self._filter_var.set("全部")
        # 更新维度按钮样式
        qbtn, fbtn = self._filter_dim_buttons
        if dimension == "quality":
            qbtn.configure(bg=Colors.ACCENT_BLUE, fg="white",
                           font=("Microsoft YaHei UI", 9, "bold"))
            fbtn.configure(bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
                           font=("Microsoft YaHei UI", 9))
        else:
            fbtn.configure(bg=Colors.ACCENT_BLUE, fg="white",
                           font=("Microsoft YaHei UI", 9, "bold"))
            qbtn.configure(bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
                           font=("Microsoft YaHei UI", 9))
        # 重建标签
        self._build_and_render_filter_tags(getattr(self, "_all_streams", []))
        # 重新渲染卡片
        self._render_filtered_streams()

    def _on_filter_tag_click(self, tag_name: str):
        """点击分类标签筛选流"""
        current = self._filter_var.get()
        self._filter_var.set(tag_name if current != tag_name else "全部")
        self._build_and_render_filter_tags(getattr(self, "_all_streams", []))
        self._render_filtered_streams()

    # ─── 渲染单条流卡片（完整版：序号+清晰度+格式+来源+URL+复制/OBS/HEVC按钮）──
    def _render_stream_card(self, stream: dict, index: int, platform=""):
        """渲染一条直播流的信息卡片"""
        url = stream.get("url", "")
        quality = stream.get("quality", "默认")
        fmt = stream.get("format", "")
        source = stream.get("source", "")

        card = tk.Frame(self.result_inner, bg=Colors.BG_CARD, bd=0)
        card.pack(fill="x", pady=3, padx=2)
        self._stream_cards.append(card)

        inner = tk.Frame(card, bg=Colors.BG_CARD)
        inner.pack(fill="x", padx=14, pady=10)

        # ── 第一行：序号 + 清晰度名称 + 格式标签 + 来源(右侧) ──
        hdr = tk.Frame(inner, bg=Colors.BG_CARD)
        hdr.pack(fill="x")

        # 序号
        tk.Label(hdr, text=f"#{index + 1}",
                 font=("Microsoft YaHei UI", 9, "bold"),
                 bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED).pack(side="left")

        # 清晰度名称（带颜色匹配）
        qual_color = "#a78bfa"
        for lvl_key, (code, label, color) in QUALITY_LEVELS.items():
            if code.lower() in quality.lower() or label in quality:
                qual_color = color
                break
        tk.Label(hdr, text=f"  {quality}",
                 font=("Microsoft YaHei UI", 11, "bold"),
                 bg=Colors.BG_CARD, fg=qual_color).pack(side="left")

        # 格式标签（FLV/FLV高清/m3u8/HLS 等）
        fmt_colors = {"flv": "#22c55e", "m3u8": "#3b82f6", "hls": "#3b82f6",
                      "fmp4": "#f59e0b", "mp4": "#8b5cf6"}
        fmt_lc = fmt.lower()
        fmt_color = next((c for k, c in fmt_colors.items() if k in fmt_lc), Colors.TEXT_MUTED)
        fmt_badge = tk.Label(hdr, text=f"  {fmt.upper()}  ",
                             font=("Microsoft YaHei UI", 8, "bold"),
                             bg=fmt_color, fg="white", padx=5, pady=1)
        fmt_badge.pack(side="left", padx=(6, 0))

        # 来源标签（右侧）
        if source and source != "INITIAL_DATA":
            tk.Label(hdr, text=f"  来源: {source}",
                     font=("Microsoft YaHei UI", 8),
                     bg=Colors.BG_CARD, fg="#666").pack(side="right")

        # ── 第二行：URL（可点击复制）──
        display_url = url if len(url) <= 90 else url[:87] + "..."
        url_lbl = tk.Label(inner, text=display_url,
                           font=("Consolas", 9), bg=Colors.BG_CARD, fg="#888",
                           cursor="hand2")
        url_lbl.pack(anchor="w", pady=(4, 0))
        url_lbl.bind("<Button-1>", lambda e, u=url: self._copy_single_url(u))

        # HEVC 提示文字
        is_hevc = any(kw in quality.lower() for kw in ["hevc", "h265", "h.265"])
        hevc_hint_color = "#ef4444"
        if is_hevc:
            hint_lbl = tk.Label(
                inner,
                text="* 该链接为HEVC编码，无法直接在OBS使用，请点击右侧「转码」按钮或使用下方HEVC转码工具",
                font=("Microsoft YaHei UI", 8),
                bg=Colors.BG_CARD, fg=hevc_hint_color,
                anchor="w")
            hint_lbl.pack(anchor="w", pady=(2, 0))

        # ── 第三行：操作按钮 ──
        btn_row = tk.Frame(inner, bg=Colors.BG_CARD)
        btn_row.pack(fill="x", pady=(8, 0))

        # 复制按钮
        cp_btn = tk.Label(btn_row, text=" 复制链接 ",
                          font=("Microsoft YaHei UI", 9, "bold"),
                          bg=Colors.ACCENT_BLUE, fg="white",
                          padx=14, pady=4, cursor="hand2")
        cp_btn.pack(side="left")
        cp_btn.bind("<Button-1>", lambda e, u=url: self._copy_single_url(u))
        cp_btn.bind("<Enter>", lambda e, b=cp_btn: b.configure(bg="#4a90d9"))
        cp_btn.bind("<Leave>", lambda e, b=cp_btn: b.configure(bg=Colors.ACCENT_BLUE))

        # OBS 按钮 / 转码按钮 / 直接可用提示
        obs_btn = None

        if hasattr(self, '_proxy_ready') and self._proxy_ready and url in getattr(self, '_proxy_urls', {}):
            # 代理已就绪 → 显示橙色 OBS 按钮（点击复制代理地址）
            proxy_url = self._proxy_urls[url]
            obs_btn = tk.Label(btn_row, text=f" OBS ",
                               font=("Microsoft YaHei UI", 9, "bold"),
                               bg="#ff5000", fg="white",
                               padx=14, pady=4, cursor="hand2")
            obs_btn.pack(side="left", padx=(6, 0))
            obs_btn.bind("<Button-1>", lambda e, p=proxy_url: self._copy_obs_url(p))
            obs_btn.bind("<Enter>", lambda e, b=obs_btn: b.configure(bg="#e64a00"))
            obs_btn.bind("<Leave>", lambda e, b=obs_btn: b.configure(bg="#ff5000"))
            # 保存引用（代理失败时更新状态用）
            if not hasattr(self, "_obs_btn_refs"):
                self._obs_btn_refs = []
            self._obs_btn_refs.append((url, obs_btn))

        elif "hls" in fmt_lc or "m3u8" in fmt_lc:
            # HLS/M3U8 非HEVC → 绿色"可直接用OBS"
            direct_lbl = tk.Label(btn_row, text=" 可直接在OBS使用 ",
                                  font=("Microsoft YaHei UI", 9),
                                  bg="#2ea043", fg="white",
                                  padx=10, pady=4, cursor="arrow")
            direct_lbl.pack(side="left", padx=(6, 0))

        # 所有流都显示 HEVC 转码按钮（不论是否检测到 HEVC 编码）
        # 用户可能遇到 OBS 兼容性问题需要手动转码
        if is_hevc:
            trans_text = " HEVC转码 "
            trans_bg = Colors.ACCENT_PURPLE
        else:
            trans_text = " 转码 "
            trans_bg = "#6b7280"  # 灰色，低优先级提示
        trans_btn = tk.Label(btn_row, text=trans_text,
                             font=("Microsoft YaHei UI", 9, "bold"),
                             bg=trans_bg, fg="white",
                             padx=14, pady=4, cursor="hand2")
        trans_btn.pack(side="right")
        trans_btn.bind("<Button-1>", lambda e, u=url: self._open_transcode_dialog(u))
        hover_bg = "#a855f7" if is_hevc else "#9ca3af"
        trans_btn.bind("<Enter>", lambda e, b=trans_btn, h=hover_bg: b.configure(bg=h))
        trans_btn.bind("<Leave>", lambda e, b=trans_btn, o=trans_bg: b.configure(bg=o))

    # ─── 复制单条 URL ───
    def _copy_single_url(self, url: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self._show_toast(f"已复制: {url[:50]}...")

    # ─── 筛选渲染（供代理就绪后/分类切换时调用）──
    def _render_filtered_streams(self):
        """根据当前筛选条件重新渲染流卡片"""
        if not hasattr(self, '_all_streams') or not self._all_streams:
            return

        streams = self._all_streams.copy()
        dimension = getattr(self, "_filter_dimension", "quality")
        filter_val = self._filter_var.get()

        # 应用筛选（非"全部"时过滤）
        if filter_val and filter_val != "全部":
            streams = [s for s in streams
                       if s.get(dimension, "").strip() == filter_val]

        platform = getattr(self, "_result_platform", "")

        self._clear_result()
        
        # 重新渲染标题和标签栏（筛选后必须重建，否则标签栏消失）
        if hasattr(self, '_result_platform'):
            # 重建结果标题行
            title_frame = tk.Frame(self.result_inner, bg=Colors.BG_CARD)
            title_frame.pack(fill="x", pady=(8, 4))
            
            title_left = tk.Frame(title_frame, bg=Colors.BG_CARD)
            title_left.pack(side="left")
            tk.Label(title_left,
                     text=f" {platform} · 共{len(streams)}条流",
                     font=("Microsoft YaHei UI", 10),
                     bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY).pack(side="left")
            
            # 维度切换按钮
            dim_frame = tk.Frame(title_frame, bg=Colors.BG_CARD)
            dim_frame.pack(side="right")
            
            q_btn = tk.Label(dim_frame, text=" 清晰度 ",
                            font=("Microsoft YaHei UI", 9, "bold"),
                            cursor="hand2",
                            bg=Colors.ACCENT_BLUE if dimension == "quality" else Colors.BG_CARD,
                            fg="white" if dimension == "quality" else Colors.TEXT_PRIMARY,
                            padx=10, pady=3)
            f_btn = tk.Label(dim_frame, text=" 格式 ",
                            font=("Microsoft YaHei UI", 9),
                            cursor="hand2",
                            bg=Colors.ACCENT_BLUE if dimension == "format" else Colors.BG_CARD,
                            fg="white" if dimension == "format" else Colors.TEXT_PRIMARY,
                            padx=10, pady=3)
            q_btn.pack(side="left", padx=(0, 2))
            f_btn.pack(side="left")
            q_btn.bind("<Button-1>", lambda e: self._switch_filter_dimension("quality"))
            f_btn.bind("<Button-1>", lambda e: self._switch_filter_dimension("format"))
            self._filter_dim_buttons = (q_btn, f_btn)
            
            # 标签栏
            tags_frame = tk.Frame(self.result_inner, bg=Colors.BG_CARD)
            tags_frame.pack(fill="x", pady=(0, 6))
            self._filter_tags_frame = tags_frame
            
            # 重建分类标签（保持当前选中状态）
            self._build_and_render_filter_tags(self._all_streams)

        for i, s in enumerate(streams):
            self._render_stream_card(s, i, platform)

    def _copy_obs_url(self, proxy_url: str):
        """复制OBS代理地址到剪贴板"""
        self.root.clipboard_clear()
        self.root.clipboard_append(proxy_url)
        hevc_note = "（HEVC流已自动转码为H264）"
        self._show_toast(f"已复制代理地址：{proxy_url}  （粘贴到OBS即可）{hevc_note}")

    # ─── 刷新所有登录状态 ───
    def _refresh_all_login_status(self):
        """解析完成后刷新四平台登录状态"""
        try:
            if hasattr(self, '_ks_login_display'):
                self._ks_login_status = _check_ks_login_status()
                self._refresh_ks_login_display()
            if hasattr(self, '_tb_login_display'):
                self._tb_login_status = _check_tb_login_status()
                self._refresh_tb_login_display()
            if hasattr(self, '_xhs_login_display'):
                self._xhs_login_status = _check_xhs_login_status()
                self._refresh_xhs_login_display()
            if hasattr(self, '_dy_login_display'):
                pass  # 抖音无需登录状态
        except Exception:
            pass

    # ─── 系统代理开关按钮回调 ───
    def _refresh_proxy_btn_state(self):
        """刷新系统代理按钮状态（启动时延迟调用）"""
        try:
            if _is_system_proxy_on():
                self.proxy_toggle_btn.configure(
                    text="  关闭代理  ", bg="#e74c3c", fg="white",
                    activebackground="#c0392b",
                )
                server = _get_current_proxy_server()
                if server:
                    self.status_var.set(f"系统代理：已开启（{server}）")
                else:
                    self.status_var.set("系统代理：已开启")
            else:
                self.proxy_toggle_btn.configure(
                    text="  系统代理  ", bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
                    activebackground=Colors.BG_HOVER,
                )
                self.status_var.set("")
        except Exception:
            pass


    # ─── HEVC → H264 转码功能 ───────────────────────────────────
    def _on_transcode_click(self):
        """打开 HEVC→H264 转码对话框"""
        self._open_transcode_dialog()

    def _on_open_wechat_video_tool(self):
        """打开微信视频号下载工具"""
        exe_path = _ensure_wechat_video_tool()
        if not exe_path:
            self._show_toast("视频号工具未找到，请联系开发者")
            return
        self.status_var.set("正在启动视频号下载工具...")
        self.status_icon.configure(fg=Colors.ACCENT_GREEN)
        try:
            subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path))
            self.status_var.set("视频号下载工具已启动")
        except Exception as e:
            self._show_toast(f"启动失败: {e}")
            self.status_var.set("视频号工具启动失败")

    def _open_transcode_dialog(self, preset_url=""):
        """HEVC/H.265 → H.264 转码工具弹窗"""
        dlg = tk.Toplevel(self.root)
        dlg.title("HEVC → H.264 转码工具")
        dlg.geometry("700x480")
        dlg.resizable(True, True)
        dlg.configure(bg=Colors.BG_DARK)
        dlg.transient(self.root)
        dlg.grab_set()

        # ── 标题 ──
        tk.Label(
            dlg,
            text="HEVC / H.265 → H.264 转码",
            font=("Microsoft YaHei UI", 13, "bold"),
            bg=Colors.BG_DARK, fg="#a78bfa",
        ).pack(anchor="w", padx=24, pady=(18, 2))

        tk.Label(
            dlg,
            text="输入 HEVC 流链接，转码为 H.264（OBS/VLC 可直接播放）",
            font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_DARK, fg=Colors.TEXT_MUTED,
        ).pack(anchor="w", padx=24, pady=(0, 12))

        # ── 输入区域 ──
        input_frame = tk.Frame(dlg, bg=Colors.BG_CARD, bd=0)
        input_frame.pack(fill="x", padx=20, pady=(0, 8))

        tk.Label(
            input_frame, text="HEVC 流链接：",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
        ).pack(anchor="w", padx=12, pady=(10, 2))

        url_container = tk.Frame(input_frame, bg=Colors.BORDER, bd=1, relief="solid")
        url_container.pack(fill="x", padx=12, pady=(0, 10))

        url_var = tk.StringVar(value=preset_url)
        url_entry = tk.Entry(
            url_container, textvariable=url_var,
            font=("Consolas", 10),
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.ACCENT_BLUE,
            relief="flat", bd=0,
        )
        url_entry.pack(fill="x", expand=True, padx=2, pady=6)

        # ── 端口设置 ──
        settings_frame = tk.Frame(dlg, bg=Colors.BG_DARK)
        settings_frame.pack(fill="x", padx=20, pady=(0, 8))

        tk.Label(
            settings_frame, text="本地代理端口：",
            font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_DARK, fg=Colors.TEXT_SECONDARY,
        ).pack(side="left")

        port_var = tk.StringVar(value="19876")
        port_entry = tk.Entry(
            settings_frame, textvariable=port_var,
            font=("Consolas", 10), width=8,
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.ACCENT_BLUE,
            relief="flat", bd=1,
        )
        port_entry.pack(side="left", padx=(4, 16))

        tk.Label(
            settings_frame,
            text="转码后访问地址：http://127.0.0.1:<端口>/live",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_DARK, fg=Colors.TEXT_MUTED,
        ).pack(side="left")

        # ── 状态显示 ──
        status_frame = tk.Frame(dlg, bg=Colors.BG_CARD, bd=0)
        status_frame.pack(fill="x", padx=20, pady=(0, 8))

        status_var = tk.StringVar(value="就绪")
        status_label = tk.Label(
            status_frame, textvariable=status_var,
            font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
            anchor="w",
        )
        status_label.pack(fill="x", padx=12, pady=8)

        # ── 结果地址框 ──
        result_frame = tk.Frame(dlg, bg=Colors.BG_CARD, bd=0)
        result_frame.pack(fill="x", padx=20, pady=(0, 8))

        tk.Label(
            result_frame, text="转码代理地址（启动后复制到 OBS）：",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
        ).pack(anchor="w", padx=12, pady=(10, 2))

        result_var = tk.StringVar(value="")
        result_container = tk.Frame(result_frame, bg=Colors.BORDER, bd=1, relief="solid")
        result_container.pack(fill="x", padx=12, pady=(0, 6))
        result_entry = tk.Entry(
            result_container, textvariable=result_var,
            font=("Consolas", 10),
            bg=Colors.BG_INPUT, fg="#a78bfa",
            relief="flat", bd=0, state="readonly",
        )
        result_entry.pack(fill="x", expand=True, padx=2, pady=6)

        # ── 按钮行 ──
        btn_frame = tk.Frame(dlg, bg=Colors.BG_DARK)
        btn_frame.pack(fill="x", padx=20, pady=(4, 16))

        proxy_ref = [None]  # [LocalStreamProxy 实例]

        def start_transcode():
            url = url_var.get().strip()
            if not url:
                status_var.set("❌  请先输入 HEVC 流链接")
                status_label.configure(fg="#e74c3c")
                return
            if not url.startswith("http"):
                status_var.set("❌  链接格式不正确（需要以 http 开头）")
                status_label.configure(fg="#e74c3c")
                return
            try:
                port = int(port_var.get().strip())
            except ValueError:
                status_var.set("❌  端口号必须是数字")
                status_label.configure(fg="#e74c3c")
                return

            # 停止已有的代理
            if proxy_ref[0]:
                try:
                    proxy_ref[0].stop()
                except Exception:
                    pass
                proxy_ref[0] = None

            start_btn.configure(text="启动中...", state="disabled", bg=Colors.TEXT_MUTED)
            status_var.set("正在启动转码代理...")
            status_label.configure(fg=Colors.TEXT_MUTED)
            result_var.set("")

            def do_start():
                try:
                    proxy = LocalStreamProxy(
                        port=port,
                        platform="通用",
                        codec_hint="hevc",
                    )
                    local_url = proxy.start(url)
                    proxy_ref[0] = proxy
                    dlg.after(0, lambda: (
                        result_var.set(local_url),
                        status_var.set(f"✅  转码代理已启动  →  {local_url}"),
                        status_label.configure(fg=Colors.ACCENT_GREEN),
                        start_btn.configure(text="重新启动", state="normal", bg="#8b5cf6"),
                        stop_btn.configure(state="normal"),
                        copy_btn.configure(state="normal"),
                    ))
                except Exception as e:
                    dlg.after(0, lambda err=str(e): (
                        status_var.set(f"❌  启动失败：{err}"),
                        status_label.configure(fg="#e74c3c"),
                        start_btn.configure(text="启动转码代理", state="normal", bg="#8b5cf6"),
                    ))

            threading.Thread(target=do_start, daemon=True).start()

        def stop_transcode():
            if proxy_ref[0]:
                try:
                    proxy_ref[0].stop()
                except Exception:
                    pass
                proxy_ref[0] = None
            result_var.set("")
            status_var.set("代理已停止")
            status_label.configure(fg=Colors.TEXT_MUTED)
            stop_btn.configure(state="disabled")
            copy_btn.configure(state="disabled")
            start_btn.configure(text="启动转码代理", bg="#8b5cf6")

        def copy_result():
            url_txt = result_var.get()
            if url_txt:
                dlg.clipboard_clear()
                dlg.clipboard_append(url_txt)
                copy_btn.configure(text="已复制！")
                dlg.after(1500, lambda: copy_btn.configure(text="复制地址"))

        def on_dlg_close():
            stop_transcode()
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", on_dlg_close)

        start_btn = tk.Button(
            btn_frame, text="  启动转码代理  ",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg="#8b5cf6", fg="white",
            activebackground="#7c3aed", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=16, pady=6,
            command=start_transcode,
        )
        start_btn.pack(side="left")

        stop_btn = tk.Button(
            btn_frame, text="  停止  ",
            font=("Microsoft YaHei UI", 10),
            bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
            activebackground=Colors.BG_HOVER,
            relief="flat", bd=0, cursor="hand2", padx=14, pady=6,
            state="disabled",
            command=stop_transcode,
        )
        stop_btn.pack(side="left", padx=(8, 0))

        copy_btn = tk.Button(
            btn_frame, text="  复制地址  ",
            font=("Microsoft YaHei UI", 10),
            bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
            activebackground=Colors.BG_HOVER,
            relief="flat", bd=0, cursor="hand2", padx=14, pady=6,
            state="disabled",
            command=copy_result,
        )
        copy_btn.pack(side="left", padx=(8, 0))

        # 粘贴已有链接
        cur_url = self.url_var.get().strip() if hasattr(self, 'url_var') else ""
        if cur_url and cur_url.startswith("http"):
            url_var.set(cur_url)

        url_entry.focus_set()

    def _on_copy_all(self):
        """点击「复制全部链接」按钮 - 将所有流的 URL 复制到剪贴板"""
        if not self._all_streams:
            self.status_var.set("没有可复制的流链接")
            self._show_toast("没有可复制的流链接")
            return
        urls = []
        for s in self._all_streams:
            url = s.get("url", "")
            if url:
                urls.append(url)
        if not urls:
            self.status_var.set("没有可复制的流链接")
            return
        text = "\n".join(urls)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(f"已复制 {len(urls)} 个流链接到剪贴板")
        self.copy_all_btn.configure(text="已复制!")
        self.root.after(1500, lambda: self.copy_all_btn.configure(text="  复制全部链接  "))
        self._show_toast(f"已复制 {len(urls)} 个链接")

    def _on_toggle_system_proxy(self):
        """点击「系统代理」/「关闭代理」按钮"""
        try:
            if _is_system_proxy_on():
                # 当前开启 → 关闭
                _clear_system_proxy()
                self.proxy_toggle_btn.configure(
                    text="  系统代理  ", bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
                    activebackground=Colors.BG_HOVER,
                )
                self.status_var.set("系统代理已关闭，流量不再走代理")
                self._show_toast("系统代理已关闭")
            else:
                # 当前关闭 → 开启（默认端口 8080）
                addr = _set_system_proxy(8080)
                self.proxy_toggle_btn.configure(
                    text="  关闭代理  ", bg="#e74c3c", fg="white",
                    activebackground="#c0392b",
                )
                self.status_var.set(f"系统代理已开启（{addr}）")
                self._show_toast(f"系统代理已设为 {addr}")
        except Exception as e:
            self._show_error(str(e))


    # ─── 显示错误 ───
    def _show_error(self, error_msg: str):
        self._clear_result()

        error_card = tk.Frame(
            self.result_inner, bg=Colors.BG_CARD,
            highlightbackground=Colors.ACCENT_RED, highlightthickness=1,
        )
        error_card.pack(fill="x", pady=10, padx=2)

        error_inner = tk.Frame(error_card, bg=Colors.BG_CARD)
        error_inner.pack(fill="x", padx=20, pady=16)

        tk.Label(
            error_inner, text="解析失败",
            font=("Microsoft YaHei UI", 14, "bold"),
            bg=Colors.BG_CARD, fg=Colors.ACCENT_RED,
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            error_inner, text=error_msg,
            font=("Microsoft YaHei UI", 10),
            bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
            wraplength=700, justify="left", anchor="w",
        ).pack(anchor="w", fill="x")

        # 小红书错误时提供「重新登录」按钮
        xhs_keywords = ["小红书", "登录", "cookie", "Cookie", "xiaohongshu"]
        is_xhs_error = any(kw in error_msg for kw in xhs_keywords)
        if is_xhs_error:
            # 重新检测 Cookie 实际状态，而不是直接标记 expired
            # 解析失败不等于 Cookie 失效（可能是直播间已结束、风控、网络等原因）
            actual_status = _check_xhs_login_status()
            if actual_status != "logged_in":
                self._xhs_login_status = actual_status
                self._refresh_xhs_login_display()

            xhs_action_row = tk.Frame(error_inner, bg=Colors.BG_CARD)
            xhs_action_row.pack(fill="x", pady=(12, 0))

            tk.Label(
                xhs_action_row, text="可能是登录状态失效导致，",
                font=("Microsoft YaHei UI", 9),
                bg=Colors.BG_CARD, fg=Colors.ACCENT_ORANGE,
            ).pack(side="left")

            xhs_relogin_btn = tk.Label(
                xhs_action_row, text=" 点击重新登录小红书 ",
                font=("Microsoft YaHei UI", 9, "bold"),
                bg="#FE2C55", fg="white",
                padx=8, pady=2, cursor="hand2",
            )
            xhs_relogin_btn.pack(side="left")
            xhs_relogin_btn.bind("<Button-1>", lambda e: self._do_xhs_relogin())
            xhs_relogin_btn.bind("<Enter>", lambda e: xhs_relogin_btn.configure(bg="#E02050"))
            xhs_relogin_btn.bind("<Leave>", lambda e: xhs_relogin_btn.configure(bg="#FE2C55"))

        # 快手错误时提供「重新登录」按钮
        ks_keywords = ["快手", "登录", "风控", "cookie", "Cookie", "passport"]
        is_ks_error = any(kw in error_msg for kw in ks_keywords)
        if is_ks_error:
            # 重新检测 Cookie 实际状态，而不是直接标记 expired
            actual_status = _check_ks_login_status()
            if actual_status != "logged_in":
                self._ks_login_status = actual_status
                self._refresh_ks_login_display()

            ks_action_row = tk.Frame(error_inner, bg=Colors.BG_CARD)
            ks_action_row.pack(fill="x", pady=(12, 0))

            tk.Label(
                ks_action_row, text="可能是登录状态失效导致，",
                font=("Microsoft YaHei UI", 9),
                bg=Colors.BG_CARD, fg=Colors.ACCENT_ORANGE,
            ).pack(side="left")

            relogin_btn = tk.Label(
                ks_action_row, text=" 点击重新登录 ",
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=Colors.ACCENT_ORANGE, fg="white",
                padx=8, pady=2, cursor="hand2",
            )
            relogin_btn.pack(side="left")
            relogin_btn.bind("<Button-1>", lambda e: self._do_ks_relogin())
            relogin_btn.bind("<Enter>", lambda e: relogin_btn.configure(bg="#e8a800"))
            relogin_btn.bind("<Leave>", lambda e: relogin_btn.configure(bg=Colors.ACCENT_ORANGE))

        # 淘宝错误时提供「重新登录」按钮
        tb_keywords = ["淘宝", "登录", "cookie", "Cookie", "login.taobao.com"]
        is_tb_error = any(kw in error_msg for kw in tb_keywords)
        if is_tb_error:
            # 重新检测 Cookie 实际状态，而不是直接标记 expired
            actual_status = _check_tb_login_status()
            if actual_status != "logged_in":
                self._tb_login_status = actual_status
                self._refresh_tb_login_display()

            tb_action_row = tk.Frame(error_inner, bg=Colors.BG_CARD)
            tb_action_row.pack(fill="x", pady=(12, 0))

            tk.Label(
                tb_action_row, text="可能是登录状态失效导致，",
                font=("Microsoft YaHei UI", 9),
                bg=Colors.BG_CARD, fg=Colors.ACCENT_ORANGE,
            ).pack(side="left")

            tb_relogin_btn = tk.Label(
                tb_action_row, text=" 点击重新登录淘宝 ",
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=Colors.ACCENT_ORANGE, fg="white",
                padx=8, pady=2, cursor="hand2",
            )
            tb_relogin_btn.pack(side="left")
            tb_relogin_btn.bind("<Button-1>", lambda e: self._do_tb_relogin())
            tb_relogin_btn.bind("<Enter>", lambda e: tb_relogin_btn.configure(bg="#e8a800"))
            tb_relogin_btn.bind("<Leave>", lambda e: tb_relogin_btn.configure(bg=Colors.ACCENT_ORANGE))

        # 抖音错误时提供「重新登录」按钮
        dy_keywords = ["抖音", "登录", "cookie", "Cookie", "浏览器", "Playwright"]
        is_dy_error = any(kw in error_msg for kw in dy_keywords)
        if is_dy_error:
            # 重新检测 Cookie 实际状态
            actual_status = _check_dy_login_status()
            if actual_status != "logged_in":
                self._dy_login_status = actual_status
                self._refresh_dy_login_display()

            dy_action_row = tk.Frame(error_inner, bg=Colors.BG_CARD)
            dy_action_row.pack(fill="x", pady=(12, 0))

            tk.Label(
                dy_action_row, text="可能是登录状态失效导致，",
                font=("Microsoft YaHei UI", 9),
                bg=Colors.BG_CARD, fg=Colors.ACCENT_ORANGE,
            ).pack(side="left")

            dy_relogin_btn = tk.Label(
                dy_action_row, text=" 点击重新登录抖音 ",
                font=("Microsoft YaHei UI", 9, "bold"),
                bg="#161823", fg="white",
                padx=8, pady=2, cursor="hand2",
            )
            dy_relogin_btn.pack(side="left")
            dy_relogin_btn.bind("<Button-1>", lambda e: self._do_dy_relogin())
            dy_relogin_btn.bind("<Enter>", lambda e: dy_relogin_btn.configure(bg="#252840"))
            dy_relogin_btn.bind("<Leave>", lambda e: dy_relogin_btn.configure(bg="#161823"))

        self.status_var.set("解析失败")
        self.status_icon.configure(fg=Colors.ACCENT_RED)
        self.fetch_btn.configure(text="  获取流链接  ", bg="#238636")

    # ─── Toast 提示 ───
    def _show_toast(self, message: str):
        """底部弹出提示"""
        toast = tk.Label(
            self.root, text=f"  {message}  ",
            font=("Microsoft YaHei UI", 10),
            bg=Colors.ACCENT_GREEN, fg="white",
            padx=16, pady=6,
        )
        toast.place(relx=0.5, rely=0.92, anchor="center")
        self.root.after(2000, toast.destroy)

    # ─── 清空结果 ───
    def _clear_result(self):
        for w in self.result_inner.winfo_children():
            w.destroy()
        self._stream_cards = []

    # ─── 本地代理管理 ───
    def _start_stream_proxy(self, streams, platform: str = "淘宝直播"):
        """为直播流创建独立的本地代理（后台线程），就绪后通知 UI 更新 OBS 按钮。

        支持平台：
        - 淘宝直播：alicdn.com / tbcdn.cn / taobaocdn.com
        - 小红书：xhscdn.com（HEVC 编码或需要 Referer 头）
        """
        # 先停掉旧的
        self._stop_stream_proxy()

        self._proxy_urls = {}
        self._proxy_ready = False
        self._proxy_hevc_checked = False

        # 根据平台收集需要代理的流
        if platform == "淘宝直播":
            proxy_streams = [s for s in streams
                            if "alicdn.com" in s.get("url", "")
                            or "tbcdn.cn" in s.get("url", "")
                            or "taobaocdn.com" in s.get("url", "")]
        elif platform == "小红书":
            # 小红书 xhscdn.com 流全部走代理（和淘宝逻辑一致）
            # 原因：OBS 直接拉 xhscdn.com FLV 有声音无画面（HEVC 编码或 CDN 兼容性问题）
            proxy_streams = [s for s in streams
                            if "xhscdn.com" in s.get("url", "")]
            print(f"[本地代理] 小红书流共 {len(streams)} 条，需代理: {len(proxy_streams)} 条")
        else:
            proxy_streams = []

        if not proxy_streams:
            return

        # 记录当前代理的平台
        self._proxy_platform = platform

        def _do_start():
            try:
                for s in proxy_streams:
                    target_url = s["url"]
                    codec_hint = s.get("codec", "")
                    proxy = LocalStreamProxy(platform=platform, codec_hint=codec_hint)
                    local_url = proxy.start(target_url)
                    self._stream_proxies[target_url] = proxy
                    self._proxy_urls[target_url] = local_url
                    hint_msg = f" (codec={codec_hint})" if codec_hint else ""
                    print(f"[本地代理] 已启动({platform}){hint_msg}：{local_url} -> {target_url[:80]}...")

                self._proxy_ready = True
                print(f"[_do_start] 代理全部就绪，准备通知 UI 更新 {len(self._proxy_urls)} 个按钮...")
                # 代理全部就绪，通知 UI 线程更新 OBS 按钮
                self.root.after(0, self._on_proxy_ready)
            except Exception as e:
                print(f"[本地代理] 启动失败：{e}")
                self.root.after(0, self._on_proxy_failed)

        threading.Thread(target=_do_start, daemon=True).start()

    def _on_proxy_ready(self):
        """代理启动成功后，重新渲染流卡片以更新 OBS 按钮状态。"""
        print(f"[_on_proxy_ready] 代理就绪，重新渲染流卡片... _proxy_ready={getattr(self, '_proxy_ready', False)}")
        try:
            self._render_filtered_streams()
        except Exception as e:
            print(f"[_on_proxy_ready] 重新渲染失败: {e}")
            # 降级：尝试直接 configure 旧按钮
            if hasattr(self, '_obs_btn_refs'):
                for orig_url, btn in self._obs_btn_refs:
                    try:
                        btn.configure(text="OBS", bg="#ff5000", fg="white", state="normal", cursor="hand2")
                    except Exception:
                        pass

        # 启动定时器：当 OBS 首次连接后 HEVC 检测结果可能更新按钮文本
        self._schedule_hevc_check()

    def _schedule_hevc_check(self):
        """定时检查 HEVC 检测状态，更新 OBS 按钮文本。HEVC 在 OBS 首次连接时才检测。"""
        if not self._proxy_ready:
            return

        all_checked = True
        for orig_url, btn in self._obs_btn_refs:
            proxy = self._stream_proxies.get(orig_url)
            if not proxy:
                continue
            try:
                btn_text = btn.cget("text")
                if proxy.is_hevc() and "转码" not in btn_text:
                    btn.configure(text="OBS(转码)")
                elif proxy.is_hevc() or "转码" in btn_text:
                    all_checked = True  # 已确定
                else:
                    # 未连接过的代理，继续等待
                    all_checked = False
            except tk.TclError:
                pass

        # 如果所有按钮都已确认状态，停止轮询
        if all_checked:
            self._proxy_hevc_checked = True
        else:
            self.root.after(1000, self._schedule_hevc_check)

    def _on_proxy_failed(self):
        """代理启动失败，更新 OBS 按钮提示。"""
        if not hasattr(self, '_obs_btn_refs'):
            return
        for orig_url, btn in self._obs_btn_refs:
            proxy = self._stream_proxies.get(orig_url)
            if proxy and proxy.is_running():
                # 该流的代理成功启动，不标记失败
                continue
            try:
                btn.configure(text="代理失败", bg="#cc3300")
            except tk.TclError:
                pass

    def _stop_stream_proxy(self):
        """停止所有本地代理。"""
        for url, proxy in self._stream_proxies.items():
            try:
                proxy.stop()
            except Exception:
                pass
        self._stream_proxies = {}
        self._proxy_urls = {}
        self._proxy_ready = False
        self._proxy_hevc_checked = False

    def _get_proxy_url(self, original_url: str) -> str:
        """获取原始 URL 对应的代理地址。"""
        return self._proxy_urls.get(original_url, "")

    def _copy_proxy_url(self, original_url: str):
        """复制代理地址到剪贴板。"""
        proxy_url = self._get_proxy_url(original_url)
        if proxy_url:
            self.root.clipboard_clear()
            self.root.clipboard_append(proxy_url)
            proxy = self._stream_proxies.get(original_url)
            hevc_note = "（HEVC转码中）" if (proxy and proxy.is_hevc()) else ""
            self._show_toast(f"已复制代理地址：{proxy_url}  （粘贴到 OBS 即可）{hevc_note}")
        else:
            # 代理还没就绪，稍等一下
            self._show_toast("代理正在启动中，请稍后重试...")





# ─── 启动密码验证 ────────────────────────────────────────────────

PASSWORD_DOC_URL = "https://www.yuque.com/r/note/11037a5a-b85f-4c41-bf08-2fe003b7afcd"

# 密码缓存（避免频繁请求文档）
_cached_password = None
_cached_password_time = 0
PASSWORD_CACHE_SECONDS = 30 * 60  # 30 分钟缓存


def _fetch_password_from_doc(timeout: float = 25) -> str:
    """通过 Playwright headless 模式从语雀文档抓取密码文本。
    
    Args:
        timeout: 整体超时时间（秒），默认 25 秒
    """
    global _cached_password, _cached_password_time

    # 检查缓存
    now = time.time()
    if _cached_password and (now - _cached_password_time) < PASSWORD_CACHE_SECONDS:
        return _cached_password

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[密码验证] playwright 未安装")
        return ""

    # ── 禁止 Playwright 子进程弹出 CMD 黑窗口 ──
    if sys.platform == "win32":
        # 临时 monkey-patch subprocess.Popen，给所有子进程加 CREATE_NO_WINDOW
        _orig_popen = subprocess.Popen
        _popen_patched = False

        def _no_console_popen(*args, **kwargs):
            creationflags = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            kwargs["creationflags"] = creationflags
            return _orig_popen(*args, **kwargs)

        subprocess.Popen = _no_console_popen
        _popen_patched = True

    password = ""
    # 用线程超时保护，防止 Playwright 卡死导致整个验证卡住
    result_holder = [""]
    error_holder = [None]

    def _do_fetch():
        try:
            with sync_playwright() as p:
                # 尝试用嵌入式 Chromium → 系统 Chrome → Edge
                browser = None
                launch_errors = []

                # 优先嵌入式 Chromium
                embedded_chromium = _ensure_chromium_ready()
                if embedded_chromium:
                    try:
                        browser = p.chromium.launch(
                            headless=True,
                            executable_path=os.path.join(embedded_chromium, "chrome.exe"),
                            args=["--no-sandbox", "--disable-gpu"],
                        )
                    except Exception as e:
                        launch_errors.append(f"Embedded Chromium: {e}")

                # Playwright 内置 Chromium
                if not browser:
                    try:
                        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                    except Exception as e:
                        launch_errors.append(f"Playwright Chromium: {e}")

                # 系统 Chrome
                if not browser:
                    try:
                        browser = p.chromium.launch(
                            headless=True,
                            channel="chrome",
                            args=["--no-sandbox"],
                        )
                    except Exception as e:
                        launch_errors.append(f"Chrome: {e}")

                # 系统 Edge
                if not browser:
                    try:
                        browser = p.chromium.launch(
                            headless=True,
                            channel="msedge",
                            args=["--no-sandbox"],
                        )
                    except Exception as e:
                        launch_errors.append(f"Edge: {e}")

                if not browser:
                    print(f"[密码验证] 无法启动浏览器: {'; '.join(launch_errors)}")
                    return

                page = browser.new_page(viewport={"width": 1280, "height": 800})
                print("[密码验证] 正在获取密码...")

                page.goto(PASSWORD_DOC_URL, wait_until="domcontentloaded", timeout=30000)

                # 等待文档内容加载（语雀/腾讯文档的编辑器需要时间渲染）
                try:
                    page.wait_for_selector(
                        ".yuque-doc-content, .doc-content, .ql-editor, [contenteditable], "
                        ".text-editor, .editor-content, .doc-body, .ne-viewer, .article-content",
                        timeout=15000
                    )
                except Exception:
                    pass

                # 额外等待确保内容渲染完成
                page.wait_for_timeout(5000)

                # 策略：先抓全文，再智能提取密码
                full_text = ""
                try:
                    full_text = page.inner_text("body").strip()
                except Exception:
                    pass

                # 提取成功后只打印行数，不打印文档内容（避免泄露密码）
                print(f"[密码验证] 文档已加载，共 {len(full_text)} 字符")

                # UI 噪声词，跳过
                skip_words = ["菜单", "插入", "正文", "默认字体", "快捷工具",
                              "PDF转换", "生成图片", "排版美化", "打印",
                              "腾讯文档", "正在同步", "无障碍", "登录",
                              "评论", "历史版本", "分享", "更多",
                              "直播流软件密码", "直播流", "软件密码"]
                # 密码关键词
                pwd_keywords = ["密码", "password", "口令", "验证"]

                lines = full_text.split("\n")
                # 清理并去重
                clean_lines = []
                for line in lines:
                    c = line.strip()
                    if c and len(c) < 500:
                        clean_lines.append(c)

                # 策略 1：找到包含"密码"关键词的行，取下一行的值
                found = ""
                for i, cl in enumerate(clean_lines):
                    if any(kw in cl.lower() for kw in pwd_keywords):
                        # 跳过纯说明文字（如"直播流软件密码"本身）
                        if any(sw == cl for sw in skip_words):
                            if i + 1 < len(clean_lines):
                                next_val = clean_lines[i + 1].strip()
                                if next_val and not any(sw in next_val for sw in skip_words):
                                    found = next_val
                                    break
                        # 如果这行格式是 "密码：xxx" 或 "密码 xxx"
                        for kw in pwd_keywords:
                            if kw in cl.lower():
                                parts = cl.split(kw)
                                if len(parts) >= 2:
                                    val = parts[-1].strip().lstrip("：:：= \t")
                                    if val and len(val) < 100:
                                        found = val
                                        break
                            if found:
                                break
                        if found:
                            break

                # 策略 2：如果策略 1 没找到，取所有短行中最短的（密码通常很短）
                if not found:
                    short_lines = []
                    for cl in clean_lines:
                        if not any(sw in cl for sw in skip_words):
                            if 1 <= len(cl) <= 50:
                                short_lines.append(cl)
                    if short_lines:
                        # 按长度排序，取最短的
                        short_lines.sort(key=len)
                        found = short_lines[0]

                if found:
                    result_holder[0] = found

                browser.close()
        except Exception as e:
            error_holder[0] = e

    # 启动线程并等待结果（带超时）
    fetch_thread = threading.Thread(target=_do_fetch, daemon=True)
    fetch_thread.start()
    fetch_thread.join(timeout=timeout)

    if fetch_thread.is_alive():
        print(f"[密码验证] 获取超时（{timeout}s），跳过本次")
        return _cached_password or ""  # 返回缓存（如果有）

    if error_holder[0]:
        print(f"[密码验证] 获取密码失败: {error_holder[0]}")

    password = result_holder[0].strip()  # 额外 strip，去掉可能的空白字符
    if password:
        _cached_password = password
        _cached_password_time = now
        # 只显示长度，不打印密码内容
        print(f"[密码验证] 密码获取成功 (长度={len(password)})")
    else:
        print("[密码验证] 未能从文档中提取到密码")

    # 恢复 subprocess.Popen
    if sys.platform == "win32" and _popen_patched:
        subprocess.Popen = _orig_popen

    return password


class PasswordGate:
    """启动密码验证页面"""

    def __init__(self, root, on_success):
        self.root = root
        self.on_success = on_success
        self._frame = None

        # 创建密码验证界面
        self.root.withdraw()  # 先隐藏主窗口

        self.win = tk.Toplevel(root)
        self.win.title("LiveStreamFetcher — 密码验证")
        self.win.geometry("420x320")
        self.win.resizable(False, False)
        self.win.configure(bg=Colors.BG_DARK)

        # 居中显示
        self.win.update_idletasks()
        x = (self.win.winfo_screenwidth() - 420) // 2
        y = (self.win.winfo_screenheight() - 320) // 2
        self.win.geometry(f"420x320+{x}+{y}")

        # 圆角窗口
        try:
            from ctypes import windll
            windll.dwmapi.DwmSetWindowAttribute(
                windll.user32.GetParent(self.win.winfo_id()),
                20, byref := __import__('ctypes').byref(__import__('ctypes').c_int(2)), 4)
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self):
        """构建密码验证界面"""
        bg = Colors.BG_DARK

        # 居中容器
        container = tk.Frame(self.win, bg=bg)
        container.place(relx=0.5, rely=0.5, anchor="center")

        # 图标
        tk.Label(
            container, text="🔐",
            font=("Segoe UI Emoji", 36),
            bg=bg, fg=Colors.ACCENT_BLUE
        ).pack(pady=(0, 12))

        # 标题
        tk.Label(
            container, text="请输入访问密码",
            font=("Microsoft YaHei UI", 14, "bold"),
            bg=bg, fg=Colors.TEXT_PRIMARY
        ).pack()

        # 副标题
        tk.Label(
            container, text="输入密码后即可使用软件",
            font=("Microsoft YaHei UI", 9),
            bg=bg, fg=Colors.TEXT_MUTED
        ).pack(pady=(2, 20))

        # 密码输入框
        input_frame = tk.Frame(container, bg=Colors.BORDER, bd=1, relief="solid")
        input_frame.pack(fill="x", ipady=1)

        self._pwd_var = tk.StringVar()
        self._pwd_entry = tk.Entry(
            input_frame, textvariable=self._pwd_var,
            font=("Consolas", 14), show="●",
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.ACCENT_BLUE,
            relief="flat", bd=0,
            width=28
        )
        self._pwd_entry.pack(padx=12, pady=8)
        self._pwd_entry.focus_set()

        # 绑定回车键
        self._pwd_entry.bind("<Return>", lambda e: self._verify())

        # 错误提示
        self._error_label = tk.Label(
            container, text="",
            font=("Microsoft YaHei UI", 9),
            bg=bg, fg=Colors.ACCENT_RED
        )
        self._error_label.pack(pady=(8, 4))

        # 验证按钮
        self._btn = tk.Button(
            container, text="验  证",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg=Colors.ACCENT_BLUE, fg="#ffffff",
            activebackground="#4090e0", activeforeground="#ffffff",
            relief="flat", bd=0, cursor="hand2",
            padx=40, pady=8,
            command=self._verify
        )
        self._btn.pack(pady=(8, 0))

        # 加载中状态
        self._loading = False

    def _set_loading(self, loading: bool):
        """切换加载状态"""
        self._loading = loading
        try:
            winfo = self._btn.winfo_exists()
        except Exception:
            return
        if not winfo:
            return
        if loading:
            self._btn.configure(text="验证中...", state="disabled", bg=Colors.TEXT_MUTED)
        else:
            # 强制刷新按钮状态，确保可点击
            self._loading = False
            self._btn.configure(text="验  证", state="normal", bg=Colors.ACCENT_BLUE,
                                activebackground="#4090e0", activeforeground="#ffffff")

    def _verify(self):
        """验证密码"""
        if self._loading:
            return

        user_input = self._pwd_var.get().strip()
        if not user_input:
            self._error_label.configure(text="请输入密码")
            self._pwd_entry.focus_set()
            return

        self._set_loading(True)
        self._error_label.configure(text="正在从云端获取密码...")

        # 在后台线程中获取云端密码并验证
        def _do_verify():
            try:
                correct_pwd = _fetch_password_from_doc()

                if not correct_pwd:
                    self.root.after(0, lambda: self._on_verify_fail("无法获取云端密码，请检查网络"))
                    return

                print(f"[密码验证] 用户输入长度={len(user_input)}")
                print(f"[密码验证] 云端密码长度={len(correct_pwd)}")
                print(f"[密码验证] 是否匹配: {user_input == correct_pwd}")

                if user_input == correct_pwd:
                    self.root.after(0, self._on_verify_success)
                else:
                    self.root.after(0, lambda: self._on_verify_fail("密码错误，请重新输入"))
            except Exception as e:
                _verify_err = f"验证失败: {e}"
                self.root.after(0, lambda: self._on_verify_fail(_verify_err))
            finally:
                # 无条件重置 loading 状态，防止按钮永远不可点击
                self._loading = False
                self.root.after(0, lambda: self._set_loading(False))

        threading.Thread(target=_do_verify, daemon=True).start()

    def _on_verify_success(self):
        """验证成功"""
        self._loading = False
        self.win.destroy()
        self.root.deiconify()  # 显示主窗口
        self.on_success()

    def _on_verify_fail(self, msg: str):
        """验证失败"""
        self._loading = False
        self._set_loading(False)
        try:
            if self._error_label.winfo_exists():
                self._error_label.configure(text=msg)
            if self._pwd_entry.winfo_exists():
                self._pwd_var.set("")
                self._pwd_entry.focus_set()
        except Exception:
            pass


# ─── 启动 ────────────────────────────────────────────────

def _run_mitmdump_worker():
    """mitmdump 工作模式入口（子进程调用）。

    当 EXE 被以 --mitmdump-worker 参数启动时，
    直接执行 mitmproxy.tools.main.mitmdump()，运行代理服务器。
    这使得 mitmproxy 可以完全内嵌到 EXE 中，无需外部依赖。
    """
    from mitmproxy.tools.main import mitmdump
    import sys as _sys

    # 清理 mitmproxy 不认识的自定义参数，但保留 mitmproxy 原生参数
    # 自定义参数：--mitmdump-worker（入口标识）、--result-file（结果文件路径）
    # --set-confdir（自定义 CA 证书目录，通过环境变量传入）
    # mitmproxy 原生参数必须保留：-p（端口）、-s（脚本）、--set（配置选项）、--mode（代理模式）等
    # ★ 注意：--mode 是 mitmproxy 原生参数（如 local:Weixin），绝对不能跳过！
    _skip_args = {"--mitmdump-worker", "--result-file"}
    _custom_confdir_value = None
    # 以上自定义参数都需要跳过其值（下一个参数）
    _new_argv = []
    _skip_next = False
    _i = 0
    while _i < len(_sys.argv):
        arg = _sys.argv[_i]
        if _skip_next:
            _skip_next = False
            _i += 1
            continue
        if arg in _skip_args:
            # 所有带值的自定义参数都跳过下一个
            if (_i + 1) < len(_sys.argv):
                _skip_next = True
            _i += 1
            continue
        # 捕获自定义的 confdir 参数（多种格式）
        if arg.startswith("--set-confdir="):
            _custom_confdir_value = arg.split("=", 1)[1]
            _i += 1
            continue
        if arg == "--confdir":
            # 兼容旧格式：--confdir <path>
            if (_i + 1) < len(_sys.argv):
                _custom_confdir_value = _sys.argv[_i + 1]
                _skip_next = True
            _i += 1
            continue
        # 关键修复：处理 --set confdir=<path> 格式（父进程传的是两个独立参数）
        if arg == "--set" and (_i + 1) < len(_sys.argv):
            _next_arg = _sys.argv[_i + 1]
            if _next_arg.startswith("confdir="):
                _custom_confdir_value = _next_arg.split("=", 1)[1]
                # 保留 --set 和 confdir=... 让 mitmdump 自己解析（官方支持）
                # 同时也设置环境变量作为双保险
                _new_argv.append(arg)
                _new_argv.append(_next_arg)
                _i += 2
                continue
        # 跳过 argv[0]（EXE 自身路径）
        if _i == 0:
            _i += 1
            continue
        _new_argv.append(arg)
        _i += 1
    _sys.argv = [_new_argv[0]] + _new_argv[1:] if _new_argv else ["mitmdump"]

    # 通过环境变量设置自定义 confdir（mitmdump CLI 不支持 --confdir 参数）
    if _custom_confdir_value:
        os.environ["MITMPROXY_CONFDIR"] = _custom_confdir_value
        print(f"[mitmdump-worker] 自定义 confdir: {_custom_confdir_value}")

    print("[mitmdump-worker] 启动 mitmproxy 代理服务器...")
    try:
        mitmdump()
    except SystemExit:
        pass
    except Exception as e:
        print(f"[mitmdump-worker] 异常退出: {e}")
        _sys.exit(1)


def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass

    # 密码验证
    app_ref = [None]

    def on_password_ok():
        app_ref[0] = LiveStreamFetcherApp(root)

    # 先创建 Toplevel 的根窗口（隐藏）
    root.withdraw()
    gate = PasswordGate(root, on_password_ok)

    root.mainloop()


if __name__ == "__main__":
    if "--mitmdump-worker" in sys.argv:
        # 子进程模式：直接运行 mitmdump 代理服务器
        _run_mitmdump_worker()
    else:
        main()
