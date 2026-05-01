# LiveStreamFetcher v5.0 — 项目总览

> 多平台直播视频流获取工具  
> 作者：LONGSHAO（龙哥）  
> 版本：v5.0  
> 开发周期：2026-04-09 ~ 2026-04-10（约 11 小时）

---

## 一、项目简介

LiveStreamFetcher 是一款桌面工具，用于从主流直播平台获取实时视频流链接（M3U8/FLV/MP4），方便用户通过 OBS 等推流工具进行直播录制或转播。

用户只需粘贴直播间链接，软件会自动识别平台、解析视频流地址，并提供一键复制和 OBS 代理功能。

---

## 二、支持平台

| 平台 | 解析方式 | 登录要求 | OBS 支持 |
|------|---------|---------|---------|
| 抖音 | HTTP 请求 | 不需要 | 直接可用 |
| 快手 | Playwright 浏览器自动化 | 需扫码登录 | 直接可用 |
| 小红书 | Playwright 浏览器自动化 | 需扫码登录 | H.264 直接可用，HEVC 需代理 |
| 淘宝直播 | Playwright 浏览器自动化 | 需扫码登录 | 需本地代理（Referer 鉴权） |
| 视频号 | HTTP 请求 + yt-dlp 降级 | 不需要 | 直接可用 |

---

## 三、核心功能

### 3.1 直播流解析
- 自动识别 5 个平台的直播间 URL
- 优先使用平台专属解析器，失败后降级到 yt-dlp
- 输出多种清晰度/格式的流链接（原画、高清、标清、竖屏、横屏等）
- 智能去重、排序、分类展示

### 3.2 OBS 推流支持
- **淘宝直播**：流链接含 auth_key + Referer 校验，软件内置本地 HTTP 代理，自动注入请求头，OBS 填代理地址即可
- **小红书 HEVC 流**：通过嵌入式 ffmpeg 自动转码为 H.264，OBS 兼容
- **其他平台**：流链接可直接粘贴到 OBS 使用

### 3.3 登录状态管理
- 快手 / 小红书 / 淘宝直播三个平台均支持登录状态持久化
- 状态栏实时显示登录状态（绿色已登录 / 灰色未登录 / 橙色失效）
- 点击状态标签可查看 Cookie 路径、退出登录或重新登录
- 登录态通过 SQLite 查询 Cookie 精准检测

### 3.4 启动密码保护
- 软件启动前需输入密码，密码存储在语雀云端文档（实时生效，无需重新打包）
- 密码缓存 30 分钟，避免频繁请求文档

### 3.5 嵌入式浏览器方案
- 将 Playwright Chromium 浏览器打包进 EXE（约 231MB）
- 首次运行自动释放到 `%APPDATA%/LiveStreamFetcher/embedded_chromium/`
- 检测顺序：EXE 同目录 → PyInstaller _MEIPASS → 已释放的 AppData → 系统 Chrome / Edge

### 3.6 嵌入式 ffmpeg
- 将 ffmpeg.exe 打包进 EXE（约 100MB），用于 HEVC 转 H.264
- 查找策略与 Chromium 一致

---

## 四、技术架构

### 4.1 代码规模

| 指标 | 数值 |
|------|------|
| 主程序行数 | 5,974 行 |
| 字符数 | 232,506 字符 |
| 文件大小 | 257,641 字节（252 KB） |
| 辅助脚本 | 23 个 |
| 打包后 EXE | 约 461 MB |

### 4.2 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.14 |
| GUI 框架 | tkinter |
| 浏览器自动化 | Playwright (sync_api) |
| 视频下载降级 | yt-dlp |
| HTTP 请求 | requests |
| 本地代理 | 内置 HTTP 服务器（socket） |
| 视频转码 | ffmpeg（嵌入式） |
| 打包工具 | PyInstaller（单文件模式） |
| 圆角窗口 | ctypes + DWM API (Windows 10+) |

### 4.3 核心类结构

