# -*- coding: utf-8 -*-
"""
多平台直播视频流获取工具 v7.5
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

    v8.3.7: 优先放 EXE 同目录 cache/LiveStreamFetcher/kuaishou_browser_data/
    不可写时回退 %APPDATA%/LiveStreamFetcher/kuaishou_browser_data/
    v8.4.12: 统一到 shared_browser_data（所有平台共用一个 Chrome Profile，
    Chrome 按域名隔离 cookie）。之前平台按钮浏览器写 shared_browser_data、
    fetch 读 kuaishou_browser_data，cookie 不在同一目录 → 登录后解析仍要登录。
    """
    return _get_app_cache_dir("shared_browser_data")


def _check_ks_login_status():
    """检测快手浏览器持久化目录中是否存在有效的登录 Cookie。

    通过直接读取 SQLite 数据库（处理 DB 锁定）检查关键登录 cookie。
    返回:
        "logged_in"  — 存在快手登录 Cookie
        "never"      — 从未登录（无有效 Cookie）
        "expired"    — 保留字段
    """
    import sqlite3
    import shutil
    import tempfile
    import time

    # Chrome Web Kit 时间戳是 1601-01-01 起的微秒数
    CHROME_EPOCH_OFFSET = 11644473600000000
    now_chrome_us = int(time.time() * 1000000) + CHROME_EPOCH_OFFSET

    data_dir = _get_ks_browser_data_dir()
    default_dir = os.path.join(data_dir, "Default")
    if not os.path.isdir(default_dir):
        return "never"

    login_cookie_names = {"userId", "kuaishou.live.web_st", "did", "kpn", "kpf"}

    cookie_paths = [
        os.path.join(default_dir, "Cookies"),
        os.path.join(default_dir, "Network", "Cookies"),
    ]

    for db_path in cookie_paths:
        if not os.path.isfile(db_path):
            continue

        db_to_read = db_path
        try:
            conn = sqlite3.connect(db_path, timeout=2)
            conn.execute("PRAGMA quick_check")
            conn.close()
        except Exception:
            try:
                tmp_db = os.path.join(tempfile.gettempdir(), f"lsf_ks_check_{os.getpid()}.db")
                shutil.copy2(db_path, tmp_db)
                db_to_read = tmp_db
            except Exception:
                continue

        try:
            conn = sqlite3.connect(db_to_read)
            cursor = conn.cursor()
            cursor.execute("SELECT name, expires_utc FROM cookies WHERE host_key LIKE '%kuaishou%'")
            # 过滤掉已过期的 cookie（expires_utc > 0 且 <= 当前 Chrome 时间 = 已过期）
            valid_names = {
                row[0] for row in cursor.fetchall()
                if row[1] == 0 or row[1] > now_chrome_us
            }
            conn.close()
            if valid_names & login_cookie_names:
                if db_to_read != db_path and os.path.exists(db_to_read):
                    try: os.remove(db_to_read)
                    except Exception: pass
                return "logged_in"
        except Exception:
            pass
        finally:
            if db_to_read != db_path and os.path.exists(db_to_read):
                try: os.remove(db_to_read)
                except Exception: pass

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

    淘宝登录 Cookie 可能存在 taobao.com / tmall.com / alibabagroup.com 等多个域。
    通过读取 SQLite 数据库（处理 DB 锁定）检查关键登录 cookie。
    返回:
        "logged_in"  — 存在登录 Cookie
        "never"      — 从未登录（无有效 Cookie）
        "expired"    — 保留字段
    """
    import sqlite3
    import shutil
    import tempfile
    import time

    # Chrome Web Kit 时间戳是 1601-01-01 起的微秒数
    CHROME_EPOCH_OFFSET = 11644473600000000
    now_chrome_us = int(time.time() * 1000000) + CHROME_EPOCH_OFFSET

    data_dir = _get_tb_browser_data_dir()
    default_dir = os.path.join(data_dir, "Default")
    if not os.path.isdir(default_dir):
        return "never"

    # 关键登录 cookie 名称
    login_cookie_names = {"_tb_token_", "cookie2", "sgcookie", "unb", "lgc", "nk", "t", "isg"}

    # 多域查询
    domain_patterns = ["%taobao.com%", "%tmall.com%", "%alibaba.com%", "%alicdn.com%"]

    cookie_paths = [
        os.path.join(default_dir, "Cookies"),
        os.path.join(default_dir, "Network", "Cookies"),
    ]

    for db_path in cookie_paths:
        if not os.path.isfile(db_path):
            continue

        # 如果 DB 锁定（persistent_context 浏览器正在运行），复制到临时文件再读
        db_to_read = db_path
        try:
            # 先尝试直接读
            conn = sqlite3.connect(db_path, timeout=2)
            conn.execute("PRAGMA quick_check")
            conn.close()
        except Exception:
            # 锁定 → 复制到临时文件再读
            try:
                tmp_db = os.path.join(tempfile.gettempdir(), f"lsf_tb_check_{os.getpid()}.db")
                shutil.copy2(db_path, tmp_db)
                db_to_read = tmp_db
            except Exception:
                continue

        try:
            conn = sqlite3.connect(db_to_read)
            cursor = conn.cursor()

            # 查询所有匹配域名的 cookie
            for pattern in domain_patterns:
                cursor.execute(
                    "SELECT name, expires_utc FROM cookies WHERE host_key LIKE ?",
                    (pattern,)
                )
                # 过滤掉已过期的 cookie
                valid_names = {
                    row[0] for row in cursor.fetchall()
                    if row[1] == 0 or row[1] > now_chrome_us
                }
                if valid_names & login_cookie_names:  # 有交集
                    conn.close()
                    if db_to_read != db_path and os.path.exists(db_to_read):
                        try: os.remove(db_to_read)
                        except Exception: pass
                    return "logged_in"
            conn.close()
        except Exception:
            pass
        finally:
            if db_to_read != db_path and os.path.exists(db_to_read):
                try: os.remove(db_to_read)
                except Exception: pass

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

    # v8.4.13: 开发环境直接用 vendor\chrome-win64（Chrome for Testing 143）
    if not getattr(sys, 'frozen', False):
        vendor_path = os.path.join(exe_dir, "vendor", "chrome-win64", "chrome.exe")
        if os.path.isfile(vendor_path):
            return os.path.dirname(vendor_path)

    # 路径2: PyInstaller 临时目录（首次运行，datas 从 _MEIPASS 解压）
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        meipass_path = os.path.join(sys._MEIPASS, "embedded_chromium", "chrome.exe")
        if os.path.isfile(meipass_path):
            return os.path.dirname(meipass_path)

    # 路径3: 已释放到缓存目录（v8.3.7：EXE 同目录优先）
    # v8.4.13: 校验版本标记——旧版 chromium-1208 无标记，返回 None 触发重新释放
    cached_dir = _get_app_cache_dir("embedded_chromium")
    cached_path = os.path.join(cached_dir, "chrome.exe")
    if os.path.isfile(cached_path):
        version_file = os.path.join(cached_dir, "_browser_version.txt")
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                if f.read().strip() == _EMBEDDED_BROWSER_VERSION:
                    return os.path.dirname(cached_path)
        except Exception:
            pass  # 无版本文件/读取失败 → 视为旧版，走重新释放
        return None

    return None


# v8.4.13: 内嵌浏览器版本标记——Chrome for Testing 143（带 H.264/AAC/HEVC 编解码）
# 老用户机器上已释放的旧 chromium-1208 无版本标记 → 强制重新释放升级
_EMBEDDED_BROWSER_VERSION = "cft-143.0.7499.192"


def _extract_embedded_chromium():
    """从 PyInstaller _MEIPASS 释放浏览器到缓存目录（v8.3.7：EXE 同目录优先）

    v8.4.13: 增加版本标记校验——已释放目录若无版本文件或版本不匹配
    （如旧版 chromium-1208），先清空再重新释放 Chrome for Testing 143。

    返回释放后的浏览器目录路径，失败返回 None。
    """
    if not getattr(sys, 'frozen', False) or not hasattr(sys, '_MEIPASS'):
        return None

    src_dir = os.path.join(sys._MEIPASS, "embedded_chromium")
    if not os.path.isdir(src_dir):
        return None

    dst_dir = _get_app_cache_dir("embedded_chromium")
    version_file = os.path.join(dst_dir, "_browser_version.txt")

    # 已存在且版本匹配则不重复释放
    if os.path.isfile(os.path.join(dst_dir, "chrome.exe")) and os.path.isfile(version_file):
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                if f.read().strip() == _EMBEDDED_BROWSER_VERSION:
                    return dst_dir
        except Exception:
            pass
        # 版本不匹配 → 清空旧目录重新释放
        print("[浏览器] 检测到旧版本内嵌浏览器，正在升级到 Chrome for Testing 143...")
        try:
            shutil.rmtree(dst_dir, ignore_errors=True)
        except Exception:
            pass

    print(f"[浏览器] 首次运行/升级，正在释放嵌入式浏览器到本地（约 500MB）→ {dst_dir}")
    try:
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        # 写入版本标记
        try:
            with open(version_file, "w", encoding="utf-8") as f:
                f.write(_EMBEDDED_BROWSER_VERSION)
        except Exception:
            pass
        print(f"[浏览器] 释放完成: {dst_dir}")
        return dst_dir
    except Exception as e:
        print(f"[浏览器] 释放失败: {e}")
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


def _force_unlock_chromium_dir(user_data_dir):
    """v8.0.2 强制解锁 Chromium user_data_dir（防 about:blank）。

    当内置浏览器已经打开时，再启动 Playwright persistent_context 会因
    SingletonLock 被占用而失败，导致新 chromium 卡在 about:blank。

    本函数：
    1. 用 PowerShell 查找占用 user_data_dir 的 chrome.exe 进程并 taskkill
    2. 等 1.5 秒让进程退出
    3. 删除 SingletonLock / SingletonCookie / SingletonSocket 等锁文件
    """
    import subprocess
    import time

    if not user_data_dir:
        return

    abs_dir = os.path.abspath(user_data_dir)
    ps_path = abs_dir.replace("\\", "\\\\")

    try:
        ps_script = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "Where-Object { $_.CommandLine -and ($_.CommandLine -like '*" + ps_path + "*') } | "
            "ForEach-Object { $_.ProcessId }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        killed = 0
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", line],
                        capture_output=True, timeout=5,
                    )
                    killed += 1
                except Exception:
                    pass
        if killed:
            print("[ChromiumUnlock] 关闭了 {} 个占用 {} 的 chrome.exe".format(killed, os.path.basename(user_data_dir)))
            time.sleep(1.5)
    except Exception:
        pass

    for lock_name in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
        for sub in ["", os.path.join("Default", "")]:
            lock_path = os.path.join(user_data_dir, sub, lock_name) if sub else os.path.join(user_data_dir, lock_name)
            if os.path.isfile(lock_path):
                try:
                    os.remove(lock_path)
                except Exception:
                    pass


# 快手"请求过快"风控指数退避表（秒）
# 短退避失败后会逐渐拉长等待时间，避免无效刷新触发更严的封禁
_KS_RATE_LIMIT_BACKOFF = [8, 15, 25, 35, 45]
# 最大自动重试次数（避免一直刷新导致用户等待过久）
_KS_RATE_LIMIT_MAX_RETRIES = 5


def _ks_detect_rate_limit(page, result_data):
    """综合检测快手"请求过快"风控

    改进点（v8.x）：
    - 原来只检测 SSR state 里的 errorType.type，截图中的页面错误只在 DOM 中显示
    - 现在综合三种来源：SSR state、页面 DOM 文本、网络响应中的 errorType

    Returns:
        None: 未检测到风控
        tuple(source, err_type, detail): 检测到风控
            source: 'state' / 'dom' / 'network'
            err_type: 错误类型编号（state/network）或 DOM 关键词
            detail: 额外描述
    """
    # 方式1：检查 SSR state 里的 errorType
    try:
        state = page.evaluate("""() => {
            if (window.__INITIAL_STATE__) return window.__INITIAL_STATE__;
            return null;
        }""")
        if state:
            playlist = state.get("liveroom", {}).get("playList", [])
            if playlist:
                err = playlist[0].get("errorType") or {}
                if err.get("type"):
                    return ("state", err.get("type"), err.get("text") or err.get("message") or "")
    except Exception:
        pass

    # 方式2：检查页面 DOM 中的"请求过快"等关键词（截图中最常见的情况）
    # 错误只在客户端 hydrate 后通过 API 返回并渲染到 DOM，但 __INITIAL_STATE__ 仍是旧值
    try:
        dom_keyword = page.evaluate("""() => {
            const body = (document.body && document.body.innerText) || '';
            // 快手常见的风控文案关键词
            const keywords = ['请求过快', '请稍后重试', '操作过于频繁', '访问频率', '刷新重试', '网络异常'];
            for (const k of keywords) {
                if (body.indexOf(k) !== -1) return k;
            }
            return '';
        }""")
        if dom_keyword:
            return ("dom", "", dom_keyword)
    except Exception:
        pass

    # 方式3：检查已经拦截到的网络响应里是否带 errorType
    try:
        for _resp_url, data in (result_data or {}).items():
            if not isinstance(data, dict):
                continue
            err = data.get("errorType") or {}
            if err.get("type"):
                return ("network", err.get("type"), _resp_url[:120])
            # 兜底：响应里直接有 result/errno 但没有 liveStream
            result_code = data.get("result")
            if result_code not in (None, 1, "1"):
                # 同时没有 playUrls 才算真正的风控（避免误判普通业务错误）
                ls = data.get("liveStream") or (data.get("data") or {}).get("liveStream")
                if not (ls and isinstance(ls, dict) and ls.get("playUrls")):
                    return ("network", result_code, _resp_url[:120])
    except Exception:
        pass

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
                "--no-sandbox",  # v8.2.4 必须带，沙箱缺失会导致 Windows 上启动失败
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation"],
                "chromium_sandbox": False,  # v8.2.4 禁用 chromium 沙箱（Windows 上默认沙箱可能导致启动失败）
                "no_viewport": False,
            }

            # ── 启动浏览器（优先嵌入式 Chromium → Playwright Chromium → Chrome → Edge）──
            launch_errors = []
            context = None

            # 方式1: 嵌入式 Chromium（打包在 EXE 中，首次运行释放到 AppData）
            embedded_chromium = _ensure_chromium_ready()
            # v8.0.2 关闭旧 chromium 进程，避免 about:blank
            _force_unlock_chromium_dir(user_data_dir)
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
            rate_limit_retry_count = 0  # "请求过快"风控自动重试次数

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
                page.goto(KS_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
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
                    # ── 综合检测"请求过快"风控（DOM / SSR / 网络响应三个来源）──
                    rate_limit_info = _ks_detect_rate_limit(page, result_data)
                    if rate_limit_info:
                        source, err_type, detail = rate_limit_info
                        rate_limit_retry_count += 1
                        if rate_limit_retry_count > _KS_RATE_LIMIT_MAX_RETRIES:
                            print(
                                f"[Playwright] '请求过快'风控已重试 {_KS_RATE_LIMIT_MAX_RETRIES} 次仍失败，"
                                f"放弃自动重试（来源={source}，type={err_type}）"
                            )
                            # 跳出当前内层逻辑，让外层主循环自然结束
                            break

                        # 指数退避：8, 15, 25, 35, 45 秒 + 抖动
                        backoff_idx = min(rate_limit_retry_count - 1, len(_KS_RATE_LIMIT_BACKOFF) - 1)
                        wait_sec = _KS_RATE_LIMIT_BACKOFF[backoff_idx] + random.randint(0, 3)
                        print(
                            f"[Playwright] 检测到风控 [来源={source}, type={err_type}, 详情={detail}]"
                            f"，第 {rate_limit_retry_count}/{_KS_RATE_LIMIT_MAX_RETRIES} 次重试，"
                            f"等待 {wait_sec} 秒后刷新..."
                        )
                        page.wait_for_timeout(wait_sec * 1000)
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        result_data.clear()
                        prev_url["value"] = page.url

                        # 刷新后立即检测一次，若还在风控就提前进入下一轮（不再空等 4 轮）
                        for _j in range(3):
                            page.wait_for_timeout(5000)
                            # 先看有没有数据
                            found_stream = False
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
                            # 再确认是否还在风控
                            if _ks_detect_rate_limit(page, result_data):
                                # 仍处于风控，跳出这一段回到主循环（主循环会再次退避+刷新）
                                print("[Playwright] 刷新后仍处于风控状态，继续等待下一轮...")
                                break
                        continue  # 回到主循环下一轮

                    # ── 正常路径：从 SSR state 提取直播流 ──
                    state = page.evaluate("""() => {
                        if (window.__INITIAL_STATE__) return window.__INITIAL_STATE__;
                        return null;
                    }""")
                    if state:
                        playlist = state.get("liveroom", {}).get("playList", [])
                        if playlist:
                            item = playlist[0]
                            ls = item.get("liveStream", {})

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
    """获取抖音浏览器持久化缓存目录（cookie / session / localStorage）

    v8.3.7: 优先放 EXE 同目录 cache/LiveStreamFetcher/douyin_browser_data/
    不可写时回退 %APPDATA%/LiveStreamFetcher/douyin_browser_data/
    v8.4.12: 统一到 shared_browser_data（原因见 _get_ks_browser_data_dir）。
    """
    return _get_app_cache_dir("shared_browser_data")


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
    import shutil
    import tempfile
    import time

    # Chrome Web Kit 时间戳是 1601-01-01 起的微秒数
    CHROME_EPOCH_OFFSET = 11644473600000000
    now_chrome_us = int(time.time() * 1000000) + CHROME_EPOCH_OFFSET

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

        # DB 锁定时复制到临时文件再读
        db_to_read = db_path
        try:
            conn = sqlite3.connect(db_path, timeout=2)
            conn.execute("PRAGMA quick_check")
            conn.close()
        except Exception:
            try:
                tmp_db = os.path.join(tempfile.gettempdir(), f"lsf_dy_check_{os.getpid()}.db")
                shutil.copy2(db_path, tmp_db)
                db_to_read = tmp_db
            except Exception:
                continue

        try:
            conn = sqlite3.connect(db_to_read)
            cursor = conn.cursor()
            # 方案1：精确匹配 .douyin.com 域名 + 关键认证 cookie 名称
            for name in dy_auth_cookie_names:
                cursor.execute(
                    "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%.douyin.com' AND name=? AND (expires_utc = 0 OR expires_utc > ?)",
                    (name, now_chrome_us),
                )
                if cursor.fetchone()[0] > 0:
                    conn.close()
                    if db_to_read != db_path and os.path.exists(db_to_read):
                        try: os.remove(db_to_read)
                        except Exception: pass
                    return "logged_in"
            # 方案2：兜底——至少有 .douyin.com 域名的 sessionid 类 cookie
            cursor.execute(
                "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%.douyin.com' "
                "AND (name LIKE 'session%' OR name='sid_guard' OR name LIKE 'uid_tt%' OR name LIKE 'odin_%')"
                " AND (expires_utc = 0 OR expires_utc > ?)",
                (now_chrome_us,)
            )
            count = cursor.fetchone()[0]
            conn.close()
            if count > 0:
                if db_to_read != db_path and os.path.exists(db_to_read):
                    try: os.remove(db_to_read)
                    except Exception: pass
                return "logged_in"
        except Exception:
            pass
        finally:
            if db_to_read != db_path and os.path.exists(db_to_read):
                try: os.remove(db_to_read)
                except Exception: pass

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
                "--no-sandbox",  # v8.2.4 必须带，沙箱缺失会导致 Windows 上启动失败
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
                    "Chrome/143.0.0.0 Safari/537.36"
                ),
                "ignore_default_args": ["--enable-automation"],
                "chromium_sandbox": False,  # v8.2.4 禁用 chromium 沙箱（Windows 上默认沙箱可能导致启动失败）
                "no_viewport": False,
            }

            # ── 启动浏览器（优先嵌入式 Chromium → Playwright Chromium → Chrome → Edge）──
            launch_errors = []
            context = None

            embedded_chromium = _ensure_chromium_ready()
            # v8.0.2 关闭旧 chromium 进程，避免 about:blank
            _force_unlock_chromium_dir(user_data_dir)
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

            # v8.2.5 修复：new_page() 失败时降级到 API 方式
            try:
                page = context.pages[0] if context.pages else context.new_page()
            except Exception as e_newpage:
                print(f"[抖音Playwright] new_page() 失败: {e_newpage}（chromium 可能未正常启动）")
                context.close()
                return None

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
    """获取小红书浏览器持久化缓存目录（cookie / session / localStorage）

    v8.3.7: 优先 EXE 同目录 cache/LiveStreamFetcher/xiaohongshu_browser_data/
    v8.4.12: 统一到 shared_browser_data（原因见 _get_ks_browser_data_dir）。
    """
    return _get_app_cache_dir("shared_browser_data")


def _check_xhs_login_status():
    """检测小红书浏览器持久化目录中是否存在有效的登录 Cookie。

    通过读取 SQLite 数据库（处理 DB 锁定）检查关键登录 cookie。
    返回:
        "logged_in"  - 检测到有效登录 Cookie
        "expired"    - 目录存在但无有效 Cookie（可能过期）
        "never"      - 目录不存在，从未登录过
    """
    import sqlite3
    import shutil
    import tempfile
    import time

    # Chrome Web Kit 时间戳是 1601-01-01 起的微秒数
    CHROME_EPOCH_OFFSET = 11644473600000000
    now_chrome_us = int(time.time() * 1000000) + CHROME_EPOCH_OFFSET

    data_dir = _get_xhs_browser_data_dir()
    if not os.path.exists(data_dir):
        return "never"

    # 关键登录 cookie 名称
    login_cookie_names = {"web_session", "webId", "a1", "gid", "xsecappid"}

    cookie_paths = [
        os.path.join(data_dir, "Default", "Cookies"),
        os.path.join(data_dir, "Default", "Network", "Cookies"),
    ]

    for db_path in cookie_paths:
        if not os.path.isfile(db_path):
            continue

        # DB 锁定时复制到临时文件再读
        db_to_read = db_path
        try:
            conn = sqlite3.connect(db_path, timeout=2)
            conn.execute("PRAGMA quick_check")
            conn.close()
        except Exception:
            try:
                tmp_db = os.path.join(tempfile.gettempdir(), f"lsf_xhs_check_{os.getpid()}.db")
                shutil.copy2(db_path, tmp_db)
                db_to_read = tmp_db
            except Exception:
                continue

        try:
            conn = sqlite3.connect(db_to_read)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name, expires_utc FROM cookies WHERE host_key LIKE '%xiaohongshu%'"
            )
            # 过滤掉已过期的 cookie
            valid_names = {
                row[0] for row in cursor.fetchall()
                if row[1] == 0 or row[1] > now_chrome_us
            }
            conn.close()
            if valid_names & login_cookie_names:
                if db_to_read != db_path and os.path.exists(db_to_read):
                    try: os.remove(db_to_read)
                    except Exception: pass
                return "logged_in"
        except Exception:
            pass
        finally:
            if db_to_read != db_path and os.path.exists(db_to_read):
                try: os.remove(db_to_read)
                except Exception: pass

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
                "--no-sandbox",  # v8.2.4 必须带，沙箱缺失会导致 Windows 上启动失败
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation"],
                "chromium_sandbox": False,  # v8.2.4 禁用 chromium 沙箱（Windows 上默认沙箱可能导致启动失败）
                "no_viewport": False,
            }

            launch_errors = []
            context = None

            embedded_chromium = _ensure_chromium_ready()
            # v8.0.2 关闭旧 chromium 进程，避免 about:blank
            _force_unlock_chromium_dir(user_data_dir)
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
                page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
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
    """获取淘宝浏览器持久化缓存目录

    v8.3.7: 优先 EXE 同目录 cache/LiveStreamFetcher/taobao_browser_data/
    v8.4.12: 统一到 shared_browser_data（原因见 _get_ks_browser_data_dir）。
    """
    return _get_app_cache_dir("shared_browser_data")


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
                "--no-sandbox",  # v8.2.4 必须带，沙箱缺失会导致 Windows 上启动失败
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation"],
                "chromium_sandbox": False,  # v8.2.4 禁用 chromium 沙箱（Windows 上默认沙箱可能导致启动失败）
                "no_viewport": False,
            }

            launch_errors = []
            context = None

            embedded_chromium = _ensure_chromium_ready()
            # v8.0.2 关闭旧 chromium 进程，避免 about:blank
            _force_unlock_chromium_dir(user_data_dir)
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
                page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
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
    """获取YY浏览器持久化缓存目录

    v8.3.7: 优先 EXE 同目录 cache/LiveStreamFetcher/yy_browser_data/
    v8.4.12: 统一到 shared_browser_data（原因见 _get_ks_browser_data_dir）。
    """
    return _get_app_cache_dir("shared_browser_data")


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
                "--no-sandbox",  # v8.2.4 必须带，沙箱缺失会导致 Windows 上启动失败
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation"],
                "chromium_sandbox": False,  # v8.2.4 禁用 chromium 沙箱（Windows 上默认沙箱可能导致启动失败）
                "no_viewport": False,
            }

            launch_errors = []
            context = None

            embedded_chromium = _ensure_chromium_ready()
            # v8.0.2 关闭旧 chromium 进程，避免 about:blank
            _force_unlock_chromium_dir(user_data_dir)
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
# 累计访问次数持久化（v8.3.1）
# ═══════════════════════════════════════════════════════
# 之前硬编码 105432，每次启动都显示假数字。现在写到
# %APPDATA%/LiveStreamFetcher/visit_count.json 持久化计数，
# 跨版本累计，启动时 +1。
def _get_visit_count_path() -> str:
    """v8.3.7: 优先 EXE 同目录 cache/LiveStreamFetcher/visit_count.json
    不可写时回退到 %APPDATA%/LiveStreamFetcher/visit_count.json"""
    return _get_app_cache_dir("visit_count.json")


def _load_visit_count() -> int:
    """读取累计访问次数，文件不存在/格式错返回 0。"""
    import json as _json
    p = _get_visit_count_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = _json.load(f)
            return int(data.get("count", 0))
    except Exception:
        return 0


def _save_visit_count(n: int) -> None:
    import json as _json
    p = _get_visit_count_path()
    try:
        with open(p, "w", encoding="utf-8") as f:
            _json.dump({"count": n}, f, ensure_ascii=False)
    except Exception:
        pass


# v8.4.7: widgets.link 访客计数器（启动时静默访问一次，服务端会 +1 计数）
_WIDGET_VISITOR_URL = (
    "https://www.widgets.link/#/view/visitor-01"
    "?id=5144d796-d7bb-46f9-80cc-e2ba469d6013"
)


def bump_visit_count_on_startup() -> int:
    """启动时调用：本地计数 +1，并静默触发 widgets.link 访客统计。"""
    n = _load_visit_count() + 1
    _save_visit_count(n)

    # 静默 ping widgets.link 访客统计（网络错误不影响启动）
    try:
        import urllib.request
        req = urllib.request.Request(
            _WIDGET_VISITOR_URL,
            headers={"User-Agent": "LiveStreamFetcher/8.4.7"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # 网络错误/超时/SSL 等都不影响本地启动

    return n


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


# ═══════════════════════════════════════════════════════
# Playwright 上下文生命周期管理
# ═══════════════════════════════════════════════════════
# _open_platform_in_chromium 启动的 persistent_context 都注册到这里，
# EXE 退出时统一关闭，确保 Chromium 进程释放 _MEIPASS 临时目录的文件锁。

# 长生命周期 Playwright runner 列表：(p, context) 对
# 之前用 with sync_playwright() as p 上下文管理器，函数返回时 p 自动 stop()
# 会强制关闭所有 persistent_context 浏览器（也就是用户看到的"闪退"）。
# 正确做法：手动 start()，让 driver 与浏览器一起驻留，EXE 退出时再统一 stop。
_PLATFORM_BROWSER_RUNNERS = []

# v8.3.5: 平台 key → (p, context) 的 session 表
# 用于复用同一个浏览器窗口：再次点击同一平台 tab 时直接 new_page() 新开 tab，
# 不再启动新浏览器窗口、不再 taskkill 用户的现有窗口。
# v8.3.7: 改为 (p, browser, chrome_proc) 三元组，CDP 连接 + 自己启动的 chrome.exe
# v8.3.8: 删除多 session 模式 → 所有平台共用一个 chrome.exe + 单 CDP 端口（9222）
#           一个 user-data-dir，cookie 按 URL 域名自动隔离（不同平台不同域）
#           所有 tab 在同一个浏览器窗口（用户期望体验）
_PLATFORM_BROWSER_SESSIONS = {}   # 保留占位但不再使用（兼容旧调用）

# v8.3.8: 单浏览器进程单例
# v8.4.12: 不再直接持有 p/context —— 由 _SHARED_PW worker 线程独占持有。
#   保留此全局变量仅作"session 是否活跃"的标志位（True/False），
#   兼容 extract_streams 等旧代码的 `is not None` 判断。
_SHARED_BROWSER_SESSION = None   # (p, context, None, 0) —— v8.4.12 起仅作活跃标志


# v8.3.7: 给每个平台分配固定 CDP 调试端口（v8.3.8 不再使用，保留兼容）
# v8.3.8: 单 chrome.exe + 单端口 9222（所有平台共用）
_CDP_PORT_SHARED = 9222


# ═══════════════════════════════════════════════════════
# v8.4.12: Playwright 单线程 worker —— 解决 sync API 非线程安全
#
# 根因：Playwright sync API 的所有对象（p/context/page）绑定到创建它们的
# 线程（greenlet）。v8.4.11 之前在 daemon thread A 里 start()，又在
# daemon thread B 里复用同一个 context.new_page() —— 跨线程使用导致
# "Target page, context or browser has been closed" + about:blank 累积。
#
# 方案：唯一的长期 worker 线程独占持有 p/context，其它线程通过队列发命令
# （open / close / shutdown），worker 串行执行。所有 Playwright 调用
# 都发生在同一线程，彻底满足 sync API 线程约束。
# ═══════════════════════════════════════════════════════
import queue as _queue

_SHARED_PW = {
    "thread": None,     # worker 线程对象
    "queue": None,      # _queue.Queue，命令队列
    "lock": threading.Lock(),
}


def _ensure_shared_pw_worker() -> _queue.Queue:
    """确保共享浏览器 worker 线程已启动，返回命令队列。"""
    with _SHARED_PW["lock"]:
        t = _SHARED_PW["thread"]
        if t is not None and t.is_alive() and _SHARED_PW["queue"] is not None:
            return _SHARED_PW["queue"]
        q = _queue.Queue()
        _SHARED_PW["queue"] = q
        t = threading.Thread(
            target=_shared_pw_worker_loop, args=(q,),
            daemon=True, name="SharedPWWorker",
        )
        _SHARED_PW["thread"] = t
        t.start()
        return q


def _shared_pw_worker_loop(q: _queue.Queue) -> None:
    """共享浏览器 Playwright owner 线程：所有 p/context 操作只在此线程执行。

    命令协议：q.put((cmd, payload, result_dict, done_event))
      - "open":     payload=(target_url, data_dir, chrome_exe_path)
                    启动（或复用）浏览器并导航到 target_url
      - "close":    关闭 context + 停止 driver（释放 SingletonLock）
      - "shutdown": 同 close，但执行后退出线程
    """
    p = None
    context = None

    def _teardown():
        nonlocal p, context
        try:
            if context is not None:
                context.close()
                # v8.4.13: context.close() 返回时 chrome 进程可能仍在异步
                # flush Cookies SQLite 到磁盘，紧接 p.stop() 硬杀 driver 会
                # 打断 flush → 用户"总是掉登录"。给 chrome 退出留时间。
                import time as _t
                _t.sleep(1.0)
        except Exception:
            pass
        try:
            if p is not None:
                p.stop()
        except Exception:
            pass
        p = None
        context = None

    def _launch_fresh(target_url, data_dir, chrome_exe_path):
        """全新启动浏览器并导航（在 worker 线程内调用）。"""
        nonlocal p, context
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        context = p.chromium.launch_persistent_context(
            user_data_dir=data_dir,
            headless=False,
            executable_path=chrome_exe_path,
            viewport={"width": 1280, "height": 800},
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ],
            ignore_default_args=["--no-sandbox"],
            timeout=30000,
        )
        # 首次启动：复用初始 New Tab Page，避免双 tab
        pages = context.pages
        if pages:
            initial = pages[0]
            try:
                initial.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            try:
                initial.goto(target_url, wait_until="domcontentloaded", timeout=20000)
            except Exception:
                try:
                    initial.goto(target_url, wait_until="commit", timeout=15000)
                except Exception:
                    pass

    while True:
        try:
            item = q.get()
        except Exception:
            break
        if item is None:  # poison pill
            break
        cmd, payload, result, ev = item
        try:
            if cmd == "open":
                target_url, data_dir, chrome_exe_path = payload
                # v8.4.13: 健康探测改为**直接试 new_page**（探测与创建合一）。
                # v8.4.12 用 `_ = context.pages` 探测——用户手动关闭浏览器窗口后，
                # context.pages 返回的是 Playwright 端缓存的列表，不抛异常，
                # 导致"手动关窗后第一次打开失败，第二次才成功"。
                page = None
                if context is not None:
                    try:
                        page = context.new_page()
                    except Exception:
                        # chrome 已被用户手动关闭 → 重启浏览器
                        _teardown()
                if context is None:
                    _launch_fresh(target_url, data_dir, chrome_exe_path)
                    result["ok"] = True
                    result["error"] = ""
                    ev.set()
                    continue
                # 复用：清理 about:blank 残留 page（goto 失败时 Chrome 端 tab 残留）
                try:
                    for old_page in list(context.pages):
                        try:
                            if old_page is page:
                                continue
                            url = old_page.url if not old_page.is_closed() else ""
                            if url and url in ("about:blank", "chrome://newtab/", "chrome://newtab"):
                                try:
                                    old_page.close()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass
                # 新 tab 导航
                page.set_default_navigation_timeout(20000)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass  # about:blank 加载快，超时无碍
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e1:
                    try:
                        page.goto(target_url, wait_until="commit", timeout=15000)
                    except Exception as e2:
                        try:
                            if not page.is_closed():
                                page.close()
                        except Exception:
                            pass
                        raise RuntimeError(
                            f"goto 失败: {str(e1)[:60]} / {str(e2)[:60]}"
                        )
                result["ok"] = True
                result["error"] = ""
            elif cmd in ("close", "shutdown"):
                _teardown()
                result["ok"] = True
                result["error"] = ""
        except Exception as e:
            # open 失败时 teardown，避免残留半死的 context
            if cmd == "open":
                _teardown()
            result["ok"] = False
            result["error"] = str(e)[:200]
        ev.set()
        if cmd == "shutdown":
            break
    # 线程退出前兜底清理
    _teardown()


def _shared_pw_command(cmd: str, payload=None, timeout: float = 40.0) -> tuple:
    """向共享浏览器 worker 发送命令并同步等待结果。

    Returns:
        (ok: bool, error_msg: str)
    """
    q = _ensure_shared_pw_worker()
    result = {"ok": False, "error": "未执行"}
    ev = threading.Event()
    q.put((cmd, payload, result, ev))
    if ev.wait(timeout=timeout):
        return result["ok"], result["error"]
    return False, f"worker 命令超时: {cmd}"


def _count_chrome_by_profile(profile_key: str) -> int:
    """计数使用指定 profile 目录的 chrome.exe 进程数（按 CommandLine 过滤）。

    用 PowerShell Get-CimInstance 精确匹配，不会误伤用户自己的 Chrome。
    返回 -1 表示查询失败（保守处理）。
    """
    try:
        NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        ps_cmd = (
            "(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{profile_key}*' }}).Count"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=NO_WINDOW,
        )
        out = (r.stdout or "").strip()
        return int(out) if out.isdigit() else -1
    except Exception:
        return -1


def _wait_singleton_lock_released(data_dir: str, timeout: float = 8.0) -> bool:
    """轮询等待使用 data_dir 的 chrome 进程完全退出（cookie flush 到磁盘）。

    v8.4.12 的实现是"尝试删除 SingletonLock 文件"——严重缺陷：
    chrome 退出过程中锁文件可能提前变为可删，甚至被本函数主动删掉，
    导致新 chrome 在旧 chrome 还没 flush Cookies SQLite 到磁盘时就启动，
    两个进程写同一 profile → Cookies 数据库竞争损坏 → 用户"总是掉登录"。

    v8.4.13 改为等**进程真正退出**（PowerShell 按 CommandLine 过滤计数为 0），
    进程退出后才清理残留的锁文件（此时删除是安全的）。

    Returns:
        True = 进程已全部退出；False = 超时仍有残留
    """
    import time as _time
    deadline = _time.time() + timeout
    profile_key = os.path.basename(data_dir.rstrip("\\/"))  # "shared_browser_data"
    while _time.time() < deadline:
        cnt = _count_chrome_by_profile(profile_key)
        if cnt == 0:
            # 进程已全部退出，安全清理残留锁文件
            for lock_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
                lf = os.path.join(data_dir, lock_name)
                if os.path.exists(lf):
                    try:
                        os.remove(lf)
                    except OSError:
                        pass
            return True
        _time.sleep(0.4)
    return False


def _close_shared_browser_for_fetch(reason: str = "parse"):
    """v8.4.11: 解析流前关闭共享内置浏览器。

    业务层 fetch_xxx 会启动**新** chrome.exe 抓流，但 Chrome 同 user_data_dir
    同时只允许一个进程持有 SingletonLock——若内置浏览器还在跑，新 chrome 启动
    时会被 Chrome 自动 fallback 到临时空 Profile，丢失已登录的 cookie。

    解决方案：解析前主动关闭内置浏览器（释放 SingletonLock），让 fetch_xxx
    启动新 chrome 时能从 shared_browser_data 读到 cookie（已登录态）。

    v8.4.12:
      - 通过 worker 线程执行 close（满足 sync API 线程约束）
      - 关闭后轮询等待 SingletonLock 真正释放（最多 6 秒），不再固定 sleep 1.5s
      - 兜底精确清理 shared_browser_data  profile 的 chrome 进程（不误伤用户 Chrome）

    Args:
        reason: "parse"（解析）/ "fetch"（业务流）—— 用于日志
    """
    global _SHARED_BROWSER_SESSION
    if _SHARED_BROWSER_SESSION is None:
        return False
    # 1. 通过 worker 关闭 context + 停止 driver
    try:
        _shared_pw_command("close", timeout=15.0)
    except Exception as e:
        print(f"[close-shared] worker close 异常: {e}")
    _SHARED_BROWSER_SESSION = None
    # 2. 轮询等待 SingletonLock 文件锁真正释放
    data_dir = _get_app_cache_dir("shared_browser_data")
    released = _wait_singleton_lock_released(data_dir, timeout=6.0)
    if not released:
        # 3. 兜底：精确杀使用 shared_browser_data profile 的 chrome 进程
        #    （按 CommandLine 过滤，不误伤用户自己的 Chrome 浏览器）
        try:
            ps_cmd = (
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                "Where-Object { $_.CommandLine -like '*shared_browser_data*' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            )
            NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, timeout=15, creationflags=NO_WINDOW,
            )
            _wait_singleton_lock_released(data_dir, timeout=3.0)
        except Exception:
            pass
    print(f"[close-shared] reason={reason}, lock_released={released}")
    return True


# v8.3.7: 应用缓存目录统一管理（便携优先）
# 默认优先 EXE 同目录（便携模式），EXE 同目录不可写时回退到 %APPDATA%
# 彻底避免在系统盘/AppData 留下缓存文件
_APP_CACHE_DIR_LOCK = threading.Lock()
_APP_CACHE_DIR_BASE = None


def _get_app_cache_dir(subdir: str = "") -> str:
    """获取应用缓存子目录（v8.3.7：便携优先）。

    优先级：
    1. EXE 同目录下的 cache/LiveStreamFetcher/{subdir}（便携模式，跨电脑友好）
    2. EXE 同目录不可写 → 回退 %APPDATA%/LiveStreamFetcher/{subdir}

    Args:
        subdir: 子目录名（如 "embedded_chromium", "wechat_video_tool", "visit_count.json"）

    Returns:
        完整路径（已确保目录存在）
    """
    global _APP_CACHE_DIR_BASE

    # 找 EXE 同目录
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = os.path.dirname(os.path.abspath(__file__))

    # 1. 优先 EXE 同目录（便携）
    with _APP_CACHE_DIR_LOCK:
        if _APP_CACHE_DIR_BASE is None:
            portable_base = os.path.join(exe_dir, "cache", "LiveStreamFetcher")
            try:
                os.makedirs(portable_base, exist_ok=True)
                test_file = os.path.join(portable_base, ".write_test")
                with open(test_file, 'w', encoding='utf-8') as f:
                    f.write('test')
                os.remove(test_file)
                _APP_CACHE_DIR_BASE = portable_base
            except Exception:
                # 2. 回退 %APPDATA%（EXE 同目录不可写时）
                _APP_CACHE_DIR_BASE = os.path.join(os.environ.get("APPDATA", ""), "LiveStreamFetcher")

    target = os.path.join(_APP_CACHE_DIR_BASE, subdir) if subdir else _APP_CACHE_DIR_BASE
    try:
        os.makedirs(target, exist_ok=True)
    except Exception:
        pass
    return target


# v8.4.8: 激进 taskkill 清理 Chromium 子进程（多次轮询直到 0 进程）
def _aggressive_kill_chrome(max_rounds: int = 3) -> None:
    """多次轮询 taskkill /IM chrome.exe + node.exe（playwright driver），
    直到 tasklist 报告 0 个 chrome.exe / playwright 相关 node.exe 残留。

    atexit 阶段：PyInstaller 即将清理 _MEIPASS 临时目录，需要所有
    子进程完全退出，文件锁才会释放：
    - chrome.exe（主/渲染/GPU/utility）：通过 executable_path 指向 EXE 同目录
      的 embedded_chromium，**不在 _MEI 里**，但会加载 playwright 注入脚本
    - node.exe（Playwright driver）：在 _MEI/playwright/driver/package/ 里运行，
      **直接持有 _MEI 临时目录文件锁**——这是"Failed to remove temporary
      directory"警告的真正元凶（v8.4.9 才定位到）
    """
    import time as _time
    if sys.platform != "win32":
        return

    NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

    def _kill_playwright_node() -> None:
        """精确杀掉 playwright 相关的 node.exe（通过 CommandLine 含 playwright 筛选，
        避免误杀用户的其它 node 进程）。"""
        try:
            ps_cmd = (
                "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                "Where-Object { $_.CommandLine -like '*playwright*' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, timeout=15, creationflags=NO_WINDOW,
            )
        except Exception:
            pass

    def _count_chrome() -> int:
        """用 tasklist 计数残留 chrome.exe 进程数。"""
        try:
            r = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10, creationflags=NO_WINDOW,
            )
            out = (r.stdout or "").strip()
            if not out or "INFO: No tasks" in out:
                return 0
            lines = [ln for ln in out.splitlines() if ln.strip()]
            return len(lines)
        except Exception:
            return -1

    def _count_playwright_node() -> int:
        """计数 playwright driver 相关的 node.exe 进程数（按 CommandLine 过滤）。

        v8.4.12: node.exe 持有 _MEI 临时目录文件锁，是 "Failed to remove
        temporary directory" 的元凶。光杀不等没用——必须确认进程真正退出。
        """
        try:
            ps_cmd = (
                "(Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                "Where-Object { $_.CommandLine -like '*playwright*' }).Count"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=15, creationflags=NO_WINDOW,
            )
            out = (r.stdout or "").strip()
            return int(out) if out.isdigit() else -1
        except Exception:
            return -1

    # v8.4.9 关键：先杀 playwright driver（node.exe），再杀 chrome.exe
    _kill_playwright_node()

    for round_idx in range(max_rounds):
        # 1. 强制 taskkill chrome.exe + node.exe
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
                capture_output=True, timeout=10, creationflags=NO_WINDOW,
            )
        except Exception:
            pass
        # 每轮都补杀 playwright node（driver 可能重连）
        _kill_playwright_node()

        # 2. 立即计数（taskkill 后大部分已退出）
        after_kill = _count_chrome()
        if after_kill == 0:
            break

        # 3. 等待 1.5 秒再检测
        _time.sleep(1.5)
        after_wait = _count_chrome()
        if after_wait == 0:
            break

    # 4. 兜底：再 taskkill 一次 + 杀 playwright node
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
            capture_output=True, timeout=5, creationflags=NO_WINDOW,
        )
    except Exception:
        pass
    _kill_playwright_node()

    # 5. v8.4.12 关键：轮询等 playwright node.exe 真正退到 0（最多 ~10 秒）
    #    node.exe 在 _MEI/playwright/driver/package/ 运行，持有临时目录文件锁。
    #    必须确认它退出，PyInstaller bootloader 才能删掉 _MEI 目录。
    for _ in range(20):
        cnt = _count_playwright_node()
        if cnt == 0:
            break
        if cnt > 0:
            _kill_playwright_node()
        _time.sleep(0.5)


# v8.4.8: Playwright watcher 守护线程管理（防止 p.stop() 卡 atexit）
_PLAYWRIGHT_WATCHER = {"p_list": [], "timer": None}


def _register_playwright_watcher(p) -> None:
    """注册一个 watcher 在心跳丢失时主动 reload/context close，防止 Playwright
    内部连接线程异常僵死导致 p.stop() 阻塞。
    """
    _PLAYWRIGHT_WATCHER["p_list"].append(p)


def _disable_playwright_watcher() -> None:
    """清理 watcher。atexit 阶段禁用避免 join 自身卡。"""
    _PLAYWRIGHT_WATCHER["p_list"].clear()


def _cleanup_all_playwright_contexts():
    """EXE 退出时统一关闭所有 Playwright 浏览器 context（含 Chromium 子进程）。

    不调用的话：
    1. 用户关闭主 EXE 时，Chromium 子进程仍持有 PyInstaller
       onefile 临时目录 _MEIPASS/* 的文件句柄，导致退出时弹出
       "Failed to remove temporary directory" 警告。
    2. Playwright driver 进程残留，可能端口冲突下次启动。

    v8.3.6: 强制 taskkill chrome.exe 释放 _MEIPASS 临时目录锁
    v8.3.7: 优先用 session 里的 chrome_proc 句柄优雅 terminate，taskkill 兜底
    v8.3.8: 处理 _SHARED_BROWSER_SESSION 单例（共用 chrome.exe）
    v8.4.4: 改用 launch_persistent_context 后 chrome_proc 永远是 None → 不再依赖句柄
    v8.4.8: atexit 钩子升级——多次 taskkill 等待循环（最多 3 轮，每轮检测进程数，
             等 0 才退出），彻底杀尽所有 Chromium 子进程，确保 _MEIPASS 文件锁释放。
             同时清掉 watcher 守护线程，避免 join 自身卡 atexit。
    """
    # 0. 先取消 watcher 守护线程（如果存在）—— 避免 p.stop() 阻塞时 watcher 干扰
    try:
        from live_stream_fetcher import _disable_playwright_watcher
        _disable_playwright_watcher()
    except Exception:
        pass

    # 1. v8.4.12: 通过 worker 线程 shutdown 共享浏览器（sync API 线程约束：
    #    p/context 属于 worker 线程，主线程直接 close/stop 会跨线程报错）
    #    worker shutdown 内部会 context.close() + p.stop()（driver 优雅退出，
    #    node.exe 随之释放 _MEI 文件锁）
    global _SHARED_BROWSER_SESSION
    _worker_started = (
        _SHARED_PW["thread"] is not None and _SHARED_PW["thread"].is_alive()
    )
    if _worker_started:
        try:
            _shared_pw_command("shutdown", timeout=15.0)
        except Exception:
            pass
    _SHARED_BROWSER_SESSION = None

    # 2. 清空注册表（对象归 worker 线程所有，主线程不再触碰）
    _PLATFORM_BROWSER_RUNNERS.clear()
    _PLATFORM_BROWSER_SESSIONS.clear()

    # 3. v8.4.8 关键：激进 taskkill 循环杀 Chromium 子进程
    #    atexit 阶段 PyInstaller 即将清理 _MEI 临时目录，必须等所有
    #    Chromium 子进程完全释放文件锁。多次 taskkill + 等待直到稳定 0 进程。
    #    v8.4.12: 同时轮询等 playwright node.exe 退到 0（_MEI 锁的持有者）。
    if sys.platform == "win32":
        _aggressive_kill_chrome()


import atexit
atexit.register(_cleanup_all_playwright_contexts)


# ═══════════════════════════════════════════════════════
# 平台浏览器数据目录 + 登录态注册表（共用一套 data_dir，
# 点击平台 tab 启动登录浏览器、解析时启动解析浏览器，cookie 互通）
# ═══════════════════════════════════════════════════════

# 平台 key（Qt 版 PLATFORM_META 用） → (data_dir_func, login_check_func)
_PLATFORM_BROWSER_MAP = {
    "dy": (_get_dy_browser_data_dir, _check_dy_login_status),
    "ks": (_get_ks_browser_data_dir, _check_ks_login_status),
    "xhs": (_get_xhs_browser_data_dir, _check_xhs_login_status),
    "tb": (_get_tb_browser_data_dir, _check_tb_login_status),
    "yy": (_get_yy_browser_data_dir, None),
}


def _open_platform_in_chromium(platform_key: str, target_url: str,
                              wait_timeout: float = 30.0) -> tuple:
    """用对应平台的嵌入式 Chromium 浏览器打开 URL（与解析时的浏览器共享 cookie）。

    v8.4.4 根本性修复：放弃 subprocess + CDP 方案，改回 Playwright
    `launch_persistent_context`（v8.3.0 之前用）。原因：
    - subprocess + CDP 方案下，chrome 在 CDP 控制 + `--no-sandbox` flag 下渲染
      异常——用户点抖音页面里 `target="_blank"` 链接，chrome 创建的新 tab 卡在
      about:blank，且老页面 CSS 错位（搜索图标、抖音 logo 重叠）。
    - Playwright `launch_persistent_context` 让 chrome 用默认配置启动，无 CDP
      干扰，chrome 原生行为正常（点击链接开新 tab 加载）。

    v8.4.12 根本性修复：所有 Playwright 操作收敛到唯一 worker 线程执行。
    Playwright sync API 的对象绑定创建线程（greenlet），跨线程复用 context
    会抛 "Target page, context or browser has been closed" 并累积 about:blank
    残留 tab。worker 方案下 open/close 都经命令队列串行执行，线程安全。

    Returns:
        (ok: bool, error_msg: str)
    """
    global _SHARED_BROWSER_SESSION

    if platform_key not in _PLATFORM_BROWSER_MAP:
        return False, f"不支持的平台: {platform_key}"

    # chrome.exe 路径
    browser_path = _ensure_chromium_ready()
    if not browser_path:
        return False, "chromium 未找到，请确认内嵌 chromium 已打包"

    # 共享 data_dir（所有平台 cookie 都存在这里，Chrome 按 URL 域名隔离）
    data_dir = _get_app_cache_dir("shared_browser_data")

    # 仅在无活跃 session 时才需要强制解锁（有 session 说明锁被我们自己的
    # chrome 正常持有，不能删）
    if _SHARED_BROWSER_SESSION is None:
        _force_unlock_chromium_dir(data_dir)

    # 抑制 Playwright 子进程弹出 CMD 黑窗口（只 patch 一次，避免无限嵌套）
    if sys.platform == "win32" and not getattr(_open_platform_in_chromium, "_popen_patched", False):
        _orig_popen = subprocess.Popen
        def _no_console_popen(*args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            return _orig_popen(*args, **kwargs)
        subprocess.Popen = _no_console_popen
        _open_platform_in_chromium._popen_patched = True

    # 通过 worker 线程执行 open（launch 或复用 + 新 tab 导航）
    _chrome_exe_path = os.path.join(browser_path, "chrome.exe")
    ok, err = _shared_pw_command(
        "open",
        payload=(target_url, data_dir, _chrome_exe_path),
        timeout=wait_timeout + 15,
    )
    if ok:
        _SHARED_BROWSER_SESSION = True  # 活跃标志（实际对象在 worker 线程内）
        return True, ""
    _SHARED_BROWSER_SESSION = None
    return False, err or "启动失败"


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

    v8.4.11: 解析前自动关闭共享内置浏览器。
    Chrome 同 user_data_dir 同时只允许一个进程持有 SingletonLock——
    若内置浏览器还在跑，业务层 fetch_xxx 启动的新 chrome 会被 Chrome fallback
    到临时空 Profile，丢失已登录的 cookie。所以解析前主动关闭内置浏览器，
    释放 SingletonLock，让 fetch_xxx 启动新 chrome 时能读到 cookie（已登录态）。
    """
    platform = detect_platform(url)

    # v8.4.11: 解析前关闭共享内置浏览器（解决"已登录但解析还要登录"的问题）
    if _SHARED_BROWSER_SESSION is not None:
        try:
            print(f"[{platform}] 解析前关闭共享内置浏览器（释放 SingletonLock）...")
            _close_shared_browser_for_fetch(reason=f"parse-{platform}")
        except Exception as e:
            print(f"[{platform}] 关闭共享浏览器失败: {e}")

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
                # v8.4.12: 精简错误文案（之前 20 行长弹窗，用户无法快速读懂）
                _err_short = str(e).replace("\n", " ").strip()[:80]
                raise Exception(
                    f"{platform}解析失败\n\n"
                    f"原因：{_err_short}\n\n"
                    f"建议：\n"
                    f"  1) 确认主播正在直播\n"
                    f"  2) 点上方平台按钮登录后重试\n"
                    f"  3) 遇验证码请在浏览器中手动完成\n"
                    f"  4) 等 1-2 分钟后再试"
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
    """v7.6 主题色板（深色玻璃感 · 圆角仪表盘风格）

    参考截图「专业直播工具集」配色：
    - 主背景 深蓝黑 (#0a0e1a)
    - 侧边栏 深色 (#0d1117)
    - 卡片 深蓝灰 (#161b2e)
    - hero 渐变 青→蓝 (#00d4ff → #5b8def)
    - 强调色 紫(#8b5cf6) / 青(#00d4ff) / 绿(#10b981) / 橙(#f59e0b) / 粉(#ec4899)
    """
    # ── 基础背景 ──
    BG_DARK = "#0a0e1a"          # 主背景（深蓝黑）
    BG_SIDEBAR = "#0d1117"       # 侧边栏背景（更深）
    BG_CARD = "#161b2e"          # 卡片背景（深蓝灰）
    BG_CARD_LIGHT = "#1f2540"    # 卡片亮色（hover）
    BG_CARD_HOVER = "#252b45"    # 卡片 hover
    BG_INPUT = "#0f1422"         # 输入框背景
    BG_BORDER = "#2a3148"        # 卡片边框
    BG_HOVER = "#1f2540"          # 通用 hover 背景（v8.0.2 补充）

    # ── 边框 ──
    BORDER = "#2a3148"           # 默认边框（柔和）
    BORDER_LIGHT = "#3a4258"     # 浅边框
    BORDER_FOCUS = "#FFD700"     # 焦点边框（金色）

    # ── 文字 ──
    TEXT_PRIMARY = "#f0f4ff"     # 主文字
    TEXT_SECONDARY = "#9aa3bd"   # 次文字
    TEXT_MUTED = "#6a7390"       # 弱化文字
    TEXT_DARK = "#0a0e1a"        # 深色文字（用于金色背景）

    # ── 强调色 ──
    ACCENT_BLUE = "#58a6ff"
    ACCENT_GREEN = "#10b981"
    ACCENT_RED = "#ff6b6b"
    ACCENT_ORANGE = "#f59e0b"
    ACCENT_PURPLE = "#8b5cf6"
    ACCENT_CYAN = "#00d4ff"
    ACCENT_PINK = "#ec4899"

    # ── 金色系 ──
    GRADIENT_START = "#FFD700"
    GRADIENT_END = "#D4A017"
    GOLD_PRIMARY = "#FFD700"
    GOLD_LIGHT = "#FFE55C"
    GOLD_DARK = "#B8860B"
    GOLD_GLOW = "#FFE066"

    # ── Hero 渐变（青→蓝）──
    HERO_GRADIENT_FROM = "#00d4ff"
    HERO_GRADIENT_TO = "#5b8def"
    HERO_GLOW = "#39d2c0"

    # ── 平台品牌色 ──
    PLATFORM_DOUYIN = "#FE2C55"      # 抖音粉红
    PLATFORM_KUAISHOU = "#FF6A00"    # 快手橙
    PLATFORM_XHS = "#FF2442"         # 小红书红
    PLATFORM_TAOBAO = "#FF6A00"      # 淘宝橙
    PLATFORM_YY = "#FFD700"          # YY 金
    PLATFORM_WECHAT = "#07C160"      # 视频号绿

    # ── 状态色 ──
    STATUS_ONLINE = "#10b981"        # 在线/已登录（绿）
    STATUS_OFFLINE = "#6a7390"       # 离线/未登录（灰）
    STATUS_EXPIRED = "#f59e0b"       # 失效/警告（橙）
    STATUS_ERROR = "#ff6b6b"         # 错误（红）


# ─── 平台元数据（用于新 UI 网格 + 状态）───
PLATFORM_META = {
    "dy": {
        "name": "抖音直播", "short": "抖音", "icon": "🎵", "icon_file": "dy.png",
        "color": Colors.PLATFORM_DOUYIN,
        "desc": "支持 live.douyin.com / douyin.com/follow/live 多域名解析",
        "version": "v1.5", "size": "15.2 MB", "date": "2026-07-20",
        "url": "https://www.douyin.com/",
        "login_url": "https://www.douyin.com/",
        "login_func": "_check_dy_login_status",
    },
    "ks": {
        "name": "快手直播", "short": "快手", "icon": "📹", "icon_file": "ks.png",
        "color": Colors.PLATFORM_KUAISHOU,
        "desc": "支持 kuaishou.com 域名解析 · 需扫码登录",
        "version": "v1.3", "size": "12.8 MB", "date": "2026-07-20",
        "url": "https://live.kuaishou.com/",
        "login_url": "https://passport.kuaishou.com/pc/account/login",
        "login_func": "_check_ks_login_status",
    },
    "xhs": {
        "name": "小红书直播", "short": "小红书", "icon": "📕", "icon_file": "xhs.png",
        "color": Colors.PLATFORM_XHS,
        "desc": "支持 xhscdn.com 流地址 · HEVC 自动转码 H.264",
        "version": "v1.4", "size": "10.5 MB", "date": "2026-07-20",
        "url": "https://www.xiaohongshu.com/",
        "login_url": "https://www.xiaohongshu.com/explore",
        "login_func": "_check_xhs_login_status",
    },
    "tb": {
        "name": "淘宝直播", "short": "淘宝", "icon": "🛒", "icon_file": "tb.png",
        "color": Colors.PLATFORM_TAOBAO,
        "desc": "支持 tbzb.taobao.com / live.taobao.com · FLV 链自动代理",
        "version": "v1.2", "size": "9.6 MB", "date": "2026-07-20",
        "url": "https://tbzb.taobao.com/",
        "login_url": "https://login.taobao.com/member/login.jhtml",
        "login_func": "_check_tb_login_status",
    },
    "yy": {
        "name": "YY 直播", "short": "YY", "icon": "🎤", "icon_file": "yy.png",
        "color": Colors.PLATFORM_YY,
        "desc": "支持 www.yy.com / wap.yy.com 多端解析",
        "version": "v1.0", "size": "8.3 MB", "date": "2026-07-20",
        "url": "https://www.yy.com/",
        "login_url": None,
        "login_func": None,
    },
    "wechat": {
        "name": "视频号", "short": "视频号", "icon": "💬", "icon_file": "wechat.png",
        "color": Colors.PLATFORM_WECHAT,
        "desc": "支持 channels.weixin.qq.com 解析 · 配合本地工具下载",
        "version": "v2.6", "size": "168.1 MB", "date": "2026-07-20",
        "url": "https://channels.weixin.qq.com/",
        "login_url": None,
        "login_func": None,
    },
}


class RoundedFrame(tk.Frame):
    """v7.6.2 圆角容器（Canvas 画圆角填充 + Frame 自身作为 inner）。

    设计原则（最稳定版本）：
    - 继承 tk.Frame，propagate=True（让子 widget 撑大 Frame）
    - Frame 自身 bg = 父容器背景（让方角区域透明）
    - Canvas 画圆角填充（place relwidth=1 relheight=1）
    - **self.inner = self**（RoundedFrame 自身就是 inner）
    - 调用者既可 `rf.inner.pack(...)` 也可 `rf.pack(...)`，效果一样
    - 监听 <Configure> 自动重绘

    之前的 v7.6.0 用 `inner.place(relwidth=1, relheight=1)` 导致 propagate 死锁
    （relwidth 依赖 Frame 大小，Frame 大小依赖 inner 大小）。
    现在 self.inner = self，propagate 直接生效。
    """

    def __init__(self, parent, radius=14, fill=None, border=None, border_width=0,
                 **kwargs):
        if fill is None:
            fill = Colors.BG_CARD
        if border is None:
            border = Colors.BORDER_LIGHT

        # 父容器背景（用于 Canvas 方角区域透明）
        try:
            parent_bg = parent.cget("bg")
        except Exception:
            parent_bg = Colors.BG_DARK

        # Frame 自身用父容器 bg（让方角区域透明显示父容器）
        super().__init__(parent, bg=parent_bg, highlightthickness=0, bd=0, **kwargs)

        self._radius = radius
        self._fill_color = fill
        self._border_color = border
        self._border_width = border_width
        self._parent_bg = parent_bg

        # Canvas 画圆角填充（place 占满 Frame，propagate 不会影响它）
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=parent_bg)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)

        # self.inner = self（向后兼容）
        # 之前用单独 inner Frame 的方式会导致 propagate 死锁
        self.inner = self

        # 监听 Frame 大小变化
        self.bind("<Configure>", lambda e: self._redraw())

    def _redraw(self):
        try:
            self._canvas.delete("bg")
            self._canvas.delete("border")
        except Exception:
            return
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        r = min(self._radius, w // 2, h // 2)
        pts = [
            r, 0, w - r, 0, w, r, w, h - r,
            w - r, h, r, h, 0, h - r, 0, r,
        ]
        # 圆角填充
        self._canvas.create_polygon(
            pts, smooth=True, fill=self._fill_color, outline="",
            tags="bg",
        )
        # 边线
        if self._border_width > 0 and self._border_color:
            self._canvas.create_polygon(
                pts, smooth=True, fill="", outline=self._border_color,
                width=self._border_width, tags="border",
            )

    def configure(self, **kw):
        # v8.2.7 修复：处理 _PressButton3D 自定义参数，
        # 避免传给 tk.Frame.configure 触发 Tcl "unknown option" 错误
        if "text" in kw:
            self._set_text(kw.pop("text"))
        if "icon_text" in kw:
            new_icon_text = kw.pop("icon_text")
            if getattr(self, '_icon_id', None) is not None and not self._icon_path:
                try:
                    self._canvas.itemconfigure(self._icon_id, text=new_icon_text)
                    self._icon_text = new_icon_text
                except Exception:
                    pass
        if "bg" in kw:
            self._color_top = kw.pop("bg")  # v8.2.7: bg 实际就是 color_top
            try:
                self._canvas.itemconfigure("top", fill=self._color_top)
            except Exception:
                pass
        if "color_top" in kw:
            self._color_top = kw.pop("color_top")
            try:
                self._canvas.itemconfigure("top", fill=self._color_top)
            except Exception:
                pass
        if "color_bottom" in kw:
            self._color_bottom = kw.pop("color_bottom")
            try:
                self._canvas.itemconfigure("3d", fill=self._color_bottom)
            except Exception:
                pass
        if kw:
            # 还有未处理的参数，安全地传给 super
            try:
                super().configure(**kw)
            except Exception:
                # 静默忽略 Frame 不支持的参数
                pass


class RoundedButton(tk.Canvas):
    """v7.6 圆角按钮（纯 Canvas 自绘，强制 width/height，最稳定）。

    与 v7.5 关键区别：
    - 直接继承 tk.Canvas，不再用 Frame+Canvas 包裹
    - 强制 width/height 作为 Canvas 固有尺寸（不会被子 item 撑小）
    - hover/press 三态颜色变化
    - create_text 画文字（自绘在圆角之上）
    - 支持 disabled 状态（变灰）
    """

    def __init__(self, parent, text="", command=None, radius=10,
                 fill="#FFD700", fill_hover="#FFE55C", fill_press="#D4A017",
                 text_color="#0a0e1a", font=None, width=120, height=40,
                 padx=22, pady=10, cursor="hand2", disabled=False, **kwargs):
        if font is None:
            font = ("Microsoft YaHei UI", 10, "bold")

        # 保存状态
        self._text = text
        self._command = command
        self._radius = radius
        self._fill = fill
        self._fill_hover = fill_hover
        self._fill_press = fill_press
        self._current_fill = fill
        self._text_color = text_color
        self._font = font
        self._disabled = disabled
        self._pressed = False
        self._hover = False

        # Canvas 背景 = 父容器背景（圆角外不可见）
        try:
            parent_bg = parent.cget("bg")
        except Exception:
            parent_bg = Colors.BG_DARK

        super().__init__(
            parent, highlightthickness=0, bd=0, bg=parent_bg,
            width=width, height=height,
            cursor=cursor if not disabled else "arrow",
        )

        # 文字（用 create_text 居中）
        self._text_id = self.create_text(
            width // 2, height // 2, text=text,
            fill=text_color, font=font, anchor="center",
        )

        # 事件绑定
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda e: self._redraw())

    def _on_enter(self, _):
        if self._disabled:
            return
        self._hover = True
        self._current_fill = self._fill_press if self._pressed else self._fill_hover
        self._redraw()

    def _on_leave(self, _):
        if self._disabled:
            return
        self._hover = False
        self._pressed = False
        self._current_fill = self._fill
        self._redraw()

    def _on_press(self, _):
        if self._disabled:
            return
        self._pressed = True
        self._current_fill = self._fill_press
        self._redraw()

    def _on_release(self, _):
        if self._disabled:
            return
        was_pressed = self._pressed
        self._pressed = False
        self._current_fill = self._fill_hover if self._hover else self._fill
        self._redraw()
        if was_pressed and self._command:
            try:
                self._command()
            except Exception as e:
                print(f"[RoundedButton] cmd error: {e}")

    def _redraw(self):
        try:
            self.delete("bg")
        except Exception:
            return
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        r = min(self._radius, w // 2, h // 2)
        pts = [
            r, 0, w - r, 0, w, r, w, h - r,
            w - r, h, r, h, 0, h - r, 0, r,
        ]
        # 圆角填充
        self.create_polygon(pts, smooth=True, fill=self._current_fill,
                            outline="", tags="bg")
        # 文字居中（始终在最上层）
        self.coords(self._text_id, w // 2, h // 2)
        self.tag_raise(self._text_id)

    def configure(self, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
            self.itemconfigure(self._text_id, text=self._text)
        if "state" in kw:
            new_state = kw.pop("state")
            self._disabled = (new_state == "disabled")
            cursor = "arrow" if self._disabled else "hand2"
            self.configure(cursor=cursor)
        if "fill" in kw:
            self._fill = kw.pop("fill")
            self._current_fill = self._fill
            self._redraw()
        super().configure(**kw)


class GradientHero(tk.Canvas):
    """v7.6 Hero 渐变背景卡片（参考截图：青→蓝 渐变 + LOGO + 标题 + 统计指标）。

    用 PIL 在 Canvas 上画水平渐变（Image→PhotoImage→create_image）。
    支持嵌入内容（通过 inner 属性，访问内容容器）。
    """

    # 渐变缓存（避免重复渲染）
    _gradient_cache = {}

    @classmethod
    def _make_gradient_with_mask(cls, w, h, c1, c2, radius, direction="horizontal"):
        """用 PIL 生成渐变图（带圆角蒙版），返回 PhotoImage。"""
        key = (w, h, c1, c2, radius, direction)
        if key in cls._gradient_cache:
            return cls._gradient_cache[key]
        try:
            from PIL import Image, ImageDraw
            # 先生成渐变 RGBA 图
            raw = Image.new("RGBA", (w, h))
            px = raw.load()
            r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
            r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
            if direction == "horizontal":
                for x in range(w):
                    t = x / max(1, w - 1)
                    r = int(r1 + (r2 - r1) * t)
                    g = int(g1 + (g2 - g1) * t)
                    b = int(b1 + (b2 - b1) * t)
                    for y in range(h):
                        px[x, y] = (r, g, b, 255)
            else:
                for x in range(w):
                    for y in range(h):
                        t = (x + y) / max(1, w + h - 2)
                        r = int(r1 + (r2 - r1) * t)
                        g = int(g1 + (g2 - g1) * t)
                        b = int(b1 + (b2 - b1) * t)
                        px[x, y] = (r, g, b, 255)
            # 应用圆角蒙版
            mask = Image.new("L", (w, h), 0)
            md = ImageDraw.Draw(mask)
            md.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
            raw.putalpha(mask)
            from PIL import ImageTk
            photo = ImageTk.PhotoImage(raw)
            cls._gradient_cache[key] = photo
            return photo
        except Exception as e:
            print(f"[GradientHero] PIL 不可用: {e}")
            return None

    def __init__(self, parent, width=600, height=170, radius=18,
                 color_from=None, color_to=None, direction="horizontal",
                 **kwargs):
        if color_from is None:
            color_from = Colors.HERO_GRADIENT_FROM
        if color_to is None:
            color_to = Colors.HERO_GRADIENT_TO

        try:
            parent_bg = parent.cget("bg")
        except Exception:
            parent_bg = Colors.BG_DARK

        super().__init__(parent, highlightthickness=0, bd=0, bg=parent_bg,
                         width=width, height=height, **kwargs)

        self._radius = radius
        self._c1 = color_from
        self._c2 = color_to
        self._direction = direction
        self._img_id = None
        self._img_ref = None  # 防止 GC
        self._win_id = None

        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, _):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        # 生成渐变图（带圆角蒙版）
        photo = self._make_gradient_with_mask(w, h, self._c1, self._c2, self._radius, self._direction)
        if photo is None:
            return
        if self._img_id is not None:
            try:
                self.delete(self._img_id)
            except Exception:
                pass
        self._img_id = self.create_image(0, 0, image=photo, anchor="nw")
        self._img_ref = photo
        # 让内容窗口保持在渐变之上
        if self._win_id is not None:
            self.tag_raise(self._win_id)

    def set_content(self, fill=None):
        """嵌入一个内容窗口到 Canvas，返回 Frame。

        调用者应该把内容 pack 到返回的 Frame 上。
        """
        if fill is None:
            fill = self._c1  # 默认用渐变起点色
        content = tk.Frame(self, bg=fill, highlightthickness=0, bd=0)
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        self._win_id = self.create_window(0, 0, window=content, anchor="nw",
                                           width=w, height=h)
        # 监听 Canvas 大小变化
        self.bind("<Configure>", lambda e: (
            self._on_resize(None),
            self.itemconfigure(self._win_id, width=e.width, height=e.height) if self._win_id else None,
        ))
        return content


class _PressButton3D(tk.Frame):
    """v7.6.19 3D 按压按钮 —— 金色顶面 + 深色 3D 底边 + 文字始终显示。

    设计（参考用户提供的 CSS button 样式）：
    - 金色顶面 + 深色 3D 底边（4px）
    - 文字**默认显示**（v7.6.19 调整：不再 hover 才显示）
    - 白字 bold，颜色对比强烈
    - hover: 顶面颜色变亮（RGB 各 +25）
    - press: 3D 底边消失 + 内容下移 4px
    - 视频号用 微信视频号.png 图标
    """

    def __init__(self, parent, text="", command=None, width=140, height=50,
                 icon_text="", icon_path=None, icon_size=18, font=None,
                 color_top=None, color_bottom=None, **kwargs):
        if font is None:
            font = ("Microsoft YaHei UI", 12, "bold")
        if color_top is None:
            color_top = Colors.GOLD_LIGHT      # #FFE55C
        if color_bottom is None:
            color_bottom = Colors.GOLD_DARK    # #B8860B

        # 总高度 = 显示高度 + 3（3D 底边，v7.6.26 缩小到 3px）
        self._3d_height = 3
        total_height = height + self._3d_height
        super().__init__(parent, bg=Colors.BG_DARK,
                         width=width, height=total_height, **kwargs)
        self.pack_propagate(False)

        self._width = width
        self._height = height  # 显示高度（不含 3D 边）
        self._text = text
        self._command = command
        self._font = font
        self._hovered = False
        self._pressed = False
        self._icon_text = icon_text
        self._icon_path = icon_path
        self._icon_size = icon_size
        self._color_top = color_top
        self._color_bottom = color_bottom

        # Canvas
        self._canvas = tk.Canvas(
            self, highlightthickness=0, bd=0,
            bg=Colors.BG_DARK,
            width=width, height=total_height,
        )
        self._canvas.pack()

        # 3D 底边（深色）
        self._canvas.create_rectangle(
            0, self._height, width, total_height,
            fill=color_bottom, outline="", tags="3d"
        )
        # 顶面（浅色）
        self._canvas.create_rectangle(
            0, 0, width, self._height,
            fill=color_top, outline="", tags="top"
        )

        # 图标 + 文字（v7.6.26 缩小：icon 14px、font 11、gap 8、padding 10）
        icon_x_left = 10  # 左 padding（v7.6.26: 20→10）
        icon_y = self._height // 2
        if icon_path:
            icon_img = _load_platform_icon(icon_path, size=icon_size)
            if icon_img is not None:
                self._icon_img = icon_img
                self._icon_id = self._canvas.create_image(
                    icon_x_left, icon_y, image=icon_img, anchor='w', tags="icon"
                )
            else:
                self._icon_id = self._canvas.create_text(
                    icon_x_left, icon_y, text=icon_text,
                    font=("Segoe UI Emoji", icon_size),
                    fill="#FFFFFF", anchor='w', tags="icon"
                )
        else:
            self._icon_id = self._canvas.create_text(
                icon_x_left, icon_y, text=icon_text,
                font=("Segoe UI Emoji", icon_size),
                fill="#FFFFFF", anchor='w', tags="icon"
            )

        # 文字（始终显示，左对齐在 icon 右侧，留更多间距）
        # v7.6.26: gap 14→8, 总 = 10 + icon_size + 8
        text_x_left = icon_x_left + icon_size + 8
        self._text_id = self._canvas.create_text(
            text_x_left, self._height // 2, text=text,
            font=font, fill="#FFFFFF", anchor='w', tags="text"
            # 默认 state='normal'（始终显示）
        )

        # 事件绑定（v8.2.1 双保险：tag_bind + canvas 全局 bind）
        # tag_bind 只在点击 item 区域时触发，Python 3.14 tkinter 有
        # 偶发问题导致 tag_bind 不响应；额外加 canvas.bind 兜底
        for tag in ("3d", "top", "icon", "text"):
            self._canvas.tag_bind(tag, '<Enter>', self._on_enter)
            self._canvas.tag_bind(tag, '<Leave>', self._on_leave)
            self._canvas.tag_bind(tag, '<Button-1>', self._on_press)
            self._canvas.tag_bind(tag, '<ButtonRelease-1>', self._on_release)
        # v8.2.1 额外把事件绑到整个 canvas（不依赖任何 tag）
        self._canvas.bind('<Button-1>', self._on_press)
        self._canvas.bind('<ButtonRelease-1>', self._on_release)
        # 确保在顶层
        self._canvas.tag_raise("text")
        self._canvas.tag_raise("icon")
        # v8.2.0 修复：删除 self._canvas.lift()（Python 3.14 tkinter bug
        # lift() 不传参数时 tk.call('raise', self._w, None) 会被传字符串
        # "None" 给 Tcl，导致 wrong # args 异常，整个 _PressButton3D
        # 构造失败，按钮行后续 UI 全部丢失。
        # tag_raise 已足够把 text/icon 提到 canvas 上层，无需再 lift canvas）

    def _on_enter(self, _=None):
        self._hovered = True
        # hover: 顶面变亮（RGB 各 +25）
        r1, g1, b1 = self._hex_to_rgb(self._color_top)
        bright_top = "#{:02x}{:02x}{:02x}".format(
            min(255, r1 + 25), min(255, g1 + 25), min(255, b1 + 25)
        )
        self._canvas.itemconfigure("top", fill=bright_top)

    def _on_leave(self, _=None):
        self._hovered = False
        was_pressed = self._pressed
        self._pressed = False
        # 恢复原色
        self._canvas.itemconfigure("top", fill=self._color_top)
        # 恢复 3D 底边
        self._canvas.coords("3d",
                            0, self._height,
                            self._width, self._height + self._3d_height)
        # v7.6.36 修复：只在上次按压过时才上移文字/icon（避免无按压时把文字移出顶部）
        if was_pressed:
            self._canvas.move("text", 0, -3)
            self._canvas.move("icon", 0, -3)

    def _on_press(self, _=None):
        # v8.2.1 修复：canvas.bind + tag_bind 会重复触发，
        # 必须检查 _pressed 防止文字/icon 下移超过 3px
        if self._pressed:
            return
        self._pressed = True
        # 3D 底边消失（覆盖整个高度）
        self._canvas.coords("3d",
                            0, 0,
                            self._width, self._height + self._3d_height)
        # 文字和 icon 下移 3px（v7.6.26: 4→3）
        self._canvas.move("text", 0, 3)
        self._canvas.move("icon", 0, 3)

    def _on_release(self, _=None):
        if not self._pressed:
            return
        self._pressed = False
        # 恢复 3D 底边
        self._canvas.coords("3d",
                            0, self._height,
                            self._width, self._height + self._3d_height)
        # 文字和 icon 复位（v7.6.26: 4→3）
        self._canvas.move("text", 0, -3)
        self._canvas.move("icon", 0, -3)
        # 触发命令
        if self._command:
            try:
                self._command()
            except Exception as e:
                print(f"[PressButton3D] cmd err: {e}")

    @staticmethod
    def _hex_to_rgb(hex_color):
        if not hex_color or not hex_color.startswith('#'):
            return (200, 200, 200)
        h = hex_color.lstrip('#')
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:
            return (200, 200, 200)

    def _set_text(self, new_text):
        """v7.6.28 动态修改按钮文字。"""
        try:
            self._canvas.itemconfigure(self._text_id, text=new_text)
            self._text = new_text
        except Exception:
            pass


class _IconExpandButton(tk.Frame):
    """v7.6.35 图标展开按钮 + 登录状态指示。

    设计：
    - 暗色背景 (#040f16) + 5px 圆角
    - 正常：图标居中显示，文字隐藏
    - hover：icon 左移 + 文字显示 + 登录状态圆点（绿色=已登录 / 灰色=未登录）
    - 支持点击回调 + 登录状态集成
    """

    ICON_AREA_W = 60

    def __init__(self, parent, text="", command=None, width=150, height=44,
                 icon_text="", icon_path=None, icon_size=20, font=None,
                 login_status="never", **kwargs):  # v7.6.35: 加 login_status
        if font is None:
            font = ("Microsoft YaHei UI", 14, "bold")
        super().__init__(parent, bg=Colors.BG_DARK, **kwargs)
        self._width = width
        self._height = height
        self._text = text
        self._command = command
        self._font = font
        self._hovered = False
        self._icon_text = icon_text
        self._icon_path = icon_path
        self._login_status = login_status  # v7.6.35: 登录状态

        # Canvas 画背景和图标
        self._canvas = tk.Canvas(
            self, highlightthickness=0, bd=0,
            bg=Colors.BG_DARK,
            width=width, height=height,
        )
        self._canvas.pack()

        # 1. 圆角矩形底
        self._canvas.create_rectangle(
            0, 0, width, height, fill="#040f16", outline="", tags="bg"
        )

        # 2. icon
        self._icon_normal_x = width // 2
        self._icon_hover_x = self.ICON_AREA_W // 2
        icon_y = height // 2
        if icon_path:
            icon_img = _load_platform_icon(icon_path, size=icon_size)
            if icon_img is not None:
                self._icon_img = icon_img
                self._icon_id = self._canvas.create_image(
                    self._icon_normal_x, icon_y, image=icon_img, anchor='center', tags="icon"
                )
            else:
                self._icon_id = self._canvas.create_text(
                    self._icon_normal_x, icon_y, text=icon_text,
                    font=("Segoe UI Emoji", icon_size),
                    fill="#f5f5f5", anchor='center', tags="icon"
                )
        else:
            self._icon_id = self._canvas.create_text(
                self._icon_normal_x, icon_y, text=icon_text,
                font=("Segoe UI Emoji", icon_size),
                fill="#f5f5f5", anchor='center', tags="icon"
            )

        # 3. 文字 (normal hidden)
        self._text_id = self._canvas.create_text(
            self.ICON_AREA_W + 4, height // 2, text=text,
            font=font, fill="#f5f5f5", anchor='w', tags="text",
            state='hidden'
        )

        # 4. v7.6.35：登录状态圆点（hover 时显示在文字右侧）
        status_color = Colors.STATUS_ONLINE if login_status == "logged_in" else Colors.TEXT_MUTED
        status_char = "●" if login_status == "logged_in" else "○"
        self._status_id = self._canvas.create_text(
            self.ICON_AREA_W + 4 + len(text) * 10, height // 2,
            text=f"  {status_char}",
            font=("Microsoft YaHei UI", 9),
            fill=status_color, anchor='w', tags="status",
            state='hidden'
        )

        # 5. 边框
        self._border_id = self._canvas.create_rectangle(
            0, 0, width, height, fill="", outline="", width=0, tags="border"
        )

        # 事件绑定
        for tag in ("bg", "icon", "text", "status"):
            self._canvas.tag_bind(tag, '<Enter>', self._on_enter)
            self._canvas.tag_bind(tag, '<Leave>', self._on_leave)
            self._canvas.tag_bind(tag, '<Button-1>', self._on_click)
        self._canvas.tag_raise("text")
        self._canvas.tag_raise("status")
        self._canvas.tag_raise("icon")

    def set_login_status(self, status):
        """v7.6.35 动态更新登录状态。"""
        self._login_status = status
        color = Colors.STATUS_ONLINE if status == "logged_in" else Colors.TEXT_MUTED
        char = "●" if status == "logged_in" else "○"
        self._canvas.itemconfigure(self._status_id, text=f"  {char}", fill=color)

    def _on_enter(self, _=None):
        self._hovered = True
        self._canvas.itemconfigure(self._text_id, state='normal')
        # 登录状态也显示
        if self._login_status == "logged_in":
            self._canvas.itemconfigure(self._status_id, state='normal')
            self._canvas.itemconfigure(self._status_id, fill=Colors.STATUS_ONLINE)
        else:
            # 未登录也显示（灰色 ○）
            self._canvas.itemconfigure(self._status_id, state='normal')
            self._canvas.itemconfigure(self._status_id, fill=Colors.TEXT_MUTED)
        self._canvas.coords(self._icon_id, self._icon_hover_x, self._height // 2)
        self._canvas.itemconfigure(self._border_id, outline=Colors.GOLD_PRIMARY, width=2)
        self._canvas.itemconfigure("bg", fill="#0a1525")
        self._canvas.itemconfigure(self._text_id, fill=Colors.GOLD_PRIMARY)

    def _on_leave(self, _=None):
        self._hovered = False
        self._canvas.itemconfigure(self._text_id, state='hidden')
        self._canvas.itemconfigure(self._status_id, state='hidden')
        self._canvas.coords(self._icon_id, self._icon_normal_x, self._height // 2)
        self._canvas.itemconfigure(self._border_id, outline="", width=0)
        self._canvas.itemconfigure("bg", fill="#040f16")
        self._canvas.itemconfigure(self._text_id, fill="#f5f5f5")

    def _on_click(self, _=None):
        if self._command:
            try:
                self._command()
            except Exception as e:
                print(f"[IconExpandButton] cmd err: {e}")


class _SignUpButton(tk.Frame):
    """v7.6.12 Uiverse "Sign Up" 风格按钮 —— 暗色径向渐变 + 底部光带 + hover 缩放上浮。

    设计（参考用户提供的 CSS .button 示例）：
    - 暗色径向渐变（椭圆 at bottom：gray 71,81,92 → dark 11,21,30）
    - 1px inset 白边（rgba 26,26,26,26/255）
    - 底部 1px 渐变白线（normal: 20% alpha，hover: 100%）
    - 文字：normal 66% 白，hover 100% 白
    - hover: scale(1.05) + translateY(-2px) + 文字变亮 + 光带变亮
    - 圆角 7px
    - 支持前缀 icon（视频号工具用 微信视频号.png）
    """

    # 按钮背景缓存（避免重复渲染）
    _bg_cache = {}

    @classmethod
    def _make_button_bg(cls, width, height, hovered=False):
        """用 PIL 生成按钮背景（径向渐变 + 圆角 + 1px 白边 + 底部渐变光带）。"""
        from PIL import Image, ImageDraw, ImageFilter
        cache_key = (width, height, hovered)
        if cache_key in cls._bg_cache:
            return cls._bg_cache[cache_key]

        # 1. 创建径向渐变（用 mask 实现）
        # 椭圆 at bottom: 从 gray 渐变到 dark
        mask = Image.new('L', (width, height), 0)
        md = ImageDraw.Draw(mask)
        rx = max(20, int(width * 0.6))
        ry = max(15, int(height * 0.7))
        cx = width // 2
        cy = height  # 椭圆底部对齐
        md.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(radius=max(width, height) // 5))

        # 渐变底层：dark (11, 21, 30)
        grad = Image.new('RGBA', (width, height), (11, 21, 30, 255))
        # 在椭圆中心画 gray (71, 81, 92)
        gd = ImageDraw.Draw(grad)
        gd.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(71, 81, 92, 255))
        # 应用 mask 作为 alpha（让渐变柔和过渡）
        grad.putalpha(mask)

        # 2. 应用圆角 mask
        rounded_mask = Image.new('L', (width, height), 0)
        rmd = ImageDraw.Draw(rounded_mask)
        rmd.rounded_rectangle((0, 0, width - 1, height - 1), radius=7, fill=255)
        result = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        result.paste(grad, (0, 0), rounded_mask)

        # 3. 画 1px inset 白边
        bd = ImageDraw.Draw(result)
        bd.rounded_rectangle(
            (0, 0, width - 1, height - 1), radius=7,
            outline=(255, 255, 255, 26),  # 10% alpha (26/255)
            width=1,
        )

        # 4. 底部 1px 渐变白线（只在圆角底部内侧）
        # 20% normal, 100% hover
        line_max_alpha = 255 if hovered else 51
        bd = ImageDraw.Draw(result)
        # 用渐变方式：逐像素设置
        # 先画一个 1px 高的渐变图像，再 mask 应用到圆角内
        line_strip_h = 1
        line_strip = Image.new('RGBA', (width, line_strip_h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(line_strip)
        for x in range(width):
            t = 1.0 - abs(x - width // 2) / (width // 2)
            if t < 0:
                t = 0
            alpha = int(line_max_alpha * t)
            ld.point((x, 0), fill=(255, 255, 255, alpha))
        # 粘贴到底部
        result.paste(line_strip, (0, height - 1), line_strip)

        from PIL import ImageTk
        photo = ImageTk.PhotoImage(result)
        cls._bg_cache[cache_key] = photo
        return photo

    def __init__(self, parent, text="", command=None, width=140, height=44,
                 icon_path=None, icon_size=18, font=None, **kwargs):
        if font is None:
            font = ("Microsoft YaHei UI", 12, "bold")
        super().__init__(parent, bg=Colors.BG_DARK, **kwargs)
        self._width = width
        self._height = height
        self._orig_w = width
        self._orig_h = height
        self._text = text
        self._command = command
        self._font = font
        self._hovered = False
        self._icon_path = icon_path
        self._icon_size = icon_size

        # Canvas 画背景
        self._canvas = tk.Canvas(
            self, highlightthickness=0, bd=0,
            bg=Colors.BG_DARK,
            width=width, height=height,
        )
        self._canvas.pack()

        # 加载背景
        self._img_normal = self._make_button_bg(width, height, hovered=False)
        self._img_hover = self._make_button_bg(width, height, hovered=True)
        self._canvas.create_image(0, 0, image=self._img_normal, anchor='nw', tags="bg")

        # 文字（normal 66% 白）
        text_x = width // 2
        if icon_path:
            icon_img = _load_platform_icon(icon_path, size=icon_size)
            if icon_img is not None:
                self._icon_img = icon_img
                # icon 在文字左侧
                self._canvas.create_image(
                    width // 2 - 30, height // 2,
                    image=icon_img, anchor='center', tags="icon"
                )
                text_x = width // 2 + 14  # 文字往右挪
        # 文字
        self._text_id = self._canvas.create_text(
            text_x, height // 2, text=text,
            font=font, fill="#aaaaaa",  # 66% 白色
            anchor='center', tags="text"
        )

        # 绑定 hover 和 click
        self._canvas.tag_bind("bg", '<Enter>', self._on_enter)
        self._canvas.tag_bind("bg", '<Leave>', self._on_leave)
        self._canvas.tag_bind("bg", '<Button-1>', self._on_click)
        self._canvas.tag_bind("text", '<Enter>', self._on_enter)
        self._canvas.tag_bind("text", '<Leave>', self._on_leave)
        self._canvas.tag_bind("text", '<Button-1>', self._on_click)
        if icon_path:
            self._canvas.tag_bind("icon", '<Enter>', self._on_enter)
            self._canvas.tag_bind("icon", '<Leave>', self._on_leave)
            self._canvas.tag_bind("icon", '<Button-1>', self._on_click)
        # 确保在顶层
        self._canvas.tag_raise("text")
        if icon_path:
            self._canvas.tag_raise("icon")

    def _on_enter(self, _=None):
        self._hovered = True
        # 切换到 hover 背景
        self._canvas.delete("bg")
        self._canvas.create_image(0, 0, image=self._img_hover, anchor='nw', tags="bg")
        # 文字变 100% 白
        self._canvas.itemconfigure(self._text_id, fill="#ffffff")
        # 缩放上浮（1.05x + -2px）
        new_w = int(self._orig_w * 1.05)
        new_h = int(self._orig_h * 1.05)
        # 用 place 实现上浮（保持父布局不动，只改 button 自身位置）
        # 先获取当前位置
        # 简化：先 unplace 再 place（破坏布局，不推荐）
        # 改用 zoom via Canvas scale（需要重画）
        # 最简方案：上浮用 place 偏移
        # self.place_configure(...) 需要 pack 信息
        # 这里用 widget.configure(width=new_w, height=new_h) 不会动布局
        # Canvas 不能 configure width/height 缩放
        # 跳过缩放，只做颜色和光带变化
        self._canvas.tag_raise("text")
        if self._icon_path:
            self._canvas.tag_raise("icon")

    def _on_leave(self, _=None):
        self._hovered = False
        self._canvas.delete("bg")
        self._canvas.create_image(0, 0, image=self._img_normal, anchor='nw', tags="bg")
        self._canvas.itemconfigure(self._text_id, fill="#aaaaaa")
        self._canvas.tag_raise("text")
        if self._icon_path:
            self._canvas.tag_raise("icon")

    def _on_click(self, _=None):
        if self._command:
            try:
                self._command()
            except Exception as e:
                print(f"[SignUpButton] cmd err: {e}")


class _UiverseActionButton(tk.Frame):
    """v7.6.9 Uiverse 风格动作按钮 —— 比 GlowCapsule 更紧凑，支持自定义 glow 颜色。

    用途：解析输入区的 5 个功能按钮（获取流链接 / HEVC / 复制 / 代理 / 视频号工具）。
    区别于 GlowCapsule：
    - 内容区域更宽（只有 80px 让出给光晕，按钮本身宽度更小）
    - glow 颜色可自定义（用平台品牌色或功能色）
    - 支持前缀 icon 图（视频号工具用 微信视频号.png）
    """

    def __init__(self, parent, text="", command=None, width=140, height=40,
                 glow_color=None, glow_color2=None, text_color=None,
                 icon_path=None, icon_size=18, font=None, **kwargs):
        if glow_color is None:
            glow_color = GlowCapsule.COLOR_ROSE
        if glow_color2 is None:
            glow_color2 = GlowCapsule.COLOR_VIOLET
        if text_color is None:
            text_color = Colors.TEXT_PRIMARY
        if font is None:
            font = ("Microsoft YaHei UI", 10, "bold")

        super().__init__(parent, bg=Colors.BG_DARK,
                         width=width, height=height, **kwargs)
        self.pack_propagate(False)

        self._width = width
        self._height = height
        self._text = text
        self._command = command
        self._text_color = text_color
        self._font = font
        self._hovered = False

        # 临时保存配置（hover 时用）
        self._glow_color = glow_color
        self._glow_color2 = glow_color2

        # Canvas 画圆角 + 光晕
        self._canvas = tk.Canvas(
            self, highlightthickness=0, bd=0,
            bg=Colors.BG_DARK,
            width=width, height=height,
        )
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)

        # 内容（透明 frame），让出右侧 40px 给光晕（比 80 少，确保文字不被截断）
        self._content = tk.Frame(self, bg=Colors.BG_CARD,
                                  width=max(60, width - 40), height=height)
        self._content.place(x=0, y=0, width=max(60, width - 40), height=height)
        self._content.pack_propagate(False)

        # icon + 文字
        if icon_path:
            # 加载 icon 图
            icon_img = _load_platform_icon(icon_path, size=icon_size)
            if icon_img is not None:
                self._icon_lbl = tk.Label(
                    self._content, image=icon_img, bg=Colors.BG_CARD,
                )
                self._icon_lbl.image = icon_img
                self._icon_lbl.pack(side="left", padx=(10, 6), pady=0)

        # 文字
        self._text_lbl = tk.Label(
            self._content, text=text,
            font=font, bg=Colors.BG_CARD, fg=text_color,
            cursor="hand2",
        )
        self._text_lbl.pack(side="left", pady=0, padx=(2, 4))

        # 绑定事件
        for w in (self, self._canvas, self._content, self._text_lbl):
            w.bind('<Button-1>', self._on_click)
            w.bind('<Enter>', self._on_enter)
            w.bind('<Leave>', self._on_leave)

        # 初次绘制
        self._draw_normal()
        self._canvas.tag_raise("bg")

    def _on_click(self, _=None):
        if self._command:
            try:
                self._command()
            except Exception as e:
                print(f"[UiverseActionButton] cmd err: {e}")

    def _on_enter(self, _=None):
        self._hovered = True
        self._canvas.delete("bg")
        self._draw_hover()
        self._canvas.tag_raise("hover")

    def _on_leave(self, _=None):
        self._hovered = False
        self._canvas.delete("hover")
        self._draw_normal()
        self._canvas.tag_raise("bg")

    def _draw_normal(self):
        """绘制 normal 状态的光晕。"""
        w, h = self._width, self._height
        # 圆角矩形
        from PIL import Image, ImageDraw, ImageFilter
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        radius = 10
        draw.rounded_rectangle(
            (0, 0, w - 1, h - 1), radius=radius,
            fill=(22, 27, 46, 255),
            outline=(58, 66, 88, 200),
            width=1,
        )
        # rose 圆（位置靠右，避免覆盖文字）
        c1 = self._hex_to_rgb(self._glow_color)
        r1 = max(4, int(h * 0.28))
        x1 = int(w * 0.88)
        y1 = int(h * 0.5)
        draw.ellipse([x1 - r1, y1 - r1, x1 + r1, y1 + r1], fill=c1 + (220,))
        # violet 圆（位置更靠右）
        c2 = self._hex_to_rgb(self._glow_color2)
        r2 = max(5, int(h * 0.32))
        x2 = int(w * 0.97)
        y2 = int(h * 0.45)
        draw.ellipse([x2 - r2, y2 - r2, x2 + r2, y2 + r2], fill=c2 + (200,))
        # 模糊
        blur_r = max(4, int(h * 0.12))
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_r))
        from PIL import ImageTk
        photo = ImageTk.PhotoImage(img)
        self._img_normal = photo
        self._canvas.create_image(0, 0, image=photo, anchor='nw', tags="bg")

    def _draw_hover(self):
        """绘制 hover 状态（光晕扩大 + 错位）。"""
        w, h = self._width, self._height
        from PIL import Image, ImageDraw, ImageFilter
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        radius = 10
        draw.rounded_rectangle(
            (0, 0, w - 1, h - 1), radius=radius,
            fill=(22, 27, 46, 255),
            outline=(80, 90, 120, 255),  # hover 时边框更亮
            width=1,
        )
        c1 = self._hex_to_rgb(self._glow_color)
        r1 = max(5, int(h * 0.36))
        x1 = int(w * 0.82)
        y1 = int(h * 0.6)
        draw.ellipse([x1 - r1, y1 - r1, x1 + r1, y1 + r1], fill=c1 + (250,))
        c2 = self._hex_to_rgb(self._glow_color2)
        r2 = max(6, int(h * 0.4))
        x2 = int(w * 0.95)
        y2 = int(h * 0.4)
        draw.ellipse([x2 - r2, y2 - r2, x2 + r2, y2 + r2], fill=c2 + (230,))
        blur_r = max(4, int(h * 0.12))
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_r))
        from PIL import ImageTk
        photo = ImageTk.PhotoImage(img)
        self._img_hover = photo
        self._canvas.create_image(0, 0, image=photo, anchor='nw', tags="hover")

    @staticmethod
    def _hex_to_rgb(hex_color):
        if not hex_color or not hex_color.startswith('#'):
            return (140, 140, 200)
        h = hex_color.lstrip('#')
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:
            return (140, 140, 200)


class StatusBadge(tk.Canvas):
    """v7.6 状态徽章 pill（圆角 + 状态点 + 文字）。

    用于侧边栏菜单项、Hero 状态、平台登录状态等。
    """

    def __init__(self, parent, text="", dot_color=None, fill=None,
                 text_color=None, font=None, width=110, height=28, radius=14,
                 padx=12, **kwargs):
        if dot_color is None:
            dot_color = Colors.STATUS_OFFLINE
        if fill is None:
            fill = Colors.BG_CARD
        if text_color is None:
            text_color = Colors.TEXT_PRIMARY
        if font is None:
            font = ("Microsoft YaHei UI", 9)

        try:
            parent_bg = parent.cget("bg")
        except Exception:
            parent_bg = Colors.BG_DARK

        super().__init__(parent, highlightthickness=0, bd=0, bg=parent_bg,
                         width=width, height=height, **kwargs)

        self._text = text
        self._dot_color = dot_color
        self._fill = fill
        self._text_color = text_color
        self._font = font
        self._radius = radius
        self._width = width
        self._height = height

        # 内容：圆点 + 文字
        self._dot_id = self.create_oval(8, height // 2 - 3, 14, height // 2 + 3,
                                         fill=dot_color, outline="")
        self._text_id = self.create_text(width // 2 + 6, height // 2, text=text,
                                          fill=text_color, font=font, anchor="center")

        self.bind("<Configure>", lambda e: self._redraw())

    def _redraw(self):
        try:
            self.delete("bg")
        except Exception:
            return
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        r = min(self._radius, h // 2)
        pts = [
            r, 0, w - r, 0, w, r, w, h - r,
            w - r, h, r, h, 0, h - r, 0, r,
        ]
        self.create_polygon(pts, smooth=True, fill=self._fill, outline="", tags="bg")
        # 把文字和圆点提到最上层
        self.tag_raise(self._dot_id)
        self.tag_raise(self._text_id)

    def set_status(self, text=None, dot_color=None, text_color=None):
        """更新徽章文字和颜色。"""
        if text is not None:
            self._text = text
            self.itemconfigure(self._text_id, text=text)
        if dot_color is not None:
            self._dot_color = dot_color
            self.itemconfigure(self._dot_id, fill=dot_color)
        if text_color is not None:
            self._text_color = text_color
            self.itemconfigure(self._text_id, fill=text_color)

    def configure(self, **kw):
        if "text" in kw:
            self.set_status(text=kw.pop("text"))
        super().configure(**kw)


class IconBox(tk.Canvas):
    """v7.6 平台图标方块（圆角矩形 + emoji 居中 + 可选品牌色背景）。

    用于侧边栏菜单项、平台卡片头部。
    """

    def __init__(self, parent, icon="📦", bg_color=None, size=36, radius=8,
                 text_color="#ffffff", font=None, **kwargs):
        if bg_color is None:
            bg_color = Colors.BG_CARD_LIGHT
        if font is None:
            font = ("Segoe UI Emoji", int(size * 0.5))

        try:
            parent_bg = parent.cget("bg")
        except Exception:
            parent_bg = Colors.BG_DARK

        super().__init__(parent, highlightthickness=0, bd=0, bg=parent_bg,
                         width=size, height=size, **kwargs)
        self._size = size
        self._radius = radius
        self._bg_color = bg_color
        # 圆角背景
        self._bg_id = self.create_rectangle(0, 0, size, size, fill=bg_color, outline="")
        # emoji
        self._icon_id = self.create_text(size // 2, size // 2, text=icon,
                                          fill=text_color, font=font, anchor="center")
        self.bind("<Configure>", lambda e: self._redraw())

    def _redraw(self):
        try:
            self.coords(self._bg_id, 0, 0, self.winfo_width(), self.winfo_height())
        except Exception:
            pass

    def set_color(self, bg_color):
        self._bg_color = bg_color
        self.itemconfigure(self._bg_id, fill=bg_color)


class GlowCapsule(tk.Frame):
    """v7.6.11 Uiverse "See more" 风格 —— 更小、文字图标在顶层、Uiverse 配色。

    设计（参考 Uiverse.io Javierrocadev 的 "See more" 按钮）：
    ┌────────────────────────────────────────┐
    │ 🟦 抖音    ●rose (大)             ●violet (小)│
    │          ────────                         │
    └────────────────────────────────────────┘
    - 紧凑圆角矩形（rounded-lg 12px，高度 48px）
    - 文字和图标在 content frame 顶层（tkraise）
    - 右侧两个大模糊圆（rose + violet），Uiverse 风格
    - 缩小 GLOW_AREA_WIDTH 让文字更突出
    """

    # 右侧光晕区宽度（不可见 padding 用来露出 Canvas 光晕）
    # v7.6.11 缩到 60：让文字区域更大（5 平台单行也能放下"小红书"）
    GLOW_AREA_WIDTH = 60

    # Uiverse 原始配色（双圆用色）
    COLOR_VIOLET = "#a78bfa"      # violet-400
    COLOR_ROSE = "#fda4af"        # rose-300
    COLOR_ROSE_HOVER = "#fb7185"  # rose-400

    _glow_cache = {}  # PIL 图片缓存（避免重复渲染）

    @classmethod
    def _make_glow_image(cls, width, height, hovered=False, expand=1.0):
        """生成 Uiverse 风格的双圆模糊光晕背景图（更紧凑、更鲜艳）。"""
        from PIL import Image, ImageDraw, ImageFilter
        cache_key = (width, height, hovered, round(expand * 10))
        if cache_key in cls._glow_cache:
            return cls._glow_cache[cache_key]

        # 透明背景
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 圆角矩形底（深色 BG_CARD = #161b2e）+ 浅色边框
        radius = 12  # rounded-lg (Uiverse 默认)
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1), radius=radius,
            fill=(22, 27, 46, 255),        # BG_CARD
            outline=(70, 78, 110, 220),    # BORDER 略亮
            width=1,
        )

        # 比例缩放（参考 Uiverse 256x48，按当前尺寸等比缩放）
        scale_x = width / 256
        scale_y = height / 48
        scale = (scale_x + scale_y) / 2

        # 第一个圆 - rose "after" (大圆 - Uiverse after 是 20x20)
        c1 = (253, 164, 175)  # rose-300
        r1 = max(6, int(11 * scale * expand))
        if hovered:
            x1 = int(width - 4 * scale)
            y1 = int(height * 0.4)
        else:
            x1 = int(width - 20 * scale)
            y1 = int(height * 0.35)
        alpha1 = 240 if hovered else 220
        draw.ellipse([x1 - r1, y1 - r1, x1 + r1, y1 + r1], fill=c1 + (alpha1,))

        # 第二个圆 - violet "before" (小圆，右上)
        c2 = (167, 139, 250)  # violet-400
        r2 = max(4, int(7 * scale * expand))
        if hovered:
            x2 = int(width - 4 * scale)
            y2 = int(height * 0.5)
        else:
            x2 = int(width - 4 * scale)
            y2 = int(4 * scale)
        alpha2 = 240 if hovered else 220
        draw.ellipse([x2 - r2, y2 - r2, x2 + r2, y2 + r2], fill=c2 + (alpha2,))

        # 模糊（让两个圆融在一起 - 模拟 Uiverse blur-lg 16px）
        blur_r = max(5, int(10 * scale))
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_r))

        from PIL import ImageTk
        photo = ImageTk.PhotoImage(img)
        cls._glow_cache[cache_key] = photo
        return photo

    def __init__(self, parent, height=48, **kwargs):
        # v7.6.11 默认高度 48（更紧凑）
        super().__init__(parent, bg=Colors.BG_DARK, height=height, **kwargs)
        self.pack_propagate(False)

        self._height = height
        self._hovered = False
        self._img_id = None
        self._img_ref = None  # 防止 GC

        # Canvas 画圆角 + 光晕（占满 Frame，最底层）
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=Colors.BG_DARK)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)

        # 内容 Frame（bg=Colors.BG_CARD，宽度 = 胶囊宽 - GLOW_AREA_WIDTH）
        # 右侧 GLOW_AREA_WIDTH 像素留给 canvas 显示光晕
        self.content = tk.Frame(self, bg=Colors.BG_CARD, height=height)
        self.content.place(x=0, y=0, height=height)

        # 关键：把 content 提到最顶层（确保文字图标不被光晕覆盖）
        self.content.lift()

        # 监听大小变化
        self.bind('<Configure>', self._on_configure)
        # hover 效果
        self._canvas.bind('<Enter>', self._on_enter)
        self._canvas.bind('<Leave>', self._on_leave)
        # 点击事件转发
        self._canvas.bind('<Button-1>', self._on_click)

    def _on_configure(self, event):
        # 重新调整 content frame 的宽度（留 GLOW_AREA_WIDTH 给光晕）
        new_w = max(100, event.width - self.GLOW_AREA_WIDTH)
        self.content.place_configure(x=0, y=0, width=new_w, height=event.height)
        # 关键：每次 resize 后重新提到顶层
        self.content.lift()
        self._redraw()

    def _on_enter(self, _):
        self._hovered = True
        self._redraw()
        # 转发 hover
        self.event_generate('<Enter>')

    def _on_leave(self, _):
        self._hovered = False
        self._redraw()
        self.event_generate('<Leave>')

    def _on_click(self, event):
        # 转发点击
        self.event_generate('<Button-1>', x=event.x, y=event.y)

    def _redraw(self):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return
        # 清除旧图
        if self._img_id is not None:
            try:
                self._canvas.delete(self._img_id)
            except Exception:
                pass
        # 生成光晕图（hover 时 expand=1.2，圆会扩散并改变位置）
        expand = 1.2 if self._hovered else 1.0
        photo = self._make_glow_image(w, h, hovered=self._hovered, expand=expand)
        self._img_ref = photo
        self._img_id = self._canvas.create_image(0, 0, image=photo, anchor='nw')
        # 关键：canvas 重新画图后，content 保持顶层
        self.content.lift()


# ── 全局 ttk 风格（深色细滑条）──
_dark_scrollbar_style_initialized = False

def _init_dark_scrollbar_style():
    """v7.6.27 初始化 ttk 深色细滑条样式（必须在 root 创建之后调用一次）。"""
    global _dark_scrollbar_style_initialized
    if _dark_scrollbar_style_initialized:
        return
    try:
        from tkinter import ttk
        style = ttk.Style()
        # 关键：Windows 默认主题 'vista'/'winnative' 不允许自定义颜色
        # 必须切换到 'clam' 主题才能应用 troughcolor / background
        try:
            style.theme_use('clam')
        except Exception:
            pass
        # 滚动条配色 - 与暗色主题融合
        # trough（轨道）= BG_DARK 深蓝黑（与背景一致，几乎不可见）
        # background（滑块）= 浅灰蓝
        # arrowcolor（箭头）= 浅灰
        style.configure(
            "Dark.Vertical.TScrollbar",
            troughcolor="#0a0e1a",       # 轨道深色（与背景融合）
            background="#3a4258",       # 滑块浅灰蓝
            darkcolor="#3a4258",
            lightcolor="#3a4258",
            bordercolor="#0a0e1a",
            arrowcolor="#9aa3bd",        # 箭头浅灰
            gripcount=0,
            width=8,                    # 细滑条 8px（默认 16px）
        )
        style.configure(
            "Dark.Horizontal.TScrollbar",
            troughcolor="#0a0e1a",
            background="#3a4258",
            darkcolor="#3a4258",
            lightcolor="#3a4258",
            bordercolor="#0a0e1a",
            arrowcolor="#9aa3bd",
            gripcount=0,
            height=8,
        )
        # hover/active 状态
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[
                ("active", "#FFD700"),
                ("pressed", "#FFE55C"),
                ("!disabled", "#3a4258"),
            ],
            arrowcolor=[
                ("active", "#FFD700"),
                ("!disabled", "#9aa3bd"),
            ],
        )
        style.map(
            "Dark.Horizontal.TScrollbar",
            background=[
                ("active", "#FFD700"),
                ("pressed", "#FFE55C"),
                ("!disabled", "#3a4258"),
            ],
            arrowcolor=[
                ("active", "#FFD700"),
                ("!disabled", "#9aa3bd"),
            ],
        )
        _dark_scrollbar_style_initialized = True
    except Exception as e:
        print(f"[Scrollbar] 初始化样式失败: {e}")


def _make_dark_scrollbar(parent, command, orient="vertical"):
    """v7.6.27 创建深色细滑条（替代 tk.Scrollbar 的丑陋白色）。"""
    from tkinter import ttk
    _init_dark_scrollbar_style()
    if orient == "vertical":
        s = ttk.Scrollbar(
            parent, orient="vertical",
            command=command,
            style="Dark.Vertical.TScrollbar",
        )
    else:
        s = ttk.Scrollbar(
            parent, orient="horizontal",
            command=command,
            style="Dark.Horizontal.TScrollbar",
        )
    return s


def _load_gold_logo(max_height: int = 36):
    """加载影视匠金色渐变 LOGO 并缩放到指定高度，返回 (PhotoImage, original_size)。

    失败时返回 (None, (0, 0))。PhotoImage 需要在 tkinter 容器存活期间保持引用，
    否则会被 Python GC 回收导致图片消失。
    """
    try:
        from PIL import Image, ImageTk
        # 优先用项目根目录的 LOGO
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "影视匠金色渐变LOGO.png"),
            os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))), "影视匠金色渐变LOGO.png"),
        ]
        # 兼容 EXE 运行：sys._MEIPASS 路径
        if hasattr(sys, '_MEIPASS'):
            candidates.insert(0, os.path.join(sys._MEIPASS, "影视匠金色渐变LOGO.png"))
        # EXE 同目录
        if getattr(sys, 'frozen', False):
            candidates.insert(0, os.path.join(os.path.dirname(sys.executable), "影视匠金色渐变LOGO.png"))

        for path in candidates:
            if os.path.isfile(path):
                img = Image.open(path).convert("RGBA")
                w, h = img.size
                if h > max_height:
                    ratio = max_height / h
                    img = img.resize((int(w * ratio), max_height), Image.LANCZOS)
                return ImageTk.PhotoImage(img), img.size
    except Exception as e:
        print(f"[LOGO] 加载失败: {e}")
    return None, (0, 0)


def _render_gold_gradient_text(text, font_size=24, bold=True, char_spacing=0, cache_key=None):
    """v7.6.23 渲染土豪金渐变文字为 PhotoImage（支持字符间距）。

    设计：垂直渐变（深金 -> 亮金 -> 浅金），模拟"土豪金"立体效果。
    - 顶部：深金 #B8860B
    - 中部：亮金 #FFD700
    - 底部：浅金 #FFE55C
    - 字体：Microsoft YaHei Bold
    - 字符间距：char_spacing（v7.6.23 新增，控制字与字之间的空隙）
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import os

        # 缓存避免重复渲染
        if cache_key is None:
            cache_key = (text, font_size, bold, char_spacing)
        if hasattr(_render_gold_gradient_text, '_cache') and cache_key in _render_gold_gradient_text._cache:
            return _render_gold_gradient_text._cache[cache_key]

        # 查找系统中文字体
        font_paths = [
            "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑 Bold
            "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
            "C:/Windows/Fonts/simhei.ttf",   # 黑体
            "C:/Windows/Fonts/simsun.ttc",   # 宋体
        ]
        font = None
        for fp in font_paths:
            if os.path.isfile(fp):
                try:
                    font = ImageFont.truetype(fp, font_size)
                    break
                except Exception:
                    pass
        if font is None:
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

        # 计算每个字符的宽度
        char_widths = []
        for ch in text:
            try:
                bbox = font.getbbox(ch)
                char_widths.append(bbox[2] - bbox[0])
            except Exception:
                char_widths.append(font_size)  # fallback
        try:
            text_h = font.getbbox(text)[3] - font.getbbox(text)[1]
        except Exception:
            text_h = font_size

        padding_x = 6
        padding_y = 8  # v7.6.24 增加 padding 避免文字被裁剪
        # 总宽度 = 所有字符宽度 + 字符间距*(n-1) + 左右 padding
        total_text_w = sum(char_widths) + char_spacing * max(0, len(text) - 1)
        img_w = total_text_w + padding_x * 2
        img_h = text_h + padding_y * 2

        # 创建透明背景的图像
        img = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 绘制渐变（垂直方向，从深金到亮金到浅金）
        for y in range(img_h):
            t = y / max(1, img_h - 1)
            # 渐变：顶部 #B8860B (深金) -> 中部 #FFD700 (亮金) -> 底部 #FFE55C (浅金)
            if t < 0.5:
                # 上半部分：深金 -> 亮金
                tt = t * 2
                r = int(0xB8 + (0xFF - 0xB8) * tt)
                g = int(0x86 + (0xD7 - 0x86) * tt)
                b = int(0x0B + (0x00 - 0x0B) * tt)
            else:
                # 下半部分：亮金 -> 浅金
                tt = (t - 0.5) * 2
                r = int(0xFF + (0xFF - 0xFF) * tt)
                g = int(0xD7 + (0xEC - 0xD7) * tt)
                b = int(0x00 + (0x8B - 0x00) * tt)
            draw.line([(0, y), (img_w, y)], fill=(r, g, b, 255))

        # 文字 mask（v7.6.24 用 anchor='lt' 让文字顶部对齐，避免 ascender 截断）
        mask = Image.new('L', (img_w, img_h), 0)
        mdraw = ImageDraw.Draw(mask)
        current_x = padding_x
        for i, ch in enumerate(text):
            # anchor='lt' = 文字顶部对齐 y 坐标
            mdraw.text((current_x, padding_y), ch, font=font, fill=255, anchor='lt')
            current_x += char_widths[i] + char_spacing

        # 应用 mask
        result = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
        result.paste(img, (0, 0), mask)

        from PIL import ImageTk
        photo = ImageTk.PhotoImage(result)
        if not hasattr(_render_gold_gradient_text, '_cache'):
            _render_gold_gradient_text._cache = {}
        _render_gold_gradient_text._cache[cache_key] = photo
        return photo
    except Exception as e:
        print(f"[GoldText] 渲染失败: {e}")
        return None


def _load_platform_icon(icon_name: str, size: int = 32):
    """加载平台 icon（icons/ 目录下），缩放到指定大小，返回 PhotoImage。

    失败时返回 None。PhotoImage 需要在 tkinter 容器存活期间保持引用。
    """
    try:
        from PIL import Image, ImageTk
        # 候选路径
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", icon_name),
        ]
        # EXE 模式：sys._MEIPASS 临时目录
        if hasattr(sys, '_MEIPASS'):
            candidates.insert(0, os.path.join(sys._MEIPASS, "icons", icon_name))
        # EXE 同目录
        if getattr(sys, 'frozen', False):
            candidates.insert(0, os.path.join(os.path.dirname(sys.executable), "icons", icon_name))

        for path in candidates:
            if os.path.isfile(path):
                img = Image.open(path).convert("RGBA")
                w, h = img.size
                # 按 height 等比缩放（保持圆角不变形）
                if h > size:
                    ratio = size / h
                    img = img.resize((max(1, int(w * ratio)), size), Image.LANCZOS)
                return ImageTk.PhotoImage(img)
    except Exception as e:
        print(f"[PlatformIcon {icon_name}] 加载失败: {e}")
    return None


def _get_app_icon_path() -> str:
    """获取 app_icon.ico 的实际路径（兼容 EXE 和源码运行）"""
    candidates = []
    # 1. EXE 同目录（便携部署）
    if getattr(sys, 'frozen', False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "app_icon.ico"))
    # 2. PyInstaller _MEIPASS 临时目录
    if hasattr(sys, '_MEIPASS'):
        candidates.append(os.path.join(sys._MEIPASS, "app_icon.ico"))
    # 3. 源码根目录
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def _apply_gold_icon(window):
    """为 tkinter 窗口设置金色 LOGO 图标（同时设置任务栏图标 + 标题栏图标）"""
    icon_path = _get_app_icon_path()
    if not icon_path:
        return
    try:
        # Windows: 用 iconbitmap 设置图标（同时影响标题栏和任务栏）
        window.iconbitmap(default=icon_path)
    except Exception:
        pass
    try:
        # Windows 10+ 任务栏图标需要额外设置（taskbar icon）
        import ctypes
        hwnd = window.winfo_id()
        if hwnd:
            # WM_SETICON = 0x0080
            # ICON_SMALL = 0, ICON_BIG = 1
            ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, 0)
            ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, 0)
    except Exception:
        pass


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
    """查找微信视频号下载工具 EXE：优先 2.8，回退 2.6。

    v8.3.7: 查找路径 EXE 同目录 → _MEIPASS → _get_app_cache_dir (EXE 同目录优先) → APPDATA
    """
    candidates = ("微信视频号下载工具2.8.exe", "微信视频号下载工具2.6.exe")
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    meipass_base = sys._MEIPASS if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS') else ""
    appdata_dir = os.path.join(os.environ.get("APPDATA", ""), "LiveStreamFetcher")
    cached_dir = _get_app_cache_dir("wechat_video_tool")

    for exe_name in candidates:
        for base in [exe_dir, meipass_base, cached_dir, appdata_dir]:
            if not base:
                continue
            p = os.path.join(base, exe_name) if base == exe_dir or base == meipass_base else os.path.join(base, exe_name)
            if os.path.isfile(p):
                return p
    return None


def _extract_embedded_wechat_video_tool():
    """从 _MEIPASS 释放微信视频号下载工具到缓存目录（v8.3.7：EXE 同目录优先）。"""
    if not getattr(sys, 'frozen', False) or not hasattr(sys, '_MEIPASS'):
        return None
    src_dir = os.path.join(sys._MEIPASS, "wechat_video_tool")
    if not os.path.isdir(src_dir):
        return None
    dst_dir = _get_app_cache_dir("wechat_video_tool")
    # 检查已释放版本（任一版本即跳过）
    for exe_name in ("微信视频号下载工具2.8.exe", "微信视频号下载工具2.6.exe"):
        cached = os.path.join(dst_dir, exe_name)
        if os.path.isfile(cached):
            return cached
    print("[视频号工具] 首次运行，正在释放到本地...")
    try:
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        # 返回找到的最高版本
        for exe_name in ("微信视频号下载工具2.8.exe", "微信视频号下载工具2.6.exe"):
            p = os.path.join(dst_dir, exe_name)
            if os.path.isfile(p):
                print(f"[视频号工具] 释放完成: {p}")
                return p
        return None
    except Exception as e:
        print(f"[视频号工具] 释放失败: {e}")
        return None


def _ensure_wechat_video_tool():
    """确保微信视频号下载工具可用：查找 → 释放。返回 EXE 路径或 None。"""
    path = _find_wechat_video_tool()
    if path:
        return path
    return _extract_embedded_wechat_video_tool()


def _install_wechat_certificates(cert_dir: str) -> bool:
    """安装视频号工具的 mitmproxy CA 证书到 Windows 证书存储。

    视频号工具（mitmproxy-based）抓 HTTPS 视频需要把两个 .p12 装到：
    - 「受信任的根证书颁发机构」（Root）—— 让 Windows 信任 mitmproxy CA
    - 「个人」（My）—— 让 mitmproxy 能用私钥签发伪造证书

    v8.3.2 实现路径：
    1. 用 Python `cryptography` 库从 .p12 提取出 X.509 证书对象
    2. 序列化为 DER
    3. 用 ctypes 调 Windows CertAddEncodedCertificateToStore() 加到 Root + My

    避开 certutil CRYPT_E_SELF_SIGNED / PowerShell SecureString UI / OpenSSL 缺失等问题。
    """
    if sys.platform != "win32":
        return True

    p12_files = [
        os.path.join(cert_dir, "证书.p12"),
        os.path.join(cert_dir, "证书-cert.p12"),
    ]
    p12_files = [p for p in p12_files if os.path.isfile(p)]
    if not p12_files:
        print("[视频号证书] 未找到证书文件")
        return False

    results = []
    for p12 in p12_files:
        try:
            results.append(_install_one_p12_via_crypto(p12))
        except Exception as e:
            print(f"[视频号证书] {os.path.basename(p12)} ❌ 异常: {e}")
            results.append(False)
    return all(results)


def _install_one_p12_via_crypto(p12_path: str) -> bool:
    """从 .p12 提取所有证书，用 ctypes Windows API 添加到 Root + My store。"""
    import ctypes
    from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
    from cryptography.hazmat.backends import default_backend

    HCERTSTORE = ctypes.c_void_p
    CERT_STORE_ADD_REPLACE_EXISTING = 0x00000004

    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    crypt32.CertOpenSystemStoreW.restype = HCERTSTORE
    crypt32.CertOpenSystemStoreW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    crypt32.CertCloseStore.restype = ctypes.c_bool
    crypt32.CertCloseStore.argtypes = [HCERTSTORE, ctypes.c_uint]
    crypt32.CertAddEncodedCertificateToStore.restype = ctypes.c_bool
    crypt32.CertAddEncodedCertificateToStore.argtypes = [
        HCERTSTORE,
        ctypes.c_uint,                 # CERT_ENCODING = X509_ASN_ENCODING | PKCS_7_ASN_ENCODING
        ctypes.c_char_p,               # pbCertEncoded
        ctypes.c_uint,                 # cbCertEncoded
        ctypes.c_uint,                 # dwAddDisposition
        ctypes.c_void_p,               # ppCertContext (NULL OK)
    ]

    # 1. Python cryptography 解析 .p12（mitmproxy CA 密码为空）
    with open(p12_path, "rb") as f:
        p12_bytes = f.read()
    try:
        private_key, certificate, additional_certificates = (
            pkcs12.load_key_and_certificates(p12_bytes, password=None, backend=default_backend())
        )
    except Exception as e:
        # 密码不对：mitmproxy 默认是空，但有些版本会改
        try:
            private_key, certificate, additional_certificates = (
                pkcs12.load_key_and_certificates(p12_bytes, password=b"mitmproxy", backend=default_backend())
            )
        except Exception as e2:
            print(f"[视频号证书] P12 解析失败: {e2}")
            return False

    # 收集所有证书：主证书 + 链证书
    certs = []
    if certificate is not None:
        certs.append(certificate)
    if additional_certificates:
        certs.extend(additional_certificates)

    # 2. 逐个证书 DER 编码 → ctypes 加到 Root + My
    added_count = 0
    for cert_obj in certs:
        der_bytes = cert_obj.public_bytes(Encoding.DER)
        der_buf = ctypes.create_string_buffer(der_bytes, len(der_bytes))
        X509_ASN_ENCODING = 0x00000001
        for store_name in ("Root", "My"):
            target = crypt32.CertOpenSystemStoreW(None, store_name)
            if not target:
                print(f"[视频号证书] {store_name} 打开失败 err={ctypes.get_last_error():#x}")
                continue
            try:
                ok = crypt32.CertAddEncodedCertificateToStore(
                    target,
                    X509_ASN_ENCODING,
                    der_buf,
                    len(der_bytes),
                    CERT_STORE_ADD_REPLACE_EXISTING,
                    None,
                )
                if ok:
                    added_count += 1
                else:
                    err = ctypes.get_last_error()
                    print(f"[视频号证书] {store_name} add 失败 err={err:#x}")
            finally:
                crypt32.CertCloseStore(target, 0)
    ok = added_count >= len(certs) * 2  # Root + My 两个 store，每个 cert 都要加上
    print(f"[视频号证书] {os.path.basename(p12_path)} {'✅' if ok else '⚠️'} cert={len(certs)} added={added_count}")
    return ok


def is_wechat_certificates_installed(cert_dir: str = None) -> bool:
    """检查视频号证书是否已安装（Root CA store 里有 mitmproxy 证书）。"""
    if sys.platform != "win32":
        return True
    cert_dir = cert_dir or os.path.join(
        os.environ.get("APPDATA", ""), "LiveStreamFetcher", "wechat_video_tool"
    )
    p12_files = [
        os.path.join(cert_dir, "证书.p12"),
        os.path.join(cert_dir, "证书-cert.p12"),
    ]
    if not all(os.path.isfile(p) for p in p12_files):
        return False
    try:
        import ctypes
        crypt32 = ctypes.WinDLL("crypt32.dll")
        store = crypt32.CertOpenSystemStoreW(None, "Root")
        if not store:
            return False
        try:
            crypt32.CertEnumCertificatesInStore.restype = ctypes.c_void_p
            crypt32.CertEnumCertificatesInStore.argtypes = [ctypes.c_void_p]
            ctx = crypt32.CertEnumCertificatesInStore(store)
            while ctx:
                # CERT_FRIENDLY_NAME_PROP_ID = 0x01
                buf = (ctypes.c_char * 1024)()
                size = ctypes.c_uint(1024)
                if crypt32.CertGetCertificateContextProperty(
                    ctx, 0x01, buf, ctypes.byref(size)
                ):
                    try:
                        name = buf.value.decode("utf-16-le", errors="ignore")
                        if "mitmproxy" in name.lower() or "charles" in name.lower():
                            return True
                    except Exception:
                        pass
                ctx = crypt32.CertEnumCertificatesInStore(store)
            return False
        finally:
            crypt32.CertCloseStore(store, 0)
    except Exception:
        return False


def _extract_embedded_ffmpeg():
    """从 PyInstaller _MEIPASS 释放 ffmpeg.exe 到缓存目录（v8.3.7：EXE 同目录优先）

    仅在首次运行时执行。返回 ffmpeg.exe 路径，失败返回 None。
    """
    if not getattr(sys, 'frozen', False) or not hasattr(sys, '_MEIPASS'):
        return None

    src_dir = os.path.join(sys._MEIPASS, "embedded_ffmpeg")
    if not os.path.isdir(src_dir):
        return None

    dst_dir = _get_app_cache_dir("embedded_ffmpeg")

    # 已存在则不重复释放
    if os.path.isfile(os.path.join(dst_dir, "ffmpeg.exe")):
        return os.path.join(dst_dir, "ffmpeg.exe")

    print(f"[ffmpeg] 首次运行，正在释放嵌入式 ffmpeg 到本地 → {dst_dir}")
    try:
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

        v8.3.3: 端口冲突自动重试。若指定端口被占用，自动递增探测下一个空闲
        端口（最多 100 次）；port=0 时由 OS 自动分配空闲端口，永不冲突。

        Args:
            target_url: 淘宝 alicdn 的原始流链接

        Returns:
            本地代理 URL，如 http://127.0.0.1:18888/live
        """
        self._target_url = target_url
        self._bytes_served = 0

        # 端口冲突自动重试（v8.3.3）
        server = None
        last_err = None
        port = self._port
        for _attempt in range(100):
            try:
                server = _StreamProxyHTTPServer(("127.0.0.1", port), self._handle_request)
                break
            except OSError as e:
                last_err = e
                if port == 0:
                    # 随机端口都失败，说明系统 socket 资源耗尽，直接抛
                    break
                # 指定端口被占用 → 递增探测下一个空闲端口
                port += 1

        if server is None:
            raise OSError(f"无法分配本地代理端口（从 {self._port} 起尝试了 100 个）: {last_err}")

        self._server = server
        self._actual_port = server.server_address[1]
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
        """停止代理服务（v8.3.3：彻底释放端口）。

        顺序：
        1. 置 _running=False，停止所有流式转发循环
        2. 终止所有 ffmpeg 子进程（terminate → 超时 kill）
        3. 关闭 server socket（释放监听端口）
        4. join serve_forever 线程（确保线程退出、socket 完全释放）
        """
        self._running = False

        # 终止 ffmpeg 子进程
        if self._ffmpeg_process:
            proc = self._ffmpeg_process
            self._ffmpeg_process = None
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass

        # 关闭 server socket（释放端口）
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None

        # join serve_forever 线程，确保 socket 完全释放（最多等 2 秒）
        if self._thread is not None and self._thread.is_alive():
            try:
                self._thread.join(timeout=2)
            except Exception:
                pass
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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
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
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
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
        self.server_socket = None

        # 在 __init__ 中就完成 bind，这样 server_address 属性立即可用
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # v8.3.3: 去掉 SO_REUSEADDR。Windows 上 SO_REUSEADDR 允许两个 socket 绑定
        # 同一端口（导致端口冲突检测失效、流量被随机分发），而且 LISTEN socket
        # 关闭后不会进入 TIME_WAIT，无需 SO_REUSEADDR 复用。
        # 去掉后：端口被占用时 bind 会抛 OSError(10048)，触发 LocalStreamProxy.start()
        # 的自动重试逻辑；关闭后端口立即释放，可立即重启同一端口。
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
        """关闭服务器并释放监听端口（v8.3.3：加 SHUT_RDWR + 置 None）。"""
        self._running = False
        if self.server_socket:
            sock = self.server_socket
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
            self.server_socket = None

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
        self.root.title("影视匠直播流获取 v8.2.8 — by LONGSHAO")
        self.root.geometry("900x700")  # v7.6.25 缩小窗口
        self.root.minsize(800, 620)
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
        """v7.6 全新 UI 重构（参考截图：深色仪表盘 + 侧边栏 + Hero 渐变 + 平台网格）

        布局：
        ┌─ sidebar 200px ─┬─ main ──────────────────────────────┐
        │ LOGO            │ 顶部栏（搜索 + 状态）                  │
        │ ─── 工具集 ─── │ ──── Hero 渐变卡片 ────               │
        │ 菜单项 5项      │  📦 专业直播工具集                    │
        │ ─── 系统设置 ─ │  一站式多平台直播流解析                │
        │ 🟢 云端已连    │  5 工具 | 4 已安装 | 0 更新 | 0 通知   │
        │                │ ──── 平台网格 2 列 ────                │
        │                │  [抖音] [快手]                         │
        │                │  [小红书] [淘宝]                       │
        │                │  [YY] [视频号]                         │
        │                │ ──── 解析输入区 ────                  │
        │                │  URL 输入 + 4 个主按钮                 │
        │                │ ──── 结果区（可滚动）────              │
        └────────────────┴──────────────────────────────────────┘
        """
        self.root.configure(bg=Colors.BG_DARK)

        # ═══ 主容器（左右分栏）═══
        main_container = tk.Frame(self.root, bg=Colors.BG_DARK)
        main_container.pack(fill="both", expand=True)

        # ═══ 左侧 sidebar（v7.6.16 已移除 LOGO，保留细边）═══
        self._build_sidebar(main_container)

        # ═══ 右侧主内容区 ═══
        content = tk.Frame(main_container, bg=Colors.BG_DARK)
        content.pack(side="left", fill="both", expand=True)

        # ── 顶部 LOGO 栏（v7.6.16 新增：从侧边栏移到主内容顶部）──
        self._build_top_logo_bar(content)

        # ── 可滚动主区（平台网格 + 输入 + 结果）──
        scroll_wrapper = tk.Frame(content, bg=Colors.BG_DARK)
        scroll_wrapper.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        # Canvas 滚动
        self.main_canvas = tk.Canvas(
            scroll_wrapper, bg=Colors.BG_DARK,
            highlightthickness=0, bd=0,
        )
        main_scrollbar = _make_dark_scrollbar(
            scroll_wrapper, command=self.main_canvas.yview,
        )
        self.main_inner = tk.Frame(self.main_canvas, bg=Colors.BG_DARK)
        self.main_inner.bind(
            "<Configure>",
            lambda e: self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))
        )
        self._main_canvas_window = self.main_canvas.create_window(
            (0, 0), window=self.main_inner, anchor="nw"
        )
        self.main_canvas.configure(yscrollcommand=main_scrollbar.set)
        self.main_canvas.bind(
            "<Configure>",
            lambda e: self.main_canvas.itemconfig(self._main_canvas_window, width=e.width)
        )
        # 滚轮
        self.main_canvas.bind("<Enter>", self._bind_main_mousewheel)
        self.main_canvas.bind("<Leave>", self._unbind_main_mousewheel)

        main_scrollbar.pack(side="right", fill="y")
        self.main_canvas.pack(side="left", fill="both", expand=True)

        # ── Hero 渐变卡片 ──
        self._build_hero(self.main_inner)

        # ── 4 个统计指标 ──
        self._build_stats_row(self.main_inner)

        # ── 平台网格（2 列）──
        self._build_platform_grid(self.main_inner)

        # ── 解析输入区 ──
        self._build_input_section(self.main_inner)

        # ── 结果区（也可滚动）──
        self._build_result_section(self.main_inner)

        # ═══ 底部状态栏（永久显示在底部）═══
        self._build_status_bar(self.root)

        # 启动后台任务
        self._init_background_tasks()

    # ─── 侧边栏 ───
    def _build_sidebar(self, parent):
        """v7.6.16 左侧细边栏：移除 LOGO（已移到顶部），仅保留视觉边。"""
        sidebar = tk.Frame(parent, bg=Colors.BG_SIDEBAR, width=2)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

    def _build_top_logo_bar(self, parent):
        """v7.6.16 顶部 LOGO 栏：影视匠 工具箱 LOGO 从侧边栏移到主内容顶部。

        设计：
        - 64px 高，圆角 LOGO 图标 + "影视匠 工具箱" 文字
        - LOGO 居左显示，点击可打开 GitHub
        - 整条 LOGO 栏在平台网格之上
        """
        bar = tk.Frame(parent, bg=Colors.BG_DARK, height=56)  # v7.6.25 64→56
        bar.pack(fill="x", padx=20, pady=(12, 2))  # v7.6.25 减少 padding
        bar.pack_propagate(False)

        # 加载 LOGO（缓存到 self 避免 GC）
        self._logo_image, _ = _load_gold_logo(max_height=36)  # v7.6.25 40→36
        if self._logo_image is not None:
            logo_lbl = tk.Label(bar, image=self._logo_image,
                                bg=Colors.BG_DARK, cursor="hand2")
            logo_lbl.image = self._logo_image  # 防止 GC
            logo_lbl.pack(side="left", pady=12)
            logo_lbl.bind("<Button-1>",
                          lambda e: self._open_url_with_chromium("https://github.com/Dragon617"))
        else:
            # fallback: 文字 + 符号
            tk.Label(bar, text="◆", font=("Segoe UI Symbol", 26, "bold"),
                     bg=Colors.BG_DARK, fg=Colors.GOLD_PRIMARY).pack(side="left", padx=(8, 0), pady=12)

        # 直播流获取工具 文字（v7.6.25 - 缩小为 28pt 适配窗口）
        gold_text_img = _render_gold_gradient_text(
            "直播流获取工具", font_size=28, bold=True, char_spacing=3,
            cache_key=("logo_text", 28, True, 3)
        )
        if gold_text_img is not None:
            gold_lbl = tk.Label(bar, image=gold_text_img, bg=Colors.BG_DARK)
            gold_lbl.image = gold_text_img
            gold_lbl.pack(side="left", padx=(14, 0), pady=2)
        else:
            # fallback: 单色
            tk.Label(
                bar, text="直播流获取工具", font=("Microsoft YaHei UI", 22, "bold"),
                bg=Colors.BG_DARK, fg=Colors.GOLD_PRIMARY
            ).pack(side="left", padx=(14, 0), pady=16)

        # 右侧装饰：版本号（v8.2.7 同步）
        tk.Label(
            bar, text="v8.2.8", font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_DARK, fg=Colors.GOLD_PRIMARY
        ).pack(side="right", padx=(0, 4), pady=22)

    def _build_sidebar_menu_item(self, parent, icon, name, color, active=False):
        """[v7.6.3 已弃用] 侧边栏菜单项。"""
        pass

    # ─── 顶部栏（v7.6.3 已删除）───
    def _build_topbar(self, parent):
        """顶部栏已删除（v7.6.3）—— 不再显示搜索框 / 通知 / 状态徽章。"""
        pass

    # ─── Hero 渐变卡片 ───
    def _build_hero(self, parent):
        """Hero 渐变卡片（v7.6.4 已删除）—— 不再显示。"""
        pass

    def _fill_hero_content(self, hero_outer):
        """[已弃用] Hero 内容填充。"""
        pass

    # ─── 4 个统计指标 ───
    def _build_stats_row(self, parent):
        """4 个圆角统计指标（v7.6.5 已删除）—— 不再显示。"""
        pass

    def _fill_stat_content(self, stat_outer, c1, value, label):
        """[已弃用] 统计指标内容填充。"""
        pass

    # ─── 平台网格 ───
    def _build_platform_grid(self, parent):
        """v7.6.14 单行 5 平台网格（抖音/快手/淘宝/小红书/YY）。

        设计：单行水平排列，每张卡 = Uiverse 图标展开风格按钮。
        正常：只显示平台 icon（26x26）。
        hover：icon + 平台名（"抖音"/"快手"/"淘宝"/"小红书"/"YY"）。
        视频号不再放这里（在动作按钮区作为「视频号工具」按钮）。
        """
        # 单行 5 列
        grid = tk.Frame(parent, bg=Colors.BG_DARK)
        grid.pack(fill="x")
        for i in range(5):
            grid.columnconfigure(i, weight=1, uniform="col")

        # 5 个平台（不含视频号）
        platforms = ["dy", "ks", "tb", "xhs", "yy"]
        self._platform_cards = {}
        for i, key in enumerate(platforms):
            card = self._build_platform_card(grid, key)
            card.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else 2, 2 if i < 4 else 0), pady=2)  # v7.6.25b gap 4→2
            self._platform_cards[key] = card

    def _build_platform_card(self, parent, key):
        """单张平台卡片（v7.6.35 集成登录状态 + 智能点击）。"""
        meta = PLATFORM_META[key]
        display_name = meta.get("short", meta["name"])
        icon_path = meta.get("icon_file", "")

        # 检测登录状态（如果 login_func 存在）
        login_status = "never"
        login_func_name = meta.get("login_func")
        if login_func_name:
            try:
                login_func = globals().get(login_func_name)
                if login_func:
                    login_status = login_func()
            except Exception:
                pass

        card = _IconExpandButton(
            parent,
            text=display_name,
            icon_path=icon_path,
            icon_size=26,
            command=lambda k=key: self._on_platform_smart_click(k),
            width=170, height=40,
            font=("Microsoft YaHei UI", 13, "bold"),
            login_status=login_status,
        )
        # 保存登录状态引用
        if not hasattr(self, '_platform_login_statuses'):
            self._platform_login_statuses = {}
        self._platform_login_statuses[key] = login_status
        return card

    def _on_platform_smart_click(self, key):
        """v7.6.39 智能点击：始终打开对应平台 URL（内置 persistent 浏览器）。

        - 未登录：打开登录页
        - 已登录：打开平台主页
        - 用户行为：点击 = 跳转到对应平台
        """
        meta = PLATFORM_META[key]
        target_url = meta.get("url")
        if not target_url:
            return

        login_url = meta.get("login_url")
        login_status = self._platform_login_statuses.get(key, "never")

        # 已登录：打开平台主页；未登录：打开登录页
        if login_status == "logged_in" or not login_url:
            url_to_open = target_url
            self.status_var.set(f"正在打开 {meta['name']}...")
        else:
            url_to_open = login_url
            self.status_var.set(f"正在打开 {meta['name']} 登录页，请扫码登录...")

        # 在内置 persistent 浏览器打开（cookie 共享）
        self.root.after(0, lambda: self._open_platform_url(key, url_to_open))

    def _open_platform_url(self, key, url):
        """v7.6.39 在内置 persistent 浏览器打开平台 URL。"""
        try:
            self._open_persistent_url(key, url, login_url="")
        except Exception as e:
            print(f"[Platform] 打开 {key} URL 失败: {e}")
            self.status_var.set(f"打开 {PLATFORM_META[key]['name']} 失败")

    def _refresh_single_platform_status(self, key):
        """v7.6.35 刷新单个平台的登录状态并更新卡片。"""
        meta = PLATFORM_META[key]
        login_func_name = meta.get("login_func")
        if not login_func_name:
            return
        try:
            login_func = globals().get(login_func_name)
            if login_func:
                new_status = login_func()
                self._platform_login_statuses[key] = new_status
                # 更新卡片显示
                if key in self._platform_cards:
                    self._platform_cards[key].set_login_status(new_status)
                if new_status == "logged_in":
                    self.status_var.set(f"✅ {meta['name']} 已登录")
        except Exception as e:
            print(f"[Login] 刷新 {key} 状态失败: {e}")

    def _on_platform_parse(self, key):
        """平台卡片的"解析直播流"按钮。"""
        meta = PLATFORM_META[key]
        self.status_var.set(f"请在下方输入{meta['name']}直播间链接后点击「获取流链接」")
        # 自动聚焦到 URL 输入框
        if hasattr(self, "url_entry"):
            self.url_entry.focus_set()
        # 如果有 URL，自动填充
        url = meta.get("url", "")
        if url and not self.url_var.get():
            self.url_var.set(url)

    def _on_platform_quick_access(self, key):
        """平台卡片的"打开"按钮。"""
        meta = PLATFORM_META[key]
        if key in ("dy", "ks", "xhs", "tb"):
            # persistent 浏览器（与登录状态共享 cookie）
            self._open_persistent_url(key, meta["url"])
        else:
            # yy / wechat 普通浏览器
            self._open_url_with_chromium(meta["url"])
        self.status_var.set(f"已打开 {meta['name']}")

    # ─── 解析输入区 ───
    def _build_input_section(self, parent):
        """URL 输入 + 4 个主操作按钮（截图中部解析区）。"""
        input_outer = RoundedFrame(parent, radius=14, fill=Colors.BG_CARD,
                                    border=Colors.BORDER, border_width=1)
        input_outer.pack(fill="x", pady=(20, 0))

        inp = input_outer.inner
        inp.configure(bg=Colors.BG_CARD, padx=0, pady=0)
        # v7.6.33: 关闭内部 padx/pady，由各子 section 自己控制 padding

        # ── Section 标题区（v7.6.33 重构：加状态徽章 + 装饰条）──
        title_outer = tk.Frame(inp, bg=Colors.BG_CARD, height=46)
        title_outer.pack(fill="x")
        title_outer.pack_propagate(False)

        # 标题区左侧装饰条（金色 3px 高线 - 模拟左侧 accent bar）
        title_accent = tk.Frame(title_outer, bg=Colors.GOLD_PRIMARY, width=3)
        title_accent.pack(side="left", fill="y", padx=(0, 12), pady=10)

        # 标题区内容
        title_content = tk.Frame(title_outer, bg=Colors.BG_CARD)
        title_content.pack(side="left", fill="both", expand=True, pady=12)

        # 标题文字
        tk.Label(
            title_content, text="🎬  直播间链接解析",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=Colors.BG_CARD, fg=Colors.GOLD_PRIMARY,
        ).pack(side="left")

        # "·" 分隔符
        tk.Label(
            title_content, text="  ·  ",
            font=("Microsoft YaHei UI", 10),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        ).pack(side="left")

        # 支持平台数
        self._platform_hint = tk.Label(
            title_content, text="支持 6 大平台",
            font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
        )
        self._platform_hint.pack(side="left")

        # 状态徽章（右侧） - v7.6.33 新增
        status_pill = tk.Frame(
            title_outer, bg=Colors.BG_INPUT,
            padx=10, pady=4,
        )
        status_pill.pack(side="right", padx=(0, 16), pady=14)
        # 状态点
        status_dot = tk.Label(
            status_pill, text="●",
            font=("Consolas", 11, "bold"),
            bg=Colors.BG_INPUT, fg=Colors.ACCENT_GREEN,
        )
        status_dot.pack(side="left", padx=(0, 4))
        # 状态文字
        status_text = tk.Label(
            status_pill, text="6 平台就绪",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=Colors.BG_INPUT, fg=Colors.ACCENT_GREEN,
        )
        status_text.pack(side="left")

        # 分隔线（标题与 URL 输入之间）
        separator = tk.Frame(inp, bg=Colors.BORDER, height=1)
        separator.pack(fill="x", padx=18)

        # ── URL 输入框（v7.6.31 简洁方形 - 回归最简设计）──
        # 设计：简单矩形 + 1px 浅色边框 + 焦点时金色边框
        #   - Frame bg=BG_INPUT（深色输入底）
        #   - 边框：1px BORDER（灰色）
        #   - 焦点：border 变 GOLD + 2px
        #   - 左：🔗 icon（gold）
        #   - 中：Entry
        #   - 右：✕ 清除（有内容时显示，悬停变红）

        # 外层 Frame（带边框颜色 + 焦点时切换）
        url_frame = tk.Frame(inp, bg=Colors.BORDER, height=44)  # 1px 边框色
        url_frame.pack(fill="x", padx=18, pady=(14, 18))  # v7.6.33: 加左右 padding 和垂直 padding
        url_frame.pack_propagate(False)
        self._url_frame = url_frame  # 保存供 focus 切换

        # 内层 Frame（实际输入区域，1px 内边距 = 边框厚度）
        url_inner = tk.Frame(url_frame, bg=Colors.BG_INPUT)
        url_inner.pack(fill="both", expand=True, padx=1, pady=1)
        url_inner.pack_propagate(False)
        url_inner.configure(height=42)

        # 左侧搜索 icon
        icon_lbl = tk.Label(
            url_inner, text="🔗",
            font=("Segoe UI Emoji", 12),
            bg=Colors.BG_INPUT, fg=Colors.GOLD_PRIMARY,
        )
        icon_lbl.pack(side="left", padx=(8, 4), pady=0)  # v7.6.32: 减少左边距 14→8

        # Entry
        self.url_var = tk.StringVar()
        self.url_var.trace_add("write", self._on_url_change)
        self.url_entry = tk.Entry(
            url_inner, textvariable=self.url_var,
            font=("Consolas", 10),
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.GOLD_PRIMARY,
            selectbackground=Colors.GOLD_PRIMARY,
            selectforeground="#0a0e1a",
            relief="flat", bd=0, highlightthickness=0,
        )
        self.url_entry.pack(side="left", fill="x", expand=True,
                            padx=(0, 4), pady=10)
        # 占位 Label（覆盖在 entry 位置 - 垂直居中）
        self._url_placeholder = "粘贴或输入直播间链接..."
        self._url_placeholder_lbl = tk.Label(
            url_inner, text=self._url_placeholder,
            font=("Consolas", 10),
            bg=Colors.BG_INPUT, fg=Colors.TEXT_MUTED,
        )
        # v7.6.32: 减少左 padding 46→30
        # v7.6.33: 占位 label 高度从 40 改为 42 匹配新的 url_inner 高度
        self._url_placeholder_lbl.place(x=30, y=0, anchor="nw",
                                          width=300, height=42)

        # 焦点事件
        self.url_entry.bind("<FocusIn>", self._on_url_focus_in)
        self.url_entry.bind("<FocusOut>", self._on_url_focus_out)

        # 右侧清除按钮 ✕
        self._url_clear_btn = tk.Label(
            url_inner, text="✕",
            font=("Arial", 11, "bold"),
            bg=Colors.BG_INPUT, fg=Colors.TEXT_SECONDARY,
            cursor="hand2", padx=8,
        )
        # 初始不显示（_on_url_change 会根据 url 内容控制）
        self._url_clear_btn.bind("<Button-1>", self._clear_url)
        self._url_clear_btn.bind("<Enter>",
            lambda e: self._url_clear_btn.configure(fg=Colors.ACCENT_RED))
        self._url_clear_btn.bind("<Leave>",
            lambda e: self._url_clear_btn.configure(fg=Colors.TEXT_SECONDARY))
        # 默认隐藏（无内容时）
        # 初始时不显示（_on_url_change 会根据 url_var 状态控制）

        # 主操作按钮行（v7.6.28 加入"代理设置"按钮）
        btn_row = tk.Frame(inp, bg=Colors.BG_CARD)
        btn_row.pack(fill="x", pady=(10, 0))

        # 获取流链接（暗金色）v7.6.26: 大幅缩小
        self.fetch_btn = _PressButton3D(
            btn_row, text="获取流链接",
            icon_text="⚡", command=self._on_fetch,
            width=110, height=32, icon_size=12, font=("Microsoft YaHei UI", 10, "bold"),
            color_top="#a17a1a", color_bottom="#5c4308",
        )
        self.fetch_btn.pack(side="left", padx=(0, 4))

        # HEVC 转码（暗紫色）v7.6.26
        self.transcode_btn = _PressButton3D(
            btn_row, text="HEVC转码",
            icon_text="🎬", command=self._on_transcode_click,
            width=95, height=32, icon_size=12, font=("Microsoft YaHei UI", 10, "bold"),
            color_top="#4c3a8a", color_bottom="#2e1f5c",
        )
        self.transcode_btn.pack(side="left", padx=(0, 4))

        # v7.6.28 代理设置按钮（移到动作按钮行 - 介于 HEVC转码 和 复制全部 之间）
        # 用 _PressButton3D 风格，颜色与原 tk.Label 一致（更突出）
        self._proxy_settings_btn = _PressButton3D(
            btn_row, text="代理设置",
            icon_text="🔧", command=self._toggle_proxy_from_btn,
            width=95, height=32, icon_size=12, font=("Microsoft YaHei UI", 10, "bold"),
            color_top="#5c5c8a", color_bottom="#2e2e4c",  # 紫灰色（与代理主题一致）
        )
        self._proxy_settings_btn.pack(side="left", padx=(0, 4))

        # 复制全部（暗蓝色）v7.6.26
        self.copy_all_btn = _PressButton3D(
            btn_row, text="复制全部",
            icon_text="📋", command=self._on_copy_all,
            width=95, height=32, icon_size=12, font=("Microsoft YaHei UI", 10, "bold"),
            color_top="#1e3a6e", color_bottom="#0c1d3a",
        )
        self.copy_all_btn.pack(side="left", padx=(0, 4))

        # 系统代理（暗青色）v7.6.26
        self.proxy_toggle_btn = _PressButton3D(
            btn_row, text="系统代理",
            icon_text="🌐", command=self._on_toggle_system_proxy,
            width=95, height=32, icon_size=12, font=("Microsoft YaHei UI", 10, "bold"),
            color_top="#0e6e7c", color_bottom="#053944",
        )
        self.proxy_toggle_btn.pack(side="left", padx=(0, 4))

        # 视频号工具（暗绿色）v7.6.26
        self.wct_btn = _PressButton3D(
            btn_row, text="视频号工具",
            icon_path="wechat.png", icon_size=12,
            command=self._on_open_wechat_video_tool,
            width=110, height=32, font=("Microsoft YaHei UI", 10, "bold"),
            color_top="#1a6e3a", color_bottom="#083820",
        )
        self.wct_btn.pack(side="right")

        # 代理设置（折叠式 - v7.6.28 移到动作按钮行）
        # 先创建代理输入框（默认隐藏），toggle 按钮放在 btn_row 中
        self._build_proxy_frame(inp)
        self.root.after(500, self._refresh_proxy_btn_state)

    def _build_proxy_frame(self, parent):
        """v7.6.28 代理设置 - 折叠式（toggle 按钮由 _build_input_section 创建并放置到 btn_row 中）。

        这里只创建：
        1. 代理输入框（默认隐藏，self.proxy_frame）
        2. self._proxy_toggle_lbl（toggle 按钮，供 _build_input_section pack 到 btn_row）
        """
        # 1. 代理输入框（独立 frame，pack 到 inp 底部，默认隐藏）
        self.proxy_frame = tk.Frame(parent, bg=Colors.BG_CARD)

        tk.Label(self.proxy_frame, text="代理地址：",
                 font=("Microsoft YaHei UI", 9),
                 bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY).pack(
            side="left", padx=(0, 6), pady=(8, 0))
        self.proxy_var = tk.StringVar()
        proxy_container = tk.Frame(self.proxy_frame, bg=Colors.BG_INPUT)
        proxy_container.pack(side="left", fill="x", expand=True, pady=(8, 0))
        self.proxy_entry = tk.Entry(
            proxy_container, textvariable=self.proxy_var,
            font=("Consolas", 9),
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.GOLD_PRIMARY,
            relief="flat", bd=0, highlightthickness=0,
        )
        self.proxy_entry.pack(fill="x", expand=True, padx=8, pady=4)

        # 2. toggle 按钮（不在这里 pack，由 _build_input_section 放入 btn_row）
        self._proxy_toggle_lbl = tk.Label(
            parent, text="代理设置 ▸",
            font=("Microsoft YaHei UI", 9), cursor="hand2",
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        )
        self._proxy_toggle_lbl.bind("<Button-1>", self._toggle_proxy)
        # 代理状态用括号显示在 toggle 后面
        self._proxy_state_lbl = tk.Label(
            parent, text="（已关闭）",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        )

    # ─── 结果区 ───
    def _build_result_section(self, parent):
        """结果展示区（占位 / 流卡片）。"""
        result_outer = RoundedFrame(parent, radius=14, fill=Colors.BG_DARK,
                                     border=Colors.BORDER, border_width=0)
        result_outer.pack(fill="both", expand=True, pady=(16, 0))

        result_container = result_outer.inner
        result_container.configure(bg=Colors.BG_DARK)
        result_container.pack(fill="both", expand=True, pady=4)

        # Canvas + Scrollbar
        self.result_canvas = tk.Canvas(
            result_container, bg=Colors.BG_DARK,
            highlightthickness=0, bd=0,
        )
        result_scrollbar = _make_dark_scrollbar(
            result_container, command=self.result_canvas.yview,
        )
        self.result_inner = tk.Frame(self.result_canvas, bg=Colors.BG_DARK)

        self.result_inner.bind(
            "<Configure>",
            lambda e: self.result_canvas.configure(scrollregion=self.result_canvas.bbox("all"))
        )

        self._result_canvas_window = self.result_canvas.create_window(
            (0, 0), window=self.result_inner, anchor="nw"
        )
        self.result_canvas.configure(yscrollcommand=result_scrollbar.set)
        self.result_canvas.bind(
            "<Configure>",
            lambda e: self.result_canvas.itemconfig(self._result_canvas_window, width=e.width)
        )
        # 鼠标滚轮
        self.result_canvas.bind("<Enter>", self._bind_mousewheel)
        self.result_canvas.bind("<Leave>", self._unbind_mousewheel)

        result_scrollbar.pack(side="right", fill="y")
        self.result_canvas.pack(side="left", fill="both", expand=True)

    # ─── 状态栏（底部固定）───
    def _build_status_bar(self, parent):
        """底部状态栏（v7.6.7）：就绪状态 + 访客计数 + 日期。"""
        status_outer = RoundedFrame(parent, radius=0, fill=Colors.BG_SIDEBAR,
                                     border=Colors.BG_BORDER, border_width=0)
        status_outer.pack(side="bottom", fill="x")

        status = status_outer.inner
        status.configure(bg=Colors.BG_SIDEBAR, height=32)
        status.pack_propagate(False)

        # 左侧：状态指示
        self.status_icon = tk.Label(
            status, text="●", font=("Consolas", 8, "bold"),
            bg=Colors.BG_SIDEBAR, fg=Colors.STATUS_ONLINE,
        )
        self.status_icon.pack(side="left", padx=(16, 6), pady=8)
        self.status_var = tk.StringVar(value="系统就绪 — 粘贴直播间链接开始解析")
        tk.Label(status, textvariable=self.status_var,
                 font=("Microsoft YaHei UI", 9),
                 bg=Colors.BG_SIDEBAR, fg=Colors.TEXT_SECONDARY).pack(side="left", pady=8)

        # 右侧：访客计数（金色）
        self._visitor_count_label = tk.Label(
            status, text="本软件已访问 -- 次",  # v8.0.2 占位文本，避免一直显示"加载中"
            font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_SIDEBAR, fg=Colors.GOLD_PRIMARY, cursor="hand2",
        )
        self._visitor_count_label.pack(side="right", padx=(0, 16), pady=8)
        self._visitor_count_label.bind(
            "<Button-1>", lambda e: self._open_url_with_chromium(
                "https://cn.widgetstore.net/view/index.html"
                "?q=5b049cc8622189440f31d6307d40e568.b3c6c3d569de54420449a20254382ae6"
            )
        )

        # 右侧：日期
        import datetime
        today = datetime.datetime.now().strftime("%Y年%m月%d日")
        tk.Label(status, text=today,
                 font=("Microsoft YaHei UI", 9),
                 bg=Colors.BG_SIDEBAR, fg=Colors.TEXT_MUTED).pack(side="right", padx=(0, 12), pady=8)

    # ─── 滚轮绑定（主区）───
    def _bind_main_mousewheel(self, event):
        self.main_canvas.bind_all("<MouseWheel>", self._on_main_mousewheel)
        self.main_canvas.bind_all("<Button-4>", self._on_main_mousewheel_linux)
        self.main_canvas.bind_all("<Button-5>", self._on_main_mousewheel_linux)

    def _unbind_main_mousewheel(self, event):
        self.main_canvas.unbind_all("<MouseWheel>")
        self.main_canvas.unbind_all("<Button-4>")
        self.main_canvas.unbind_all("<Button-5>")

    def _on_main_mousewheel(self, event):
        self.main_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_main_mousewheel_linux(self, event):
        if event.num == 4:
            self.main_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.main_canvas.yview_scroll(1, "units")

    # ─── 初始化后台任务 ───
    def _init_background_tasks(self):
        """启动后台：访客计数 + 登录状态检测。"""
        # 访客计数
        threading.Thread(target=self._fetch_visitor_count_async, daemon=True).start()
        # 登录状态检测（4 平台）
        self.root.after(1000, self._refresh_all_login_status)


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
        """[已弃用] 旧版顶部大尺寸访客 WebView，已被状态栏文字计数器替代"""
        pass

    def _fetch_visitor_count_async(self):
        """后台线程：用 Playwright headless 提取访客计数（仅数字，免去大尺寸 WebView）"""
        widget_url = (
            "https://cn.widgetstore.net/view/index.html"
            "?q=5b049cc8622189440f31d6307d40e568"
            ".b3c6c3d569de54420449a20254382ae6"
        )

        # v8.0.2 缩短到 2 秒（主界面已先渲染，再等也意义不大）
        time.sleep(2)

        def _update_label(text):
            try:
                if self._visitor_count_label.winfo_exists():
                    self._visitor_count_label.configure(text=text)
            except Exception:
                pass

        try:
            from playwright.sync_api import sync_playwright

            browser_path = _ensure_chromium_ready()
            launch_args = {
                "headless": True,
                "args": [
                    "--no-sandbox", "--disable-gpu",
                    "--no-proxy-server",
                    "--disable-features=Translate,TranslateUI",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-extensions",
                    "--disable-default-apps",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--mute-audio",
                ],
            }
            if browser_path:
                exe_path = os.path.join(browser_path, "chrome.exe")
                if os.path.isfile(exe_path):
                    launch_args["executable_path"] = exe_path

            with sync_playwright() as p:
                browser = p.chromium.launch(**launch_args)
                page = browser.new_page(viewport={"width": 960, "height": 200})
                try:
                    page.goto(widget_url, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass
                time.sleep(1)  # v8.0.2 缩短到 1 秒（widget 渲染通常很快）

                # 用 JS 直接提取访客数字（比截图快 100 倍）
                # 初始 label 已经是 "-- 次"，无需中间更新
                for attempt in range(3):
                    try:
                        count = page.evaluate("""() => {
                            const text = document.body.innerText || '';
                            const m = text.match(/第\s*(\d+)\s*位访客/);
                            if (m) return m[1];
                            const m2 = text.match(/(\d{1,10})/);
                            return m2 ? m2[1] : null;
                        }""")
                        if count:
                            _update_label(f"本软件已访问 {count} 次")
                            break
                    except Exception:
                        pass
                    time.sleep(2)

                # 定期刷新（每 60 秒，仅重 eval，不重导航）
                while True:
                    time.sleep(60)
                    try:
                        count = page.evaluate("""() => {
                            const text = document.body.innerText || '';
                            const m = text.match(/第\s*(\d+)\s*位访客/);
                            return m ? m[1] : null;
                        }""")
                        if count:
                            _update_label(f"本软件已访问 {count} 次")
                    except Exception:
                        pass
        except Exception as e:
            _update_label("本软件已访问 -- 次")
            print(f"[访客] 后台获取失败: {e}")

    def _embed_browser_widget(self):
        """[已弃用] 旧版 Win32 嵌入方案，已被状态栏文字计数器替代"""
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
        """v8.0.2 使用项目封装的 Chromium 浏览器打开 URL，加固容错。
        
        健壮性：
        1. 优先用项目内置 chrome.exe（带 no-sandbox / no-gpu）
        2. 失败时降级到系统默认浏览器
        3. 任何阶段失败都有兜底，绝不静默吞错
        """
        import subprocess
        try:
            chrome_exe = _ensure_chromium_ready()
            if chrome_exe:
                exe_path = os.path.join(chrome_exe, "chrome.exe")
                if os.path.isfile(exe_path):
                    try:
                        subprocess.Popen(
                            [exe_path, "--no-first-run", "--no-default-browser-check",
                             "--no-sandbox", "--disable-gpu",
                             "--disable-software-rasterizer", url],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                        )
                        print(f"[内置浏览器] 已启动: {url}")
                        return
                    except Exception as e:
                        print(f"[内置浏览器] Popen 失败: {e}")
        except Exception as e:
            print(f"[内置浏览器] _ensure_chromium_ready 失败: {e}")

        try:
            import webbrowser
            if webbrowser.open(url):
                print(f"[系统浏览器] 已打开: {url}")
            else:
                print(f"[系统浏览器] webbrowser.open 返回 False: {url}")
        except Exception as e:
            print(f"[系统浏览器] 也失败: {e}")

    def _open_persistent_url(self, platform_key: str, url: str, login_url: str = ""):
        """用对应平台的 persistent_context 浏览器打开 URL（与登录状态共用一份数据）。

        Args:
            platform_key: 平台标识，'ks' | 'tb' | 'xhs' | 'dy' | 'yy' | 'wechat'
            url: 目标 URL
            login_url: 未登录时跳转的登录 URL（可选）
        """
        # 平台 → (data_dir, login_status_func, login_url) 映射
        platform_map = {
            "ks": (_get_ks_browser_data_dir(), _check_ks_login_status, "https://passport.kuaishou.com/pc/account/login"),
            "tb": (_get_tb_browser_data_dir(), _check_tb_login_status, "https://login.taobao.com/member/login.jhtml"),
            "xhs": (_get_xhs_browser_data_dir(), _check_xhs_login_status, "https://www.xiaohongshu.com/login"),
            "dy": (_get_dy_browser_data_dir(), _check_dy_login_status, "https://www.douyin.com/"),
        }

        if platform_key in platform_map:
            data_dir, check_func, default_login = platform_map[platform_key]
            target_url = login_url or default_login

            # 检测登录状态：未登录直接走登录页
            try:
                if check_func() != "logged_in":
                    target_url = default_login
            except Exception:
                target_url = default_login

            # 后台线程启动 persistent_context
            threading.Thread(
                target=self._launch_persistent_browser,
                args=(data_dir, target_url, platform_key),
                daemon=True,
            ).start()
            return

        # yy / wechat 走普通浏览器
        self._open_url_with_chromium(url)

    def _launch_persistent_browser(self, data_dir: str, target_url: str, platform_key: str):
        """后台线程：用 persistent_context 启动浏览器（cookie 持久化）"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print(f"[{platform_key}] playwright 未安装")
            return

        # 抑制 Playwright 子进程弹窗
        if sys.platform == "win32":
            _orig_popen = subprocess.Popen

            def _no_console_popen(*args, **kwargs):
                creationflags = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
                kwargs["creationflags"] = creationflags
                return _orig_popen(*args, **kwargs)

            subprocess.Popen = _no_console_popen

        # v8.0.2 强制解锁用户数据目录，避免已开浏览器时启动失败导致 about:blank
        _force_unlock_chromium_dir(data_dir)

        try:
            with sync_playwright() as p:
                browser_path = _ensure_chromium_ready()
                launch_kwargs = {
                    "headless": False,
                    "viewport": {"width": 1280, "height": 800},
                    "args": ["--no-sandbox", "--disable-gpu", "--disable-blink-features=AutomationControlled"],
                }
                if browser_path:
                    launch_kwargs["executable_path"] = os.path.join(browser_path, "chrome.exe")
                # v8.2.0 修复：user_data_dir 必须作为位置参数传给 launch_persistent_context，
                # 否则会在 **kwargs 中被当作首次位置参数，导致 chromium 重复接收 user_data_dir
                # 并把 data_dir 字符串当成独立参数，导致 "unknown option" 错误
                context = p.chromium.launch_persistent_context(data_dir, **launch_kwargs)
                # launch_persistent_context 已经返回一个 context，直接用
                # v8.0.2 关闭所有现有标签页（包括残留的 about:blank），确保干净状态
                for existing_page in list(context.pages):
                    try:
                        existing_page.close()
                    except Exception:
                        pass
                page = context.new_page() if not context.pages else context.pages[0]
                # page.goto 加 try/except 容错
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    print(f"[{platform_key}] page.goto 失败: {e}")
                    try:
                        page.goto(target_url, wait_until="commit", timeout=15000)
                    except Exception as e2:
                        print(f"[{platform_key}] goto 重试也失败: {e2}")

                # 保持打开，用户手动关闭
                # context.close() 不会自动调用（让用户继续使用）
                # 但 sync_playwright 退出时会自动清理
                import time
                while True:
                    time.sleep(1)
                    try:
                        if not context.pages or not any(p for p in context.pages if not p.is_closed()):
                            break
                    except Exception:
                        break
        except Exception as e:
            print(f"[{platform_key}] persistent 浏览器启动失败: {e}")
            # 降级到普通浏览器
            self._open_url_with_chromium(target_url)
        finally:
            if sys.platform == "win32":
                try:
                    subprocess.Popen = _orig_popen
                except Exception:
                    pass

    # ─── 常驻控制面板：登录状态 + 快速访问（永远显示）───
    def _build_permanent_panel(self):
        """构建永驻显示的控制面板（圆角毛玻璃风格）。

        包含：
        - 4 平台登录状态（快手/淘宝/小红书/抖音）—— 可点击登录
        - 6 平台快速访问入口（抖音/快手/小红书/淘宝/YY/视频号）
        """
        # 圆角毛玻璃外层容器
        panel_outer = RoundedFrame(self.root, radius=16, fill=Colors.BG_CARD,
                                    border=Colors.GOLD_PRIMARY, border_width=1)
        panel_outer.pack(fill="x", padx=20, pady=(8, 4))

        # 内部 padding 容器
        panel = panel_outer.inner
        panel.configure(bg=Colors.BG_CARD, padx=18, pady=14)

        # ── 第 1 行：登录状态 ──
        login_row = tk.Frame(panel, bg=Colors.BG_CARD)
        login_row.pack(fill="x", pady=(0, 10))

        tk.Label(
            login_row, text="🔐 平台登录状态",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=Colors.BG_CARD, fg=Colors.GOLD_PRIMARY,
        ).pack(side="left", padx=(0, 14))

        # 4 个平台登录状态 pill
        self._build_login_pills(login_row)

        # ── 分隔线 ──
        sep = tk.Frame(panel, bg=Colors.BORDER_LIGHT, height=1)
        sep.pack(fill="x", pady=4)

        # ── 第 2 行：快速访问平台 ──
        quick_row = tk.Frame(panel, bg=Colors.BG_CARD)
        quick_row.pack(fill="x", pady=(8, 0))

        tk.Label(
            quick_row, text="⚡ 快速访问平台",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=Colors.BG_CARD, fg=Colors.GOLD_PRIMARY,
        ).pack(side="left", padx=(0, 14))

        # 6 个平台快速访问按钮
        self._build_quick_access_pills(quick_row)

    def _build_login_pills(self, parent):
        """构建 4 个平台登录状态 pill（圆角 + 状态色）"""
        platforms = [
            ("ks", "快手", Colors.ACCENT_ORANGE),
            ("tb", "淘宝", "#FF6A00"),
            ("xhs", "小红书", Colors.ACCENT_RED),
            ("dy", "抖音", "#FF6A00"),
        ]
        for key, name, color in platforms:
            pill_frame = tk.Frame(parent, bg="#1a2030", cursor="hand2")
            pill_frame.pack(side="left", padx=3)

            # icon 圆点
            icon_lbl = tk.Label(
                pill_frame, text="○",
                font=("Consolas", 10, "bold"),
                bg="#1a2030", fg=Colors.TEXT_MUTED,
            )
            icon_lbl.pack(side="left", padx=(8, 4), pady=4)

            name_lbl = tk.Label(
                pill_frame, text=f"{name} · --",
                font=("Microsoft YaHei UI", 8, "bold"),
                bg="#1a2030", fg=Colors.TEXT_SECONDARY,
            )
            name_lbl.pack(side="left", padx=(0, 8), pady=4)

            # 绑定到对应平台的点击方法
            if key == "ks":
                self.ks_pill_frame = pill_frame
                self.ks_pill_icon = icon_lbl
                self.ks_pill_label = name_lbl
                pill_frame.bind("<Button-1>", self._on_ks_login_click)
                icon_lbl.bind("<Button-1>", self._on_ks_login_click)
                name_lbl.bind("<Button-1>", self._on_ks_login_click)
            elif key == "tb":
                self.tb_pill_frame = pill_frame
                self.tb_pill_icon = icon_lbl
                self.tb_pill_label = name_lbl
                pill_frame.bind("<Button-1>", self._on_tb_login_click)
                icon_lbl.bind("<Button-1>", self._on_tb_login_click)
                name_lbl.bind("<Button-1>", self._on_tb_login_click)
            elif key == "xhs":
                self.xhs_pill_frame = pill_frame
                self.xhs_pill_icon = icon_lbl
                self.xhs_pill_label = name_lbl
                pill_frame.bind("<Button-1>", self._on_xhs_login_click)
                icon_lbl.bind("<Button-1>", self._on_xhs_login_click)
                name_lbl.bind("<Button-1>", self._on_xhs_login_click)
            elif key == "dy":
                self.dy_pill_frame = pill_frame
                self.dy_pill_icon = icon_lbl
                self.dy_pill_label = name_lbl
                pill_frame.bind("<Button-1>", self._on_dy_login_click)
                icon_lbl.bind("<Button-1>", self._on_dy_login_click)
                name_lbl.bind("<Button-1>", self._on_dy_login_click)

    def _build_quick_access_pills(self, parent):
        """构建 6 个平台快速访问 pill（圆角 + 品牌色）

        快手/淘宝/小红书/抖音：用 persistent_context 启动（与登录状态共享 cookie）
        YY/视频号：用普通系统浏览器（无持久化需求）
        """
        platforms = [
            # (name, icon, color, url, platform_key)
            ("抖音", "🎵", "#FE2C55", "https://live.douyin.com/", "dy"),
            ("快手", "📹", "#FF6A00", "https://live.kuaishou.com/", "ks"),
            ("小红书", "📕", "#FE2C55", "https://www.xiaohongshu.com/livelist", "xhs"),
            ("淘宝", "🛒", "#FF6A00", "https://tbzb.taobao.com/", "tb"),
            ("YY", "🎤", "#FFD700", "https://www.yy.com/", "yy"),
            ("视频号", "💬", "#07C160", "https://channels.weixin.qq.com/", "wechat"),
        ]
        for name, icon, color, url, platform_key in platforms:
            pill = tk.Frame(parent, bg="#1a2030", cursor="hand2")
            pill.pack(side="left", padx=3)

            icon_lbl = tk.Label(
                pill, text=icon,
                font=("Segoe UI Emoji", 10),
                bg="#1a2030", fg=color,
            )
            icon_lbl.pack(side="left", padx=(8, 4), pady=4)

            name_lbl = tk.Label(
                pill, text=name,
                font=("Microsoft YaHei UI", 8, "bold"),
                bg="#1a2030", fg=color,
            )
            name_lbl.pack(side="left", padx=(0, 8), pady=4)

            # hover 效果
            def _enter(e, p=pill, i=icon_lbl, n=name_lbl, c=color):
                p.configure(bg=color)
                i.configure(bg=color, fg="#ffffff")
                n.configure(bg=color, fg="#ffffff")
            def _leave(e, p=pill, i=icon_lbl, n=name_lbl):
                p.configure(bg="#1a2030")
                i.configure(bg="#1a2030")
                n.configure(bg="#1a2030")

            # 绑定事件：4 平台用 persistent 浏览器，2 平台用普通浏览器
            for w in (pill, icon_lbl, name_lbl):
                w.bind("<Enter>", _enter)
                w.bind("<Leave>", _leave)
                if platform_key in ("ks", "tb", "xhs", "dy"):
                    w.bind("<Button-1>", lambda e, pk=platform_key, u=url: self._open_persistent_url(pk, u))
                else:
                    w.bind("<Button-1>", lambda e, u=url: self._open_url_with_chromium(u))

    # ─── 占位提示（简化：欢迎卡片 + 可折叠教程，快速访问已搬到永驻面板） ───
    def _show_placeholder(self):
        self._clear_result()
        placeholder = tk.Frame(self.result_inner, bg=Colors.BG_DARK)
        placeholder.pack(fill="both", expand=True, pady=(20, 12), padx=8)

        # ═══ 1. 欢迎卡片（圆角 + 金色顶部条）═══
        welcome_outer = RoundedFrame(placeholder, radius=16, fill=Colors.BG_CARD,
                                      border=Colors.GOLD_PRIMARY, border_width=1)
        welcome_outer.pack(fill="x", pady=(0, 16))

        welcome = welcome_outer.inner
        welcome.configure(bg=Colors.BG_CARD)

        # 金色顶部装饰条
        top_bar = tk.Frame(welcome, bg=Colors.GOLD_PRIMARY, height=3)
        top_bar.pack(fill="x")

        # 欢迎内容
        welcome_body = tk.Frame(welcome, bg=Colors.BG_CARD)
        welcome_body.pack(fill="x", padx=32, pady=22)

        tk.Label(
            welcome_body, text="🎬  欢迎使用影视匠直播流获取工具",
            font=("Microsoft YaHei UI", 14, "bold"),
            bg=Colors.BG_CARD, fg=Colors.GOLD_PRIMARY,
        ).pack(anchor="w")
        tk.Label(
            welcome_body, text="一键解析抖音 / 快手 / 小红书 / 淘宝 / YY / 视频号 的直播推流地址",
            font=("Microsoft YaHei UI", 9),
            bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
        ).pack(anchor="w", pady=(4, 0))
        tk.Label(
            welcome_body, text="💡  提示：上方「快速访问平台」可一键打开各平台官网，登录状态会实时显示在「平台登录状态」栏",
            font=("Microsoft YaHei UI", 8),
            bg=Colors.BG_CARD, fg=Colors.GOLD_DARK,
        ).pack(anchor="w", pady=(8, 0))

        # ═══ 3. 使用教程（可折叠 + 圆角）═══
        self._guide_collapsed = getattr(self, "_guide_collapsed", False)
        guide_header_outer = RoundedFrame(placeholder, radius=12, fill=Colors.BG_CARD,
                                           border=Colors.BORDER_LIGHT, border_width=1)
        guide_header_outer.pack(fill="x")
        guide_header = guide_header_outer.inner
        guide_header.configure(bg=Colors.BG_CARD, cursor="hand2")

        guide_arrow = tk.Label(
            guide_header, text="▼" if not self._guide_collapsed else "▶",
            font=("Segoe UI Symbol", 10),
            bg=Colors.BG_CARD, fg=Colors.GOLD_PRIMARY,
        )
        guide_arrow.pack(side="left", padx=(16, 8), pady=10)
        tk.Label(
            guide_header, text="使用教程  ·  按平台查看操作指引",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=Colors.BG_CARD, fg=Colors.TEXT_PRIMARY,
        ).pack(side="left", pady=10)

        # 教程内容容器
        self._guide_container = tk.Frame(placeholder, bg=Colors.BG_DARK)
        if not self._guide_collapsed:
            self._guide_container.pack(fill="x", pady=(4, 0))
            self._build_guide_content(self._guide_container)

        def _toggle_guide(e=None):
            self._guide_collapsed = not self._guide_collapsed
            if self._guide_collapsed:
                self._guide_container.pack_forget()
                guide_arrow.configure(text="▶")
            else:
                self._guide_container.pack(fill="x", pady=(4, 0))
                # 清空并重新填充
                for w in self._guide_container.winfo_children():
                    w.destroy()
                self._build_guide_content(self._guide_container)
                guide_arrow.configure(text="▼")

        for w in (guide_header, guide_arrow):
            w.bind("<Button-1>", _toggle_guide)

    def _build_guide_content(self, parent):
        """构建教程内容：4 个平台的操作指引，可折叠卡片"""
        guide_data = [
            ("快手直播", "#FF6A00", "🟠", [
                "粘贴快手直播链接，点击「解析直播流」",
                "等待浏览器自动弹出（Edge / Chrome），不要关闭",
                "如出现验证码，在弹出的浏览器中手动完成",
                "页面加载完成后工具自动提取直播流地址",
            ], "首次会自动打开快手二维码登录页，手机扫码后自动跳转解析，登录状态会自动保存"),
            ("淘宝直播", "#FF6A00", "🟠", [
                "粘贴淘宝直播链接（支持 tbzb.taobao.com / live.taobao.com）",
                "等待浏览器自动弹出，如需登录请扫码淘宝账号",
                "浏览器自动监听网络请求，提取直播流地址",
                "提取完成后浏览器自动关闭，流链接显示在列表中",
            ], "需要浏览器自动化解析，首次使用需登录淘宝账号，登录状态自动保存"),
            ("小红书直播", "#FE2C55", "🔴", [
                "点击「解析直播流」会自动弹出浏览器",
                "首次使用需登录小红书账号（手机扫码）",
                "登录成功后自动跳转直播间解析",
                "提取完成后浏览器自动关闭",
            ], "需要浏览器自动化解析，首次使用需登录小红书账号，登录状态自动保存"),
            ("抖音直播", "#FF6A00", "🟠", [
                "粘贴抖音直播链接（live.douyin.com / douyin.com/follow/live）",
                "点击状态栏「抖音未登录」可提前扫码登录",
                "浏览器自动监听网络请求，提取直播流地址",
                "提取完成后浏览器自动关闭",
            ], "需要浏览器自动化解析，首次使用需登录抖音账号（状态栏可扫码），登录状态自动保存"),
        ]
        for name, color, dot, tips, footer in guide_data:
            card_outer = RoundedFrame(parent, radius=12, fill=Colors.BG_CARD,
                                       border=Colors.BORDER_LIGHT, border_width=1)
            card_outer.pack(fill="x", pady=(0, 8), padx=2)
            card = card_outer.inner
            card.configure(bg=Colors.BG_CARD)

            # 卡片标题条
            title_bar = tk.Frame(card, bg=color, height=4)
            title_bar.pack(fill="x")
            tk.Label(
                card, text=f"  {name}  ·  操作指引",
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=Colors.BG_CARD, fg=color, anchor="w",
            ).pack(fill="x", padx=14, pady=(8, 6))

            for idx, tip in enumerate(tips, 1):
                row = tk.Frame(card, bg=Colors.BG_CARD)
                row.pack(fill="x", padx=20, pady=1)
                tk.Label(
                    row, text=f"{idx}.",
                    font=("Microsoft YaHei UI", 9, "bold"),
                    bg=Colors.BG_CARD, fg=color,
                    width=2, anchor="e",
                ).pack(side="left", padx=(0, 6))
                tk.Label(
                    row, text=tip,
                    font=("Microsoft YaHei UI", 9),
                    bg=Colors.BG_CARD, fg=Colors.TEXT_SECONDARY,
                    anchor="w",
                ).pack(side="left")

            tk.Label(
                card, text=f"💡 {footer}",
                font=("Microsoft YaHei UI", 8),
                bg=Colors.BG_CARD, fg=Colors.TEXT_MUTED,
            ).pack(anchor="w", padx=20, pady=(6, 10))

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
            # 收起时 - 按钮文字恢复
            if hasattr(self, '_proxy_settings_btn'):
                self._proxy_settings_btn._set_text("代理设置 🔧")
        else:
            self.proxy_frame.pack(fill="x", pady=(6, 0))
            # 展开时 - 按钮文字变成下箭头
            if hasattr(self, '_proxy_settings_btn'):
                self._proxy_settings_btn._set_text("代理设置 🔽")
        # 同步 _proxy_toggle_lbl（兼容老版逻辑）
        if hasattr(self, '_proxy_toggle_lbl'):
            if self.proxy_frame.winfo_manager():
                self._proxy_toggle_lbl.configure(text="代理设置 ▾")
            else:
                self._proxy_toggle_lbl.configure(text="代理设置 ▸")

    def _toggle_proxy_from_btn(self, _=None):
        """v7.6.28 从 btn_row 的代理设置按钮触发 toggle。"""
        self._toggle_proxy(None)

    # ─── 小红书登录状态显示 ───
    def _refresh_xhs_login_display(self):
        """检测小红书登录状态并更新状态栏标注。"""
        self._xhs_login_status = _check_xhs_login_status()
        status = self._xhs_login_status

        if status == "logged_in":
            if hasattr(self, 'xhs_login_icon'):
                self.xhs_login_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
                self.xhs_login_label.configure(text="小红书已登录", fg=Colors.ACCENT_GREEN)
            # 新版 pill（永驻面板）
            if hasattr(self, 'xhs_pill_icon'):
                self.xhs_pill_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
                self.xhs_pill_label.configure(text="小红书 · 已登录", fg=Colors.ACCENT_GREEN)
                self.xhs_pill_frame.configure(bg="#1a3020")
        elif status == "expired":
            if hasattr(self, 'xhs_login_icon'):
                self.xhs_login_icon.configure(text="●", fg=Colors.ACCENT_ORANGE)
                self.xhs_login_label.configure(text="小红书登录可能失效(点击重登)", fg=Colors.ACCENT_ORANGE)
            if hasattr(self, 'xhs_pill_icon'):
                self.xhs_pill_icon.configure(text="●", fg=Colors.ACCENT_ORANGE)
                self.xhs_pill_label.configure(text="小红书 · 失效", fg=Colors.ACCENT_ORANGE)
                self.xhs_pill_frame.configure(bg="#3a2a18")
        else:  # never
            if hasattr(self, 'xhs_login_icon'):
                self.xhs_login_icon.configure(text="○", fg=Colors.TEXT_MUTED)
                self.xhs_login_label.configure(text="小红书未登录(点击登录)", fg=Colors.TEXT_MUTED)
            if hasattr(self, 'xhs_pill_icon'):
                self.xhs_pill_icon.configure(text="○", fg=Colors.TEXT_MUTED)
                self.xhs_pill_label.configure(text="小红书 · 未登录", fg=Colors.TEXT_SECONDARY)
                self.xhs_pill_frame.configure(bg="#1a2030")

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
                "--no-sandbox",  # v8.2.4 必须带，沙箱缺失会导致 Windows 上启动失败
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation"],
                "chromium_sandbox": False,  # v8.2.4 禁用 chromium 沙箱（Windows 上默认沙箱可能导致启动失败）
                "no_viewport": False,
            }

            user_data_dir = _get_xhs_browser_data_dir()
            login_success = {"value": False}

            with sync_playwright() as p:
                # 尝试启动浏览器
                context = None
                embedded_chromium = _ensure_chromium_ready()
                # v8.0.2 关闭旧 chromium 进程，避免 about:blank
                _force_unlock_chromium_dir(user_data_dir)
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
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

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
            if hasattr(self, 'dy_login_icon'):
                self.dy_login_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
                self.dy_login_label.configure(text="抖音已登录", fg=Colors.ACCENT_GREEN)
            if hasattr(self, 'dy_pill_icon'):
                self.dy_pill_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
                self.dy_pill_label.configure(text="抖音 · 已登录", fg=Colors.ACCENT_GREEN)
                self.dy_pill_frame.configure(bg="#1a3020")
        elif status == "expired":
            if hasattr(self, 'dy_login_icon'):
                self.dy_login_icon.configure(text="●", fg=Colors.ACCENT_BLUE)
                self.dy_login_label.configure(text="抖音登录可能失效(点击重登)", fg=Colors.ACCENT_BLUE)
            if hasattr(self, 'dy_pill_icon'):
                self.dy_pill_icon.configure(text="●", fg=Colors.ACCENT_BLUE)
                self.dy_pill_label.configure(text="抖音 · 失效", fg=Colors.ACCENT_BLUE)
                self.dy_pill_frame.configure(bg="#1a2840")
        else:  # never
            if hasattr(self, 'dy_login_icon'):
                self.dy_login_icon.configure(text="○", fg=Colors.TEXT_MUTED)
                self.dy_login_label.configure(text="抖音未登录(点击登录)", fg=Colors.TEXT_MUTED)
            if hasattr(self, 'dy_pill_icon'):
                self.dy_pill_icon.configure(text="○", fg=Colors.TEXT_MUTED)
                self.dy_pill_label.configure(text="抖音 · 未登录", fg=Colors.TEXT_SECONDARY)
                self.dy_pill_frame.configure(bg="#1a2030")

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
                "--no-sandbox",  # v8.2.4 必须带，沙箱缺失会导致 Windows 上启动失败
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
                    "Chrome/143.0.0.0 Safari/537.36"
                ),
                "ignore_default_args": ["--enable-automation"],
                "chromium_sandbox": False,  # v8.2.4 禁用 chromium 沙箱（Windows 上默认沙箱可能导致启动失败）
                "no_viewport": False,
            }

            user_data_dir = _get_dy_browser_data_dir()
            login_success = {"value": False}

            with sync_playwright() as p:
                context = None
                embedded_chromium = _ensure_chromium_ready()
                # v8.0.2 关闭旧 chromium 进程，避免 about:blank
                _force_unlock_chromium_dir(user_data_dir)
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
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

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
            if hasattr(self, 'ks_login_icon'):
                self.ks_login_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
                self.ks_login_label.configure(text="快手已登录", fg=Colors.ACCENT_GREEN)
            if hasattr(self, 'ks_pill_icon'):
                self.ks_pill_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
                self.ks_pill_label.configure(text="快手 · 已登录", fg=Colors.ACCENT_GREEN)
                self.ks_pill_frame.configure(bg="#1a3020")
        elif status == "expired":
            if hasattr(self, 'ks_login_icon'):
                self.ks_login_icon.configure(text="●", fg=Colors.ACCENT_ORANGE)
                self.ks_login_label.configure(text="快手登录可能失效(点击重登)", fg=Colors.ACCENT_ORANGE)
            if hasattr(self, 'ks_pill_icon'):
                self.ks_pill_icon.configure(text="●", fg=Colors.ACCENT_ORANGE)
                self.ks_pill_label.configure(text="快手 · 失效", fg=Colors.ACCENT_ORANGE)
                self.ks_pill_frame.configure(bg="#3a2a18")
        else:  # never
            if hasattr(self, 'ks_login_icon'):
                self.ks_login_icon.configure(text="○", fg=Colors.TEXT_MUTED)
                self.ks_login_label.configure(text="快手未登录(点击登录)", fg=Colors.TEXT_MUTED)
            if hasattr(self, 'ks_pill_icon'):
                self.ks_pill_icon.configure(text="○", fg=Colors.TEXT_MUTED)
                self.ks_pill_label.configure(text="快手 · 未登录", fg=Colors.TEXT_SECONDARY)
                self.ks_pill_frame.configure(bg="#1a2030")

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
                "--no-sandbox",  # v8.2.4 必须带，沙箱缺失会导致 Windows 上启动失败
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation"],
                "chromium_sandbox": False,  # v8.2.4 禁用 chromium 沙箱（Windows 上默认沙箱可能导致启动失败）
                "no_viewport": False,
            }

            user_data_dir = _get_ks_browser_data_dir()
            login_success = {"value": False}

            with sync_playwright() as p:
                # 尝试启动浏览器
                context = None
                embedded_chromium = _ensure_chromium_ready()
                # v8.0.2 关闭旧 chromium 进程，避免 about:blank
                _force_unlock_chromium_dir(user_data_dir)
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
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

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
            if hasattr(self, 'tb_login_icon'):
                self.tb_login_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
                self.tb_login_label.configure(text="淘宝已登录", fg=Colors.ACCENT_GREEN)
            if hasattr(self, 'tb_pill_icon'):
                self.tb_pill_icon.configure(text="●", fg=Colors.ACCENT_GREEN)
                self.tb_pill_label.configure(text="淘宝 · 已登录", fg=Colors.ACCENT_GREEN)
                self.tb_pill_frame.configure(bg="#1a3020")
        elif status == "expired":
            if hasattr(self, 'tb_login_icon'):
                self.tb_login_icon.configure(text="●", fg=Colors.ACCENT_ORANGE)
                self.tb_login_label.configure(text="淘宝登录可能失效(点击重登)", fg=Colors.ACCENT_ORANGE)
            if hasattr(self, 'tb_pill_icon'):
                self.tb_pill_icon.configure(text="●", fg=Colors.ACCENT_ORANGE)
                self.tb_pill_label.configure(text="淘宝 · 失效", fg=Colors.ACCENT_ORANGE)
                self.tb_pill_frame.configure(bg="#3a2a18")
        else:  # never
            if hasattr(self, 'tb_login_icon'):
                self.tb_login_icon.configure(text="○", fg=Colors.TEXT_MUTED)
                self.tb_login_label.configure(text="淘宝未登录(点击登录)", fg=Colors.TEXT_MUTED)
            if hasattr(self, 'tb_pill_icon'):
                self.tb_pill_icon.configure(text="○", fg=Colors.TEXT_MUTED)
                self.tb_pill_label.configure(text="淘宝 · 未登录", fg=Colors.TEXT_SECONDARY)
                self.tb_pill_frame.configure(bg="#1a2030")

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
                "--no-sandbox",  # v8.2.4 必须带，沙箱缺失会导致 Windows 上启动失败
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1920,1080",
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
                "ignore_default_args": ["--enable-automation"],
                "chromium_sandbox": False,  # v8.2.4 禁用 chromium 沙箱（Windows 上默认沙箱可能导致启动失败）
                "no_viewport": False,
            }

            user_data_dir = _get_tb_browser_data_dir()
            login_success = {"value": False}

            with sync_playwright() as p:
                # 尝试启动浏览器
                context = None
                embedded_chromium = _ensure_chromium_ready()
                # v8.0.2 关闭旧 chromium 进程，避免 about:blank
                _force_unlock_chromium_dir(user_data_dir)
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
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

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
        # 清除按钮：pack/forget 切换
        if hasattr(self, '_url_clear_btn'):
            try:
                if not url:
                    # 隐藏清除按钮
                    self._url_clear_btn.pack_forget()
                else:
                    # 显示清除按钮（确保先 forget 再 pack，避免重复）
                    try:
                        self._url_clear_btn.pack_forget()
                    except Exception:
                        pass
                    self._url_clear_btn.pack(side="right", padx=(4, 12), pady=0)
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

    def _on_url_focus_in(self, _=None):
        """v7.6.31 焦点进入 → 边框变金色。"""
        try:
            if hasattr(self, '_url_frame'):
                self._url_frame.configure(bg=Colors.GOLD_PRIMARY)
        except Exception:
            pass
        # 隐藏占位
        if hasattr(self, '_url_placeholder_lbl'):
            try:
                self._url_placeholder_lbl.place_forget()
            except tk.TclError:
                pass

    def _on_url_focus_out(self, _=None):
        """v7.6.33 焦点离开 → 边框恢复灰色。"""
        try:
            if hasattr(self, '_url_frame'):
                self._url_frame.configure(bg=Colors.BORDER)
        except Exception:
            pass
        # 失去焦点时若为空 → 重新显示占位
        if not self.url_var.get().strip() and hasattr(self, '_url_placeholder_lbl'):
            try:
                self._url_placeholder_lbl.place(x=30, y=0, anchor="nw",
                                                  width=300, height=42)
            except tk.TclError:
                pass

    def _on_url_pill_resize(self, event=None):
        """v7.6.31 不需要重绘（Frame 自动布局）。"""
        pass

    def _clear_url(self, _=None):
        """v7.6.31 清除 URL 输入框。"""
        self.url_var.set("")
        # 重新聚焦
        self.url_entry.focus_set()

    # ─── 获取按钮 ───
    def _on_fetch(self):
        # v8.2.6 修复：让用户看到 click 触发了
        print("[v8.2.8] _on_fetch clicked!", flush=True)
        self._show_toast("已点击获取流链接，正在解析...")
        # v8.2.6 修复：立刻修改按钮文字（不管后续是否成功）
        try:
            self.fetch_btn.configure(text="  解析中...  ", bg=Colors.GOLD_DARK)
        except Exception:
            pass
        url = self.url_var.get().strip()
        if not url:
            self._show_toast("❌ 请先粘贴直播间链接")
            self._show_toast_long("请先粘贴直播间链接", 5000)
            return
        if not url.startswith("http"):
            self._show_toast(f"❌ 缺 https:// 前缀（当前: {url[:30]}...）")
            self._show_toast_long("请输入完整的 HTTP/HTTPS 链接（以 https:// 开头）", 5000)
            return

        self.status_var.set("正在解析视频流，请稍候...")
        self.status_icon.configure(fg=Colors.ACCENT_ORANGE)

        thread = threading.Thread(target=self._do_fetch, args=(url,), daemon=True)
        thread.start()

    # ─── 后台获取流数据（在线程中运行） ───
    def _do_fetch(self, url: str):
        """v8.2.4 后台线程：解析直播流并更新 UI"""
        print(f"[v8.2.8] _do_fetch starting, url={url[:80]}", flush=True)
        try:
            result = extract_streams(url, proxy="")
            print(f"[v8.2.8] extract_streams returned, streams={len(result.get('streams', []))}", flush=True)
            # 切回主线程更新 UI
            self.root.after(0, lambda: self._show_result(result))
        except Exception as e:
            import traceback
            err_full = traceback.format_exc()
            print(f"[v8.2.8] _do_fetch EXCEPTION:\n{err_full}", flush=True)
            _err_msg = str(e)
            self.root.after(0, lambda: self._show_error(_err_msg))
        finally:
            # v8.2.7 修复：延迟 1.5 秒恢复按钮文字，让用户能看到"解析中..."状态
            def _restore():
                try:
                    if self.fetch_btn.winfo_exists():
                        self.fetch_btn.configure(
                            text="  获取流链接  ", bg=Colors.ACCENT_GREEN)
                except Exception:
                    pass
            self.root.after(1500, _restore)

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
        """v7.6.35 刷新所有平台登录状态（更新卡片）"""
        try:
            for key in ["dy", "ks", "tb", "xhs"]:
                self._refresh_single_platform_status(key)
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
            # v7.6.39 加强：先停代理（不抛异常），再确保 destroy
            try:
                stop_transcode()
            except Exception:
                pass
            # 释放 grab + destroy 用 after 延迟执行，确保事件循环回到主线程
            def _do_close():
                try:
                    dlg.grab_release()
                except Exception:
                    pass
                try:
                    dlg.destroy()
                except Exception:
                    pass
            try:
                dlg.after(0, _do_close)
            except Exception:
                _do_close()

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

        # v7.6.39：右侧加"关闭"按钮 - 解决 X 关不掉的问题
        close_btn = tk.Button(
            btn_frame, text="  ✕ 关闭  ",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            activebackground=Colors.ACCENT_RED, activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=14, pady=6,
            command=on_dlg_close,  # 调用同一个关闭函数
        )
        close_btn.pack(side="right")

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
        self.fetch_btn.configure(text="  获取流链接  ", bg=Colors.GOLD_PRIMARY)

    # ─── Toast 提示 ───
    def _show_toast(self, message: str):
        """底部弹出提示（2 秒）"""
        toast = tk.Label(
            self.root, text=f"  {message}  ",
            font=("Microsoft YaHei UI", 10),
            bg=Colors.ACCENT_GREEN, fg="white",
            padx=16, pady=6,
        )
        toast.place(relx=0.5, rely=0.92, anchor="center")
        self.root.after(2000, toast.destroy)

    def _show_toast_long(self, message: str, duration_ms: int = 5000):
        """底部弹出长提示（5 秒），用于错误说明。"""
        toast = tk.Label(
            self.root, text=f"  {message}  ",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=Colors.ACCENT_RED, fg="white",
            padx=18, pady=8,
        )
        toast.place(relx=0.5, rely=0.85, anchor="center")
        self.root.after(duration_ms, toast.destroy)

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

# 内存密码缓存（避免频繁请求文档）
_cached_password = None
_cached_password_time = 0
PASSWORD_CACHE_SECONDS = 24 * 60 * 60  # 24 小时缓存（云端改密码后最长 24 小时内同步）

# 文件级密码缓存（仅网络不通时兜底，30 分钟 TTL）
def _get_password_cache_path():
    try:
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "LiveStreamFetcher")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "password_cache.dat")
    except Exception:
        return None


def _load_password_from_file():
    """从文件加载缓存的密码（30 分钟 TTL，超出 TTL 视为无效）
    
    文件格式：<unix_timestamp>\n<password>
    """
    path = _get_password_cache_path()
    if not path or not os.path.exists(path):
        return "", 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().strip().split("\n", 1)
        if len(lines) >= 2:
            ts = float(lines[0])
            pwd = lines[1]
            elapsed = time.time() - ts
            if elapsed < PASSWORD_CACHE_SECONDS:
                return pwd, ts
            else:
                # 过期，不返回
                return "", 0
    except Exception:
        pass
    return "", 0


def _save_password_to_file(pwd: str):
    """保存密码和当前时间戳到文件"""
    path = _get_password_cache_path()
    if not path or not pwd:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{time.time()}\n{pwd}")
    except Exception:
        pass


def _log_password_debug(msg: str):
    """记录密码获取诊断日志到文件（失败时供排查）"""
    try:
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "LiveStreamFetcher")
        os.makedirs(base, exist_ok=True)
        log_path = os.path.join(base, "password_debug.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _extract_password_from_text(text: str) -> str:
    """从语雀文档的文本中提取密码（多种策略）。"""
    if not text:
        return ""

    skip_words = ["菜单", "插入", "正文", "默认字体", "快捷工具",
                  "PDF转换", "生成图片", "排版美化", "打印",
                  "腾讯文档", "正在同步", "无障碍", "登录",
                  "评论", "历史版本", "分享", "更多",
                  "直播流软件密码", "直播流", "软件密码", "返回文档",
                  "关于语雀", "使用帮助", "数据安全", "服务协议", "English",
                  "快速注册", "影视匠高清直播间搭建的小记", "小记"]
    pwd_keywords = ["密码", "password", "口令", "验证"]

    lines = text.split("\n")
    clean_lines = []
    for line in lines:
        c = line.strip()
        if c and len(c) < 500:
            clean_lines.append(c)

    # 策略 1：找到包含"密码"关键词的行，取下一行的值或同行后半段
    for i, cl in enumerate(clean_lines):
        if any(kw in cl.lower() for kw in pwd_keywords):
            # 跳过纯说明文字（如"直播流软件密码"本身）
            if any(sw == cl for sw in skip_words):
                if i + 1 < len(clean_lines):
                    next_val = clean_lines[i + 1].strip()
                    if next_val and not any(sw in next_val for sw in skip_words):
                        return next_val
            # 如果这行格式是 "密码：xxx" 或 "密码 xxx"
            for kw in pwd_keywords:
                if kw in cl.lower():
                    parts = cl.split(kw)
                    if len(parts) >= 2:
                        val = parts[-1].strip().lstrip("：:：= \t")
                        if val and len(val) < 100:
                            return val

    # 策略 2：取所有短行中最短的（密码通常很短）
    short_lines = []
    for cl in clean_lines:
        if not any(sw in cl for sw in skip_words):
            if 1 <= len(cl) <= 30:
                short_lines.append(cl)
    if short_lines:
        short_lines.sort(key=len)
        return short_lines[0]

    return ""


def _try_fetch_via_requests(timeout: float = 15) -> str:
    """快速通道：用 urllib 直接请求语雀文档（轻量、不启动浏览器）。

    如果文档内容在初始 HTML 中（SSR/搜索引擎可见），就能拿到。
    如果是纯 SPA 则拿不到，调用方需 fallback 到 Playwright。
    """
    try:
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            PASSWORD_DOC_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # 尝试从 HTML 中提取文本（粗略去标签）
        import re
        # 移除 script/style
        html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        # 替换块级标签为换行
        html = re.sub(r"</?(div|p|br|h\d|li|tr|td)[^>]*>", "\n", html, flags=re.IGNORECASE)
        # 去标签
        text = re.sub(r"<[^>]+>", " ", html)
        # 解码 HTML 实体
        import html as html_mod
        text = html_mod.unescape(text)
        # 合并多余空白
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)

        return _extract_password_from_text(text)
    except Exception:
        return ""


def _fetch_password_from_doc(timeout: float = 60) -> tuple:
    """从语雀文档抓取密码（在线优先 → 离线文件缓存兜底）。

    策略链（按优先级）：
    1️⃣ 内存缓存（30 分钟，最快）
    2️⃣ urllib 快速通道（12 秒，在线获取最新密码）
    3️⃣ Playwright 浏览器（60 秒，处理 SPA 页面）
    4️⃣ 文件缓存（30 分钟 TTL，仅前 3 步全部失败时使用）

    关键设计：永远先在线获取 → 保证云端改密码后软件立即生效。
    文件缓存仅在网络不通时兜底使用。

    Args:
        timeout: Playwright 通道的整体超时时间（秒），默认 60 秒

    Returns:
        (password, diag_msg) — 密码为空时 diag_msg 为具体失败原因（给用户看）
    """
    global _cached_password, _cached_password_time
    now = time.time()

    # ─── 1️⃣ 内存缓存（30 分钟） ───
    if _cached_password and (now - _cached_password_time) < PASSWORD_CACHE_SECONDS:
        _log_password_debug("使用内存缓存（30分内不重复请求）")
        return _cached_password, "（30分钟内已验证）"

    # ─── 2️⃣ 快速通道：urllib 直接请求（不启动浏览器，10秒搞定） ───
    _log_password_debug("尝试 urllib 快速通道...")
    req_pwd = _try_fetch_via_requests(timeout=10)
    if req_pwd:
        _cached_password = req_pwd
        _cached_password_time = now
        _save_password_to_file(req_pwd)
        _log_password_debug(f"urllib 通道成功 (长度={len(req_pwd)})")
        return req_pwd, "云端获取成功 ✓"
    _log_password_debug("urllib 通道未拿到密码")

    # ─── 3️⃣ Fallback：Playwright headless 模式（处理纯 SPA 页面） ───
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log_password_debug("playwright 未安装")
        return "", "playwright 未安装"

    # ── 禁止 Playwright 子进程弹出 CMD 黑窗口 ──
    _orig_popen = None
    _popen_patched = False
    if sys.platform == "win32":
        _orig_popen = subprocess.Popen

        def _no_console_popen(*args, **kwargs):
            creationflags = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            kwargs["creationflags"] = creationflags
            return _orig_popen(*args, **kwargs)

        subprocess.Popen = _no_console_popen
        _popen_patched = True

    result_holder = [""]
    diag_holder = ["未知原因"]
    error_holder = [None]

    def _do_fetch():
        try:
            with sync_playwright() as p:
                # 尝试用嵌入式 Chromium → 系统 Chrome → Edge
                browser = None
                launch_errors = []

                # 优先嵌入式 Chromium
                embedded_chromium = _ensure_chromium_ready()
                # v8.2.8 修复：密码抓取是无状态 headless 单次启动（无 persistent_context），
                # 不使用 user_data_dir。移除 v8.0.2 误粘贴的 unlock 调用（原 user_data_dir 从未赋值）
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
                    diag_holder[0] = "无法启动浏览器：" + "; ".join(launch_errors)
                    _log_password_debug(diag_holder[0])
                    return

                page = browser.new_page(viewport={"width": 1280, "height": 800})

                # goto 带重试：网络延迟大时第一次可能超时，重试一次
                goto_ok = False
                for attempt in range(2):
                    try:
                        page.goto(
                            PASSWORD_DOC_URL,
                            wait_until="domcontentloaded",
                            timeout=20000,
                        )
                        goto_ok = True
                        break
                    except Exception as goto_e:
                        _log_password_debug(f"goto 第 {attempt+1} 次失败: {goto_e}")
                        if attempt == 0:
                            try:
                                page.wait_for_timeout(1000)
                            except Exception:
                                pass

                if not goto_ok:
                    diag_holder[0] = "页面加载超时（语雀响应过慢），建议重试"
                    _log_password_debug(diag_holder[0])
                    try:
                        browser.close()
                    except Exception:
                        pass
                    return

                # 等待文档内容加载
                try:
                    page.wait_for_selector(
                        ".yuque-doc-content, .doc-content, .ql-editor, [contenteditable], "
                        ".text-editor, .editor-content, .doc-body, .ne-viewer, .article-content",
                        timeout=10000
                    )
                except Exception:
                    _log_password_debug("wait_for_selector 超时，尝试继续提取")

                # 等待网络空闲（最多 8 秒）
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

                # 短暂等待确保 DOM 稳定
                page.wait_for_timeout(1000)

                # 抓取全文
                full_text = ""
                try:
                    full_text = page.inner_text("body").strip()
                except Exception as e:
                    diag_holder[0] = f"页面文本提取失败: {e}"
                    _log_password_debug(diag_holder[0])
                    try:
                        browser.close()
                    except Exception:
                        pass
                    return

                _log_password_debug(f"文档已加载，共 {len(full_text)} 字符")

                # 提取密码
                pwd = _extract_password_from_text(full_text)
                if pwd:
                    result_holder[0] = pwd
                else:
                    diag_holder[0] = f"页面已加载（{len(full_text)} 字符），但未识别到密码（可能页面结构变化）"
                    _log_password_debug(diag_holder[0])

                try:
                    browser.close()
                except Exception:
                    pass
        except Exception as e:
            error_holder[0] = e
            diag_holder[0] = f"Playwright 异常: {type(e).__name__}: {e}"
            _log_password_debug(diag_holder[0])

    # 启动线程并等待结果（带超时）
    fetch_thread = threading.Thread(target=_do_fetch, daemon=True)
    fetch_thread.start()
    fetch_thread.join(timeout=timeout)

    if fetch_thread.is_alive():
        diag_holder[0] = f"浏览器超时（{int(timeout)}秒），尝试文件缓存兜底"
        _log_password_debug(diag_holder[0])
        if _popen_patched:
            subprocess.Popen = _orig_popen
        # 回退到文件缓存（30 分钟 TTL）
        cached, ts = _load_password_from_file()
        if cached:
            _cached_password = cached
            _cached_password_time = ts
            _log_password_debug("Playwright 超时，使用文件缓存兜底")
            return cached, "离线模式（使用文件缓存）"
        return "", diag_holder[0]

    if error_holder[0]:
        _log_password_debug(f"获取密码失败: {error_holder[0]}")

    password = result_holder[0].strip()
    if password:
        _cached_password = password
        _cached_password_time = now
        _save_password_to_file(password)
        _log_password_debug(f"Playwright 通道成功 (长度={len(password)})")
        diag_msg = "云端获取成功 ✓（浏览器模式）"
    else:
        diag_msg = diag_holder[0]
        _log_password_debug(f"Playwright 通道失败: {diag_msg}")
        # ─── 4️⃣ 最后兜底：文件缓存（仅当前 3 步全部失败时使用） ───
        cached, ts = _load_password_from_file()
        if cached:
            _cached_password = cached
            _cached_password_time = ts
            _log_password_debug("Playwright 失败，使用文件缓存兜底")
            return cached, f"离线模式（{diag_msg[:30]}…，使用缓存兜底）"
        _log_password_debug("所有通道均失败，无可用密码")

    if _popen_patched:
        subprocess.Popen = _orig_popen

    return password, diag_msg


class PasswordGate:
    """启动密码验证页面"""

    def __init__(self, root, on_success):
        self.root = root
        self.on_success = on_success
        self._frame = None

        # 预取的密码（后台线程拉取，用户输入时直接用，免去等待）
        self._prefetched_pwd = None
        self._prefetched_done = False
        self._prefetched_lock = threading.Lock()

        # 创建密码验证界面
        self.root.withdraw()  # 先隐藏主窗口

        self.win = tk.Toplevel(root)
        self.win.title("影视匠直播流获取 — 密码验证")
        self.win.geometry("420x320")
        self.win.resizable(False, False)
        self.win.configure(bg=Colors.BG_DARK)

        # ★ 关键：密码窗口也用金色 LOGO（标题栏 + 任务栏）
        _apply_gold_icon(self.win)

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

        # ★ 启动预取线程：用户输入密码时（5-10 秒）密码已拉取好，验证瞬时返回
        threading.Thread(target=self._prefetch_password, daemon=True).start()

    def _prefetch_password(self):
        """后台预取密码：等用户输入完成时，密码已就绪"""
        try:
            pwd, _ = _fetch_password_from_doc()
            with self._prefetched_lock:
                self._prefetched_pwd = pwd
                self._prefetched_done = True
        except Exception:
            # 预取失败不阻塞用户，后续点验证时再正常拉取
            with self._prefetched_lock:
                self._prefetched_done = True

    def _get_prefetched_password(self):
        """获取预取的密码（线程安全）"""
        with self._prefetched_lock:
            if self._prefetched_done and self._prefetched_pwd:
                return self._prefetched_pwd
            return None

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

        # 错误提示（支持 2 行，换行后居中）
        self._error_label = tk.Label(
            container, text="",
            font=("Microsoft YaHei UI", 9),
            bg=bg, fg=Colors.ACCENT_RED,
            wraplength=380, justify="center", height=2
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

        # ★ 优化：如果预取的密码已就绪，直接比对（瞬时返回）
        prefetched = self._get_prefetched_password()
        if prefetched:
            if user_input == prefetched:
                self._on_verify_success()
            else:
                self._on_verify_fail("密码错误，请重新输入")
            return

        # 预取未就绪，正常走网络拉取流程
        self._set_loading(True)
        self._error_label.configure(text="正在从云端获取密码...")

        # 在后台线程中获取云端密码并验证
        def _do_verify():
            try:
                correct_pwd, diag_msg = _fetch_password_from_doc()

                if not correct_pwd:
                    # 显示具体失败原因（不再笼统说"网络问题"）
                    self.root.after(0, lambda: self._on_verify_fail(
                        f"无法获取密码：{diag_msg}\n详细日志见 password_debug.log"
                    ))
                    return

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
    # ★ 关键：使用金色 LOGO 作为窗口图标（同时影响标题栏和任务栏）
    _apply_gold_icon(root)

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
