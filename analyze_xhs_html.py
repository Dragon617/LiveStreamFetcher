"""分析小红书直播间 HTML 结构"""
import requests
import re
import json

url = 'https://www.xiaohongshu.com/livestream/570223924590512327'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Referer': 'https://www.xiaohongshu.com/',
}
resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
text = resp.text

print(f"Status: {resp.status_code}")
print(f"Content length: {len(text)}")

# 搜索所有 window.xxx = 的模式
print("\n=== window.xxx 变量 ===")
vars_found = re.findall(r'window\.(\w+)\s*=\s*(.{1,100})', text)
for name, val in vars_found[:30]:
    print(f"  window.{name} = {val.strip()[:100]}")

# 搜索所有 __ 开头的变量
print("\n=== __xxx 变量 ===")
underscore_vars = re.findall(r'__(\w+)__', text)
if underscore_vars:
    from collections import Counter
    counts = Counter(underscore_vars)
    for name, count in counts.most_common(20):
        print(f"  __{name}__ 出现 {count} 次")
else:
    print("  未找到")

# 搜索含 livestream/live 相关的 script 内容
print("\n=== HTML 中的 livestream/live 相关内容 ===")
for i, line in enumerate(text.split('\n')):
    ll = line.lower()
    if any(kw in ll for kw in ['livestream', 'liveid', 'live_id', 'live_room', 'liveroom', 'pullurl', 'pull_url', 'streamurl', 'stream_url']):
        print(f"  line {i}: {line.strip()[:300]}")

# 搜索所有 script src
print("\n=== script src 列表 ===")
scripts = re.findall(r'<script[^>]*src=["\']([^"\']+)["\']', text)
for s in scripts[:20]:
    print(f"  {s}")

# 搜索 data- 属性
print("\n=== data- 属性 ===")
data_attrs = re.findall(r'data-([\w-]+)=["\']([^"\']+)["\']', text)
for name, val in data_attrs[:20]:
    if any(kw in name.lower() for kw in ['live', 'stream', 'room', 'id', 'state']):
        print(f"  data-{name} = {val[:200]}")

# 搜索 JSON 数据块 (各种可能的格式)
print("\n=== 搜索 JSON 数据块 ===")
json_patterns = [
    r'window\.(\w+)\s*=\s*(\{[^;]{1,500})',
    r'__INITIAL_(\w+)__\s*=\s*(\{[^;]{1,500})',
    r'__NEXT_DATA__\s*=\s*(\{[^;]+?\})\s*;?',
]
for pattern in json_patterns:
    matches = re.findall(pattern, text)
    if matches:
        for name, val in matches[:5]:
            print(f"  {name}: {val[:200]}")

# 尝试搜索 embedState 或 hydrationData 等 React/Next.js 常见模式
print("\n=== 搜索 hydration/state 脚本 ===")
state_scripts = re.findall(r'<script[^>]*>([^<]*(?:state|hydrate|initial|config|props)[^<]*)</script>', text, re.IGNORECASE)
for s in state_scripts[:5]:
    print(f"  {s[:300]}")

# 搜索 meta 标签中的有用信息
print("\n=== meta 标签 ===")
metas = re.findall(r'<meta[^>]+>', text)
for m in metas[:15]:
    print(f"  {m[:200]}")
