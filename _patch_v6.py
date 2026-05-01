# -*- coding: utf-8 -*-
"""
v6.0 patch 脚本：
1. 删除视频号（mitmproxy）相关代码
2. 新增「转码」按钮，处理 HEVC/H.265 流转 H.264
3. 版本号全局改为 v6.0
"""
import re

SRC = r"c:\Users\15346\WorkBuddy\20260408125205\live_stream_fetcher.py"
DST = r"c:\Users\15346\WorkBuddy\20260408125205\live_stream_fetcher.py"

print("读取源文件...")
with open(SRC, "r", encoding="utf-8") as f:
    code = f.read()

original_len = len(code.splitlines())
print(f"原始行数: {original_len}")

# ══════════════════════════════════════════════════════════════
# 1. 版本号修改
# ══════════════════════════════════════════════════════════════
print("\n[1] 修改版本号...")
code = code.replace('LiveStreamFetcher v5.20 — by LONGSHAO', 'LiveStreamFetcher v6.0 — by LONGSHAO')
code = code.replace('LiveStreamFetcher v5.5 — by LONGSHAO', 'LiveStreamFetcher v6.0 — by LONGSHAO')
code = code.replace('LiveStreamFetcher v5 — by LONGSHAO', 'LiveStreamFetcher v6.0 — by LONGSHAO')
code = code.replace(
    'header, text="v5.20"',
    'header, text="v6.0"'
)
code = code.replace(
    'header, text="v5.5"',
    'header, text="v6.0"'
)
# 文件头说明
code = code.replace(
    '多平台直播视频流获取工具 v2\n支持平台：抖音、视频号、快手、小红书、淘宝直播',
    '多平台直播视频流获取工具 v6.0\n支持平台：抖音、快手、小红书、淘宝直播'
)
print("  版本号替换完成")

# ══════════════════════════════════════════════════════════════
# 2. 删除 PLATFORM_PATTERNS 中的视频号条目
# ══════════════════════════════════════════════════════════════
print("\n[2] 删除 PLATFORM_PATTERNS 视频号...")
code = re.sub(
    r'\s*"视频号":\s*\[\s*r"channels\\\\..weixin\\.qq\\.com",\s*r"weixin\\.qq\\.com\.\*channel",\s*r"videopublish\\.qq\\.com",\s*\],',
    '',
    code
)
# 更通用的方式
code = re.sub(
    r'\n\s*"视频号":\s*\[\n[^\]]*\],',
    '',
    code
)
print("  完成")

# ══════════════════════════════════════════════════════════════
# 3. 删除 WeChatChannelsProxy class + fetch_wechat_channels +
#    WeChatProxyRunningError + CACertNotInstalledError
# ══════════════════════════════════════════════════════════════
print("\n[3] 删除视频号后端代码块...")

# 找到代码块边界
BLOCK_START_MARKER = "\n# ─── 视频号（mitmproxy 代理抓取）─────────────────────────────"
BLOCK_END_MARKER   = "\n# ═══════════════════════════════════════════════════════\n# 平台解析器注册表"

start_idx = code.find(BLOCK_START_MARKER)
end_idx   = code.find(BLOCK_END_MARKER)

if start_idx == -1 or end_idx == -1:
    print(f"  警告: 视频号代码块边界未找到 (start={start_idx}, end={end_idx})")
else:
    deleted_lines = code[start_idx:end_idx].count('\n')
    code = code[:start_idx] + "\n" + code[end_idx:]
    print(f"  已删除 {deleted_lines} 行")

# ══════════════════════════════════════════════════════════════
# 4. 删除 PLATFORM_FETCHERS 中的视频号条目
# ══════════════════════════════════════════════════════════════
print("\n[4] 删除 PLATFORM_FETCHERS['视频号']...")
code = re.sub(r'\s*"视频号":\s*fetch_wechat_channels,\n', '\n', code)
print("  完成")

# ══════════════════════════════════════════════════════════════
# 5. 删除不降级列表中的"视频号"
# ══════════════════════════════════════════════════════════════
print("\n[5] 删除不降级列表中的视频号...")
code = code.replace(
    'if platform in ("快手", "淘宝直播", "视频号", "小红书", "抖音"):',
    'if platform in ("快手", "淘宝直播", "小红书", "抖音"):'
)
print("  完成")

# ══════════════════════════════════════════════════════════════
# 6. 删除 platform_colors 中的视频号
# ══════════════════════════════════════════════════════════════
print("\n[6] 删除 platform_colors 视频号...")
code = code.replace(
    '"淘宝直播": "#ff5000", "视频号": "#07c160",',
    '"淘宝直播": "#ff5000",'
)
print("  完成")

