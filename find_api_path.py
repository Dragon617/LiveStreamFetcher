"""找到 $.Mn 对应的具体 HTTP 方法和路径"""
import requests
import re

js_url = "https://fe-static.xhscdn.com/formula-static/xhs-pc-web/public/resource/js/index.7bc6aee1.js"
headers = {'User-Agent': 'Mozilla/5.0'}
js_text = requests.get(js_url, headers=headers, timeout=30).text

# 搜索 $ = n(xxxxx) 找到模块 ID
# 然后搜索 Mn: 的定义
print("=== 搜索 Mn: 方法定义 ===")
for m in re.finditer(r'Mn\s*[:(]', js_text):
    start = max(0, m.start() - 50)
    end = min(len(js_text), m.end() + 300)
    snippet = js_text[start:end]
    if 'get' in snippet.lower() or 'room' in snippet.lower() or 'post' in snippet.lower() or 'fetch' in snippet.lower() or 'url' in snippet.lower() or 'path' in snippet.lower():
        print(f"[{m.start()}]: {snippet[:350]}")
        print("---")

# 搜索 get 方法 + room 相关的 URL 构造
print("\n=== 搜索 room info API URL ===")
for m in re.finditer(r'room.*info|info.*room', js_text, re.IGNORECASE):
    start = max(0, m.start() - 200)
    end = min(len(js_text), m.end() + 200)
    snippet = js_text[start:end].replace('\n', ' ')
    if 'url' in snippet.lower() or 'path' in snippet.lower() or 'api' in snippet.lower() or 'http' in snippet.lower() or 'get' in snippet.lower():
        print(f"  [{m.start()}]: {snippet[:400]}")
        print("---")

# 搜索 /api/sns/ 中包含 live 的路径
print("\n=== 搜索 api 中 live/room 路径 ===")
for m in re.finditer(r'["\']([^"\']*(?:live|room)[^"\']*)["\']', js_text):
    url = m.group(1)
    if 'http' in url or '/' in url:
        print(f"  {url}")
