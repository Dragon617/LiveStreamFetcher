"""尝试快手 V2 API 和其他方法获取直播流"""
import sys, json, io, re, time, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import requests

session = requests.Session()
ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# 方法1: 快手 open API - visionWebLiveRoom
print("=== Method 1: visionWebLiveRoom (V2 GraphQL) ===")
# 首先建立 cookie
session.headers.update({'User-Agent': ua})
r = session.get('https://live.kuaishou.com/', timeout=10)
time.sleep(1)

# 检查 cookie
did = session.cookies.get('did', '')
print(f"  did cookie: {did}")

# 方法2: 快手 Web 直播间 API v2
print("\n=== Method 2: V2 API ===")
# 这个 API 通过用户名获取直播间信息
v2_url = 'https://live.kuaishou.com/live_api/room/info'
r2 = session.get(v2_url, params={'user': 'ltsx1219'}, timeout=10)
print(f"  Status: {r2.status_code}")
print(f"  Response: {r2.text[:500]}")

# 方法3: 快手 Web 直播间 enterroom API
print("\n=== Method 3: enterroom API ===")
enter_url = 'https://live.kuaishou.com/live_api/liveroom/enterroom'
# POST 请求
r3 = session.post(enter_url, json={"principalId": "ltsx1219"}, timeout=10)
print(f"  POST Status: {r3.status_code}")
print(f"  Response: {r3.text[:500]}")

# GET 请求
r4 = session.get(enter_url, params={"principalId": "ltsx1219"}, timeout=10)
print(f"  GET Status: {r4.status_code}")
print(f"  Response: {r4.text[:500]}")

# 方法4: 快手的 user profile API
print("\n=== Method 4: user profile API ===")
profile_url = 'https://live.kuaishou.com/live_api/user/public'
r5 = session.get(profile_url, params={'user': 'ltsx1219'}, timeout=10)
print(f"  Status: {r5.status_code}")
print(f"  Response: {r5.text[:500]}")

# 方法5: 从 reco 中找到 ltsx1219 的 principalId
print("\n=== Method 5: reco with search ===")
reco_url = 'https://live.kuaishou.com/live_api/liveroom/reco'
r6 = session.get(reco_url, params={"count": "30"}, timeout=10)
print(f"  Status: {r6.status_code}")
try:
    data = r6.json()
except:
    data = {}
reco_list = data.get("data", {}).get("list", [])
print(f"  Reco items: {len(reco_list)}")
for room in reco_list:
    author = room.get("author", {})
    name = author.get("name", "")
    aid = author.get("id", "")
    living = author.get("living", False)
    if living:
        print(f"  *** LIVE: {name} (id={aid})")
    if 'ltsx' in name.lower():
        print(f"  >>> MATCH: {name} (id={aid})")

# 方法6: 快手搜索 API
print("\n=== Method 6: Search API ===")
search_url = 'https://live.kuaishou.com/live_api/search/live'
r7 = session.get(search_url, params={"keyword": "ltsx1219", "count": "5"}, timeout=10)
print(f"  Status: {r7.status_code}")
print(f"  Response: {r7.text[:500]}")

# 方法7: GraphQL
print("\n=== Method 7: GraphQL ===")
gql_url = 'https://live.kuaishou.com/graphql'
gql_body = {
    "operationName": "sensitiveWordQuery",
    "query": "query sensitiveWordQuery($input: SensitiveWordInput!) { sensitiveWordQuery(input: $input) { result } }",
    "variables": {"input": {"keyword": "ltsx1219"}}
}
r8 = session.post(gql_url, json=gql_body, timeout=10)
print(f"  Status: {r8.status_code}")
print(f"  Response: {r8.text[:300]}")

# 方法8: 看看其他快手URL格式
print("\n=== Method 8: Try /profile/ URL ===")
r9 = session.get('https://live.kuaishou.com/profile/ltsx1219', timeout=10, allow_redirects=True)
print(f"  Final URL: {r9.url}")
print(f"  Status: {r9.status_code}")
# 提取 ID
m = re.search(r'principalId[=:]["\']?(\w+)', r9.text)
if m:
    print(f"  Found principalId: {m.group(1)}")

# 方法9: 从 mobile share page 获取
print("\n=== Method 9: Mobile share page ===")
share_url = 'https://m.gifshow.com/fw/photo/ltsx1219'
r10 = session.get(share_url, headers={
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)'
}, timeout=10, allow_redirects=True)
print(f"  Final URL: {r10.url}")
print(f"  Status: {r10.status_code}")
# 查找 userId
for pat in [r'"userId"\s*:\s*"?(\d+)"?', r'"principalId"\s*:\s*"?(\d+)"?', r'"id"\s*:\s*"?(\d+)"?']:
    matches = re.findall(pat, r10.text)
    if matches:
        print(f"  Pattern [{pat[:30]}]: {matches[:5]}")