# ══════════════════════════════════════════════════════════════
# 7. 删除 UI 中的「视频号抓取」按钮
# ══════════════════════════════════════════════════════════════
print("\n[7] 删除 UI 视频号按钮...")
WCH_BTN_BLOCK = '''
        # ── 视频号独立按钮 ──
        self.wch_btn = tk.Button(
            btn_row, text="  视频号抓取  ",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg="#07c160", fg="white",
            activebackground="#06ad56", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", padx=20, pady=7,
            command=self._on_wch_capture,
        )
        self.wch_btn.pack(side="left", padx=(8, 0))
        self.wch_btn.bind("<Enter>", lambda e: self.wch_btn.configure(bg="#06ad56"))
        self.wch_btn.bind("<Leave>", lambda e: self.wch_btn.configure(bg="#07c160"))
'''
if WCH_BTN_BLOCK in code:
    # 替换为转码按钮
    TRANSCODE_BTN_BLOCK = '''
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
'''
    code = code.replace(WCH_BTN_BLOCK, TRANSCODE_BTN_BLOCK)
    print("  按钮替换完成")
else:
    print("  警告: 视频号按钮块未找到（尝试模糊匹配）")
    idx = code.find('self.wch_btn = tk.Button(')
    if idx != -1:
        print(f"  找到 wch_btn 位置: {code[:idx].count(chr(10))+1}")

# ══════════════════════════════════════════════════════════════
# 8. 删除视频号相关的 UI 方法：
#    _on_wch_capture / _do_wch_capture / _show_cert_install_guide /
#    _enter_wch_wait_mode / _update_wch_counter / _start_wch_polling /
#    _on_wch_streams_captured / _on_wch_timeout_or_stop
# ══════════════════════════════════════════════════════════════
print("\n[8] 删除视频号 UI 方法...")

# 找「视频号独立抓取按钮」注释到下一个「# ─── 」区块
WCH_UI_METHODS_START = "\n    # ─── 视频号独立抓取按钮 ───"
# 找下一个 "    # ─── " 注释块（UI 其他方法）
idx_start = code.find(WCH_UI_METHODS_START)
if idx_start != -1:
    # 找结束（下一个同级注释块）
    idx_end = code.find("\n    # ─── ", idx_start + len(WCH_UI_METHODS_START))
    if idx_end == -1:
        idx_end = code.find("\n    def _show_cert_install_guide", idx_start)
    if idx_end != -1:
        # 还需要删 _show_cert_install_guide 和更多视频号方法
        # 一起找到 wch_timeout_or_stop 的结尾
        # 找到最后一个视频号相关方法的结尾
        wch_end_method = "_on_wch_timeout_or_stop"
        idx_wch_method = code.find(f"    def {wch_end_method}")
        if idx_wch_method != -1:
            # 找该方法后的下一个同级 def
            idx_after = code.find("\n    def ", idx_wch_method + 10)
            if idx_after == -1:
                idx_after = code.find("\n    # ─── ", idx_wch_method + 10)
            if idx_after != -1:
                deleted = code[idx_start:idx_after].count('\n')
                code = code[:idx_start] + code[idx_after:]
                print(f"  已删除视频号 UI 方法块（约 {deleted} 行）")
            else:
                print("  警告: 未找到方法结尾")
        else:
            print(f"  警告: 未找到 {wch_end_method}")
    else:
        print("  警告: 未找到视频号 UI 方法块结尾")
else:
    print("  警告: 未找到视频号 UI 方法注释")

# ══════════════════════════════════════════════════════════════
# 9. 删除 _show_result 中的视频号 tips 分支
# ══════════════════════════════════════════════════════════════
print("\n[9] 删除 _show_result 中的视频号 tips 分支...")
WC_TIPS_BLOCK = '''            elif platform == "视频号":
                tips = [
                    "视频号流通过 mitmproxy 代理拦截获取，链接时效性较短（约几分钟）",
                    "FLV/M3U8 链接可直接粘贴到 VLC、PotPlayer、ffplay 中播放",
                    "录制命令：ffmpeg -i \\"<链接>\\" -c copy output.ts",
                    "如提示链接已过期，请重新点击「获取流链接」再次抓取",
                    "注意：视频号流需要 PC 微信已登录且能正常打开目标直播间",
                ]'''
if WC_TIPS_BLOCK in code:
    code = code.replace(WC_TIPS_BLOCK, '')
    print("  完成")