```
live_stream_fetcher.py (5,974 行)
├── FetchUserError          # 自定义异常
├── detect_platform()       # URL 平台识别
├── fetch_douyin()          # 抖音解析器
├── fetch_kuaishou()        # 快手解析器（HTTP + Playwright）
├── fetch_xiaohongshu()     # 小红书解析器（Playwright）
├── fetch_taobao_live()     # 淘宝直播解析器（Playwright）
├── fetch_wechat_channels() # 视频号解析器
├── fetch_streams_ytdlp()   # yt-dlp 通用降级
├── extract_streams()       # 统一入口（自动识别平台 → 专属解析 → 降级）
├── Colors                  # 主题色定义（暗色主题）
├── LocalStreamProxy        # 本地流代理（HEVC 转码 / Referer 注入）
├── _StreamProxyHTTPServer  # 代理 HTTP 服务器
├── LiveStreamFetcherApp    # 主应用 GUI（tkinter）
│   ├── _build_ui()                # 构建 UI
│   ├── _on_fetch()                # 获取流链接
│   ├── _show_result()             # 展示结果
│   ├── _render_filtered_streams() # 渲染流卡片
│   ├── _create_stream_card()      # 创建单个流卡片
│   ├── _copy_single() / _on_copy_all()  # 复制链接
│   ├── _start_stream_proxy()      # 启动本地代理
│   ├── _refresh_xxx_login_display()  # 刷新登录状态
│   ├── _do_xxx_relogin()          # 重新登录
│   └── ...
└── PasswordGate             # 启动密码验证界面
```

---

## 五、打包与分发

### 5.1 打包命令

```bash
C:\Python314\python.exe -m PyInstaller --clean LiveStreamFetcher.spec
```

### 5.2 打包配置要点

- **single-file 模式**：所有依赖打包成一个 EXE
- **UPX 压缩**：启用（`upx=True`）
- **console 模式**：保留控制台窗口（调试用）
- **hiddenimports**：必须显式包含 `playwright`、`playwright.sync_api`、`greenlet`（动态导入检测不到）
- **datas**：打包嵌入式 Chromium（`chromium-1208/chrome-win64`）和 ffmpeg（`C:\ffmpeg\bin`）

### 5.3 PyInstaller 关键依赖

```python
datas = [
    ('chromium-1208/chrome-win64', 'embedded_chromium'),  # ~231MB
    ('C:\\ffmpeg\\bin', 'embedded_ffmpeg'),               # ~100MB
]
hiddenimports = [
    'playwright', 'playwright.sync_api', 'greenlet',
    'yt_dlp', 'yt_dlp.extractor', ...
]
```

### 5.4 输出

- EXE 路径：`dist\LiveStreamFetcher.exe`
- EXE 大小：约 461 MB

---

## 六、平台解析策略详解

### 6.1 抖音（douyin.com / live.douyin.com）
- **纯 HTTP 请求**，无需浏览器
- 提取 `web_rid` → 调用抖音 Web API → 解析 `stream_url` 中的 `play_addr`
- 支持 FLV 和 HLS 格式

### 6.2 快手（live.kuaishou.com）
- **双模式**：先尝试 HTTP 请求，失败后 Playwright 浏览器自动化
- HTTP 模式：提取 `roomId` → 调用快手 API → 解析 `playUrls`
- Playwright 模式：`launch_persistent_context` 保持登录态 → 监听网络请求中的 `.m3u8` / `.flv`
- **风控处理**：检测 `errorType.type` 自动刷新页面重试
- **登录持久化**：`%APPDATA%/LiveStreamFetcher/kuaishou_browser_data/`

### 6.3 小红书（xiaohongshu.com）
- **Playwright 浏览器自动化**（2026-04 前端改版后必须）
- 旧方案（已失效）：从 `__INITIAL_STATE__` SSR 数据直接提取
- 新方案：监听 `edith.xiaohongshu.com` API 响应 → 提取 `roomInfo.pullConfig` → 解析流地址
- 流地址域名：`live-source-play.xhscdn.com`
- **HEVC 检测**：自动检测编码格式，HEVC 流需 ffmpeg 转码

### 6.4 淘宝直播（tbzb.taobao.com）
- **Playwright 浏览器自动化**（纯请求和 yt-dlp 均不支持）
- 监听 `alicdn.com` 域名的 m3u8/flv 请求 + `mtop.taobao.*` API 响应
- **OBS 本地代理**：alicdn 链接有 auth_key + Referer 校验，内置代理注入请求头
- 登录页：`login.taobao.com/member/login.jhtml`

### 6.5 视频号（channels.weixin.qq.com / weixin.qq.com）
- **HTTP 请求 + yt-dlp 降级**
- 提取直播间 ID → 请求视频号 API → 解析流地址

---

## 七、登录状态管理

### 7.1 状态检测机制

通过 SQLite 查询浏览器 Cookie 数据库，精准判断是否已登录：

```sql
SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%kuaishou.com%'
```

