"""深度搜索 livestreamService 的 API 逻辑"""
import requests
import re

js_url = "https://fe-static.xhscdn.com/formula-static/xhs-pc-web/public/resource/js/index.7bc6aee1.js"
headers = {'User-Agent': 'Mozilla/5.0'}
js_text = requests.get(js_url, headers=headers, timeout=30).text

# 搜索 livestreamService 附近的代码
idx = js_text.find("livestreamService")
if idx >= 0:
    # 提取前后 2000 字符
    start = max(0, idx - 500)
    end = min(len(js_text), idx + 2000)
    print("=== livestreamService 上下文 ===")
    print(js_text[start:end])
    print("\n\n")

# 搜索所有 API 路径（更广泛）
print("=== 所有 API 路径 ===")
api_paths = re.findall(r'["\'](/api/[^"\']+)["\']', js_text)
for p in sorted(set(api_paths)):
    print(f"  {p}")

# 搜索 pullConfig 相关
print("\n=== pullConfig 上下文 ===")
for m in re.finditer(r'pullConfig', js_text):
    start = max(0, m.start() - 200)
    end = min(len(js_text), m.end() + 200)
    print(f"...{js_text[start:end]}...")
    print("---")

# 搜索 liveStream 相关的 state 操作
print("\n=== liveStream state 操作 ===")
for m in re.finditer(r'liveStream', js_text):
    start = max(0, m.start() - 150)
    end = min(len(js_text), m.end() + 150)
    snippet = js_text[start:end].replace('\n', ' ')
    print(f"  [{m.start()}]: ...{snippet}...")