else:
    # 模糊删除
    code = re.sub(
        r'\s*elif platform == "视频号":\s*tips = \[.*?\]',
        '',
        code,
        flags=re.DOTALL
    )
    print("  使用模糊匹配完成")

# ══════════════════════════════════════════════════════════════
# 10. 删除 _on_toggle_system_proxy 中引用 WeChatChannelsProxy.set_system_proxy 的依赖
#     （只有这里用了 WeChatChannelsProxy，改成内联实现）
# ══════════════════════════════════════════════════════════════
print("\n[10] 替换 WeChatChannelsProxy.set_system_proxy 引用...")
code = code.replace(
    'addr = WeChatChannelsProxy.set_system_proxy(8080)',
    'addr = _set_system_proxy(8080)'
)
# 检查是否有其他引用
wc_refs = code.count('WeChatChannelsProxy')
print(f"  剩余 WeChatChannelsProxy 引用数: {wc_refs}")

# ══════════════════════════════════════════════════════════════
# 11. 在合适位置注入 set_system_proxy 辅助函数 + 转码方法
# ══════════════════════════════════════════════════════════════
print("\n[11] 注入辅助函数 _set_system_proxy...")

SET_PROXY_FUNC = '''
def _set_system_proxy(port: int) -> str:
    """设置 Windows 系统代理（替代原 WeChatChannelsProxy.set_system_proxy）"""
    import winreg
    proxy_addr = f"127.0.0.1:{port}"
    try:
        reg_path = r"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings"
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
        reg_path = r"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        import ctypes
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
    except Exception:
        pass

'''

# 注入到 PLATFORM_FETCHERS 区块之前
INJECT_BEFORE = "\n# ═══════════════════════════════════════════════════════\n# 平台解析器注册表"
if INJECT_BEFORE in code:
    code = code.replace(INJECT_BEFORE, SET_PROXY_FUNC + INJECT_BEFORE)
    print("  注入完成")
else:
    print("  警告: 注入位置未找到")

# ══════════════════════════════════════════════════════════════
# 12. 删除 _on_toggle_system_proxy 中对 WeChatChannelsProxy 的其他引用
# ══════════════════════════════════════════════════════════════
print("\n[12] 清理 _on_toggle_system_proxy...")
# 替换 clear_system_proxy
code = code.replace(
    'WeChatChannelsProxy.clear_system_proxy()',
    '_clear_system_proxy()'
)
wc_refs2 = code.count('WeChatChannelsProxy')
print(f"  剩余 WeChatChannelsProxy 引用数: {wc_refs2}")
if wc_refs2 > 0:
    # 找出所有剩余引用
    for i, line in enumerate(code.splitlines()):
        if 'WeChatChannelsProxy' in line:
            print(f"  行 {i+1}: {line.strip()}")

# ══════════════════════════════════════════════════════════════
# 13. 注入转码方法到 LiveStreamFetcherApp
# ══════════════════════════════════════════════════════════════
print("\n[13] 注入转码方法到 LiveStreamFetcherApp...")

TRANSCODE_METHODS = '''
    # ─── HEVC → H264 转码功能 ───────────────────────────────────
    def _on_transcode_click(self):
        """打开 HEVC→H264 转码对话框"""
        self._open_transcode_dialog()

    def _open_transcode_dialog(self):
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

        url_var = tk.StringVar()
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
                        stream_url=url,
                        port=port,
                        referer="",
                        transcode_hevc=True,
                    )
                    proxy.start()
                    proxy_ref[0] = proxy
                    local_url = f"http://127.0.0.1:{proxy.port}/live"
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

'''

# 注入到 _on_toggle_system_proxy 方法之前
INJECT_BEFORE_UI = "    def _on_toggle_system_proxy(self):"
if INJECT_BEFORE_UI in code:
    code = code.replace(INJECT_BEFORE_UI, TRANSCODE_METHODS + "    " + INJECT_BEFORE_UI.strip())
    print("  转码方法注入完成")
else:
    print("  警告: 注入锚点未找到")

# ══════════════════════════════════════════════════════════════
# 14. 保存
# ══════════════════════════════════════════════════════════════
final_len = len(code.splitlines())
print(f"\n[14] 保存文件...")
print(f"  原始行数: {original_len}")
print(f"  修改后行数: {final_len}")
print(f"  删减: {original_len - final_len} 行")

with open(DST, "w", encoding="utf-8") as f:
    f.write(code)

print(f"\n[OK] Done: {DST}")
