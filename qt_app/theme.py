# -*- coding: utf-8 -*-
"""
theme.py — PySide6 版本的主题定义（单一数据源）

色值来源：live_stream_fetcher.py 的 Colors 类（v7.6 深色玻璃感配色）
说明：UI 层完全解耦，不 import 业务层，颜色/字号/间距在此集中维护，
      便于长期统一调整视觉。
"""

from __future__ import annotations

import os
import sys


def _read_version() -> str:
    """从项目根目录 VERSION 文件读取版本号（版本号唯一真相来源）。

    兼容三种运行环境：
      1. 开发模式（源码运行）：项目根目录 VERSION
      2. PyInstaller onefile：_MEIPASS 内打包的 VERSION
      3. fallback：读取失败返回默认 "8.3.0"
    """
    # 候选基础目录：_MEIPASS（打包）→ 项目根（开发）
    bases = []
    if getattr(sys, "_MEIPASS", None):
        bases.append(sys._MEIPASS)
    bases.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    for base in bases:
        p = os.path.join(base, "VERSION")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    ver = f.read().strip()
                    if ver:
                        return ver
            except Exception:
                continue
    return "8.3.0"


# 应用版本号（全局唯一数据源，UI 显示 / EXE 命名都引用它）
APP_VERSION = _read_version()
APP_VERSION_FULL = f"v{APP_VERSION}"


class Colors:
    """深色玻璃感 · 圆角仪表盘风格主题色板"""

    # ── 基础背景 ──
    BG_DARK = "#0a0e1a"          # 主背景（深蓝黑）
    BG_SIDEBAR = "#0d1117"       # 侧边栏背景（更深）
    BG_CARD = "#161b2e"          # 卡片背景（深蓝灰）
    BG_CARD_LIGHT = "#1f2540"    # 卡片亮色
    BG_CARD_HOVER = "#252b45"    # 卡片 hover
    BG_INPUT = "#0f1422"         # 输入框背景
    BG_HOVER = "#1f2540"         # 通用 hover 背景

    # ── 边框 ──
    BORDER = "#2a3148"           # 默认边框
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

    # ── Hero 渐变（青→蓝）──
    HERO_GRADIENT_FROM = "#00d4ff"
    HERO_GRADIENT_TO = "#5b8def"

    # ── 平台品牌色 ──
    PLATFORM_DOUYIN = "#FE2C55"
    PLATFORM_KUAISHOU = "#FF6A00"
    PLATFORM_XHS = "#FF2442"
    PLATFORM_TAOBAO = "#FF6A00"
    PLATFORM_YY = "#FFD700"
    PLATFORM_WECHAT = "#07C160"

    # ── 状态色 ──
    STATUS_ONLINE = "#10b981"
    STATUS_OFFLINE = "#6a7390"
    STATUS_EXPIRED = "#f59e0b"
    STATUS_ERROR = "#ff6b6b"


# ─── 平台元数据（用于顶部横向 tab 选择）───
# v8.2.8 设计：5 个解析平台横向 tab，视频号作为独立工具按钮（不在 tab 里）
PLATFORM_META = {
    "dy": {
        "name": "抖音直播", "short": "抖音", "icon": "🎵",
        "icon_path": "icons/dy_real.png",
        "open_url": "https://www.douyin.com/jingxuan",
        "color": Colors.PLATFORM_DOUYIN,
        "desc": "支持 live.douyin.com / douyin.com 多域名解析",
        "login": True,
    },
    "xhs": {
        "name": "小红书直播", "short": "小红书", "icon": "📕",
        "icon_path": "icons/xhs_real.png",
        "open_url": "https://www.xiaohongshu.com/login",
        "color": Colors.PLATFORM_XHS,
        "desc": "支持 xhscdn.com 流地址 · HEVC 自动转码",
        "login": True,
    },
    "ks": {
        "name": "快手直播", "short": "快手", "icon": "📹",
        "icon_path": "icons/ks_real.png",
        "open_url": "https://passport.kuaishou.com/pc/account/login",
        "color": Colors.PLATFORM_KUAISHOU,
        "desc": "支持 kuaishou.com 域名解析 · 需扫码登录",
        "login": True,
    },
    "tb": {
        "name": "淘宝直播", "short": "淘宝", "icon": "🛒",
        "icon_path": "icons/tb_real.png",
        "open_url": "https://tbzb.taobao.com/",
        "color": Colors.PLATFORM_TAOBAO,
        "desc": "支持 tbzb.taobao.com · FLV 链自动代理",
        "login": True,
    },
    "yy": {
        "name": "YY 直播", "short": "YY", "icon": "🎤",
        "icon_path": "icons/yy_real.png",
        "open_url": "https://www.yy.com/",
        "color": Colors.PLATFORM_YY,
        "desc": "支持 www.yy.com / wap.yy.com 多端解析",
        "login": False,
    },
}


# ─── 清晰度分类（颜色映射）───
QUALITY_LEVELS = {
    "UHD":   ("UHD",    "超高清", "#ff6b6b"),
    "OR4":   ("OR4",    "原画",   "#f0883e"),
    "HD":    ("HD",     "高清",   "#3fb950"),
    "SD":    ("SD",     "标清",   "#58a6ff"),
    "LD":    ("LD",     "流畅",   "#8b949e"),
    "OTHER": ("OTHER",  "其他",   "#bc8cff"),
}

# 格式标签颜色
FORMAT_COLORS = {
    "flv":  "#22c55e",
    "m3u8": "#3b82f6",
    "hls":  "#3b82f6",
    "fmp4": "#f59e0b",
    "mp4":  "#8b5cf6",
}


# ─── 字号 / 间距 ───
FONT_FAMILY = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

FONT_H1 = 22          # hero 大标题
FONT_H2 = 16          # 区块标题
FONT_BODY = 13        # 正文
FONT_SMALL = 11       # 小字
FONT_TINY = 9         # 标签

RADIUS_SM = 8
RADIUS_MD = 12
RADIUS_LG = 16
RADIUS_XL = 20

SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24


def hex_to_rgba(hex_color: str, alpha: int) -> str:
    """十六进制色转 rgba() 字符串，用于 QSS 半透明叠加。"""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"
