"""搜索 edith live API base URL 和具体端点"""
import requests
import re

js_url = "https://fe-static.xhscdn.com/formula-static/xhs-pc-web/public/resource/js/index.7bc6aee1.js"
headers = {'User-Agent': 'Mozilla/5.0'}
js_text = requests.get(js_url, headers=headers, timeout=30).text

# 搜索 getEdithLiveApiBaseUrl
print("=== getEdithLiveApiBaseUrl ===")
for m in re.finditer(r'getEdithLiveApiBaseUrl', js_text):
    start = max(0, m.start() - 100)
    end = min(len(js_text), m.end() + 300)
    print(f"[{m.start()}]: {js_text[start:end]}")
    print("---")

# 搜索 $.Mn (getRoomInfo 的 API 函数)
print("\n=== $.Mn 定义 ===")
# 搜索 Mn= 或 .Mn= 
for m in re.finditer(r'\.Mn\s*=\s*', js_text):
    start = max(0, m.start() - 50)
    end = min(len(js_text), m.end() + 300)
    print(f"[{m.start()}]: {js_text[start:end]}")
    print("---")

# 搜索 edith base URL
print("\n=== edith base URL ===")
for m in re.finditer(r'edith', js_text, re.IGNORECASE):
    start = max(0, m.start() - 100)
    end = min(len(js_text), m.end() + 200)
    snippet = js_text[start:end].replace('\n', ' ')
    if 'url' in snippet.lower() or 'base' in snippet.lower() or 'http' in snippet.lower() or 'api' in snippet.lower():
        print(f"[{m.start()}]: {snippet[:300]}")
        print("---")

# 搜索直播 API 路径模式
print("\n=== /live/ 或 /room/ API ===")
for m in re.finditer(r'["\']([^"\']*(?:/live/|/room/)[^"\']*)["\']', js_text):
    print(f"  {m.group(1)}")

# 搜索 getRoomInfo 调用
print("\n=== getRoomInfo 调用 ===")
for m in re.finditer(r'getRoomInfo', js_text):
    start = max(0, m.start() - 100)
    end = min(len(js_text), m.end() + 200)
    snippet = js_text[start:end].replace('\n', ' ')
    print(f"  [{m.start()}]: {snippet[:300]}")
    print("---")