检测文件路径：
| 平台 | Cookie 数据库路径 |
|------|----------------|
| 快手 | `%APPDATA%/LiveStreamFetcher/kuaishou_browser_data/Default/Cookies` |
| 淘宝 | `%APPDATA%/LiveStreamFetcher/taobao_browser_data/Default/Cookies` |
| 小红书 | `%APPDATA%/LiveStreamFetcher/xiaohongshu_browser_data/Default/Cookies` |

### 7.2 状态显示

- **绿色圆点**：已登录（Cookie 有效）
- **灰色圆点**：未登录
- **橙色圆点**：登录可能失效（Cookie 过期）

点击状态标签弹出菜单：
- 查看 Cookie 存储路径
- 退出登录（清除 Cookie）
- 重新登录（打开浏览器扫码）

---

## 八、本地代理（LocalStreamProxy）

### 8.1 架构

```
OBS ──→ 127.0.0.1:xxxxx/live ──→ LocalStreamProxy ──→ 目标 CDN
                                        │
                                        ├── 注入 Referer 头（淘宝）
                                        ├── HEVC → H.264 转码（小红书）
                                        └── 流式转发（iter_content）
```

### 8.2 代理模式

| 场景 | 代理行为 |
|------|---------|
| 淘宝直播 | 直通模式，仅注入 Referer 头 |
| 小红书 HEVC 流 | ffmpeg 转码模式（HEVC → H.264） |
| 小红书 H.264 流 | 不需要代理，直接使用 |

### 8.3 端口分配

随机端口（`port=0`），由操作系统分配，避免端口冲突。

---

## 九、UI 设计

### 9.1 界面风格
- **暗色主题**（深色背景 + 蓝色/白色文字）
- **圆角窗口**（Windows 10+ DWM API）
- **最小窗口**：860 x 680
- **默认窗口**：960 x 780

### 9.2 主要组件

| 区域 | 内容 |
|------|------|
| 顶部标题栏 | 软件名称 + 版本号 + 作者 |
| URL 输入区 | 输入框 + 获取按钮 + 代理开关 |
| 状态栏 | 快手/淘宝/小红书登录状态 + 作者信息 |
| 结果展示区 | 分类标签 + 流卡片列表（支持滚动） |
| 流卡片 | 清晰度标签 + 格式标签 + 复制按钮 + OBS 按钮 |

### 9.3 平台指引卡片
- 快手：蓝色主题，提示需要扫码登录
- 淘宝直播：橙色主题，提示需要扫码登录
- 小红书：红色主题，提示需要扫码登录

---

## 十、密码验证（PasswordGate）

### 10.1 验证流程

```
启动 → 隐藏主窗口 → 显示密码输入框 → 用户输入密码
  → 后台线程获取云端密码 → 比对
    → 匹配成功 → 销毁密码窗口 → 显示主窗口
    → 匹配失败 → 显示错误提示，清空输入
```

### 10.2 密码来源
- **语雀分享页**：`https://www.yuque.com/r/note/11037a5a-b85f-4c41-bf08-2fe003b7afcd`
- **提取方式**：Playwright headless 模式打开语雀页面 → DOM 提取文本 → 按关键词匹配密码
- **缓存策略**：30 分钟缓存，避免频繁请求

### 10.3 安全特性
- 控制台不打印密码明文（只显示长度）
- 验证失败时不暴露云端密码
- 线程超时保护（25 秒），防止 Playwright 卡死

---

## 十一、开发历程

### 2026-04-09（Day 1）

| 时间 | 里程碑 |
|------|--------|
| 13:40 | 项目创建，基础框架搭建 |
| 下午 | 抖音/视频号 HTTP 解析器、快手解析器、yt-dlp 降级 |
| 下午 | Playwright 浏览器自动化（快手/小红书/淘宝） |
| 下午 | PyInstaller 打包 + 嵌入式 Chromium 方案 |
| 晚上 | 快手登录状态管理（SQLite Cookie 检测） |
| 晚上 | 淘宝直播解析 + OBS 本地代理 |
| 晚上 | 小红书 2026-04 前端改版适配（Vue SPA + edith API） |
| 晚上 | HEVC 转码代理、三平台登录误判修复 |
| 深夜 | 嵌入式 ffmpeg、版本号统一 v5.0 |

### 2026-04-10（Day 2）

| 时间 | 里程碑 |
|------|--------|
| 凌晨 | 启动密码验证功能（腾讯文档 → 语雀） |
| 凌晨 | 密码验证按钮无响应 bug 修复（5 层修复） |
| 凌晨 | TclError 修复（控件存活检查） |
| 凌晨 | 密码明文泄露修复（控制台安全加固） |

