"""分析小红书直播间前端 JS 如何加载流数据"""
import requests
import re

url = 'https://www.xiaohongshu.com/livestream/570223924590512327'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Referer': 'https://www.xiaohongshu.com/',
}
resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
text = resp.text

# 搜索所有 JS 文件 URL
scripts = re.findall(r'<script[^>]*src=["\']([^"\']+)["\']', text)
print(f"=== 找到 {len(scripts)} 个 JS 文件 ===")

# 下载主要的 bundle JS (index.js) 搜索 live/stream 相关 API
main_js = None
for s in scripts:
    if 'index.' in s and '.js' in s:
        main_js = s
        break

if main_js:
    print(f"\n下载主 JS: {main_js}")
    try:
        js_resp = requests.get(main_js, timeout=15)
        js_text = js_resp.text
        print(f"JS 大小: {len(js_text)} 字符")
        
        # 搜索 live 相关的 API 路径
        api_patterns = re.findall(r'["\'](/api/[^"\']*(?:live|stream|room|pull)[^"\']*)["\']', js_text, re.IGNORECASE)
        print(f"\n=== live 相关 API 路径 ({len(api_patterns)}) ===")
        for p in set(api_patterns):
            print(f"  {p}")
        
        # 搜索 pullUrl/hlsUrl/flvUrl 等字段名
        field_patterns = re.findall(r'["\'](\w*[Pp]ull[Uu]rl\w*)["\']', js_text)
        field_patterns += re.findall(r'["\'](\w*[Hh][Ll][Ss]\w*[Uu]rl\w*)["\']', js_text)
        field_patterns += re.findall(r'["\'](\w*[Ff][Ll][Vv]\w*[Uu]rl\w*)["\']', js_text)
        field_patterns += re.findall(r'["\'](\w*[Mm]3[Uu]8\w*[Uu]rl\w*)["\']', js_text)
        field_patterns += re.findall(r'["\'](\w*[Ss]tream[Uu]rl\w*)["\']', js_text)
        field_patterns += re.findall(r'["\'](\w*[Pp]ush[Uu]rl\w*)["\']', js_text)
        field_patterns += re.findall(r'["\'](\w*[Rr]tmp\w*)["\']', js_text)
        if field_patterns:
            print(f"\n=== 流地址字段名 ===")
            for f in set(field_patterns):
                print(f"  {f}")
        
        # 搜索 pullConfig 或类似配置
        config_patterns = re.findall(r'["\'](\w*[Pp]ull[Cc]onfig\w*)["\']', js_text)
        config_patterns += re.findall(r'["\'](\w*[Ll]ive[Ss]tream\w*)["\']', js_text)
        config_patterns += re.findall(r'["\'](\w*[Rr]oom[Dd]ata\w*)["\']', js_text)
        if config_patterns:
            print(f"\n=== 配置字段名 ===")
            for f in set(config_patterns):
                print(f"  {f}")
        
        # 搜索 WebSocket 相关
        ws_patterns = re.findall(r'["\']wss?://[^"\']+["\']', js_text)
        if ws_patterns:
            print(f"\n=== WebSocket URL ===")
            for w in ws_patterns[:10]:
                print(f"  {w}")
        
        # 搜索 edith API (小红书常用)
        edith_patterns = re.findall(r'["\']([^"\']*edith[^"\']*)["\']', js_text, re.IGNORECASE)
        if edith_patterns:
            print(f"\n=== edith 相关 ===")
            for e in set(edith_patterns)[:10]:
                print(f"  {e}")
                
    except Exception as e:
        print(f"下载 JS 失败: {e}")

# 搜索其他 JS 文件
for s in scripts:
    if 'live' in s.lower() or 'stream' in s.lower():
        print(f"\n  直播相关 JS: {s}")