---

## 十二、项目文件结构

```
LiveStreamFetcher/
├── live_stream_fetcher.py      # 主程序（5,974 行 / 252 KB）
├── LiveStreamFetcher.spec      # PyInstaller 打包配置
├── build_exe.py                # 辅助打包脚本
│
├── analyze_flv.py              # FLV 文件分析
├── analyze_xhs_api.py          # 小红书 API 分析
├── analyze_xhs_html.py         # 小红书 HTML 分析
├── analyze_xhs_js.py           # 小红书 JS 分析
├── analyze_xhs_service.py      # 小红书 Service Worker 分析
├── debug_flv2.py               # FLV 调试
├── debug_flv3.py               # FLV 调试 v3
├── debug_xhs_live.py           # 小红书直播调试
├── extract_xhs_state.py        # 小红书 __INITIAL_STATE__ 提取
├── find_api_path.py            # API 路径查找
├── hexdump_flv.py              # FLV 十六进制转储
├── inspect_room_data.py        # 直播间数据检查
├── parse_flv_meta.py           # FLV metadata 解析
├── parse_metadata.py           # 通用 metadata 解析
├── check_cookies.py            # Cookie 检查工具
├── check_xhs_login.py          # 小红书登录检查
├── test_ffmpeg_direct.py       # ffmpeg 直通测试
├── test_ffmpeg_transcode.py    # ffmpeg 转码测试
├── test_ks_aibi.py             # 快手 AI 直播间测试
├── test_ks_pid.py              # 快手 PID 测试
├── test_ks_profile.py          # 快手 Profile 测试
├── test_ks_v2.py               # 快手 v2 测试
│
├── dist/
│   └── LiveStreamFetcher.exe   # 打包输出（~461 MB）
├── build/                      # PyInstaller 构建缓存
└── __pycache__/                # Python 缓存
```

---

## 十三、关键踩坑记录

### 13.1 PyInstaller
- `playwright` / `playwright.sync_api` / `greenlet` 必须显式加入 hiddenimports，否则打包后运行报 `ModuleNotFoundError`

### 13.2 Playwright
- `launch_persistent_context` 登录后不一定生成 `Default/Cookies` 文件（有些只生成 Login Data / History），不能用单文件存在性判断登录态
- 快手风控：检测到 `errorType.type` 需自动 `page.reload()`，等待后再监听
- 小红书 2026-04 改版：`__INITIAL_STATE__` 从 SSR 变为 SPA 占位，必须监听 `edith.xiaohongshu.com` API

### 13.3 淘宝直播
- 淘宝直播纯请求和 yt-dlp 都无法获取流（SPA + API 签名），只能用 Playwright
- alicdn 链接有 auth_key + Referer 校验，OBS 无法自定义请求头，必须走本地代理

### 13.4 Tkinter
- `Toplevel.destroy()` 后，已调度的 `root.after()` 回调仍会执行，操作已销毁控件会报 `TclError`，需用 `winfo_exists()` 检查
- 按钮在后台线程中修改状态后，`activebackground` / `activeforeground` 可能不刷新，需在 `_set_loading(False)` 中强制重新设置

### 13.5 密码验证
- 腾讯文档 DOM 结构复杂，提取到的文本可能是说明文字而非密码值，改用语雀分享页更可靠
- `__INITIAL_STATE__` 中含 `undefined` 值（JS 保留字），JSON 解析时需替换为 `null`

---

## 十四、运行环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10+（圆角窗口需要 DWM API） |
| Python | 3.14（开发环境） |
| 运行时 | 无需安装 Python，EXE 自包含 |
| 磁盘空间 | 首次运行需约 400MB（释放 Chromium + ffmpeg 到 AppData） |
| 网络 | 需要访问各直播平台和语雀文档（密码验证） |

---

## 十五、已知限制

1. **快手/淘宝/小红书** 需要弹出浏览器窗口扫码登录，无法完全无头运行
2. **小红书 HEVC 流** 转码需要 ffmpeg，增加 CPU 开销
3. **EXE 体积大**（461MB），主要因为嵌入了 Chromium + ffmpeg
4. **淘宝直播** 必须通过本地代理转发，增加一层网络跳转
5. **密码验证** 依赖语雀文档可访问性，网络异常时无法启动软件

---

*文档生成时间：2026-04-10*
