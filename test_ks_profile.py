"""从 profile 页面获取 principalId"""
import sys, json, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import requests, time

session = requests.Session()
ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
session.headers.update({'User-Agent': ua})

# 先获取首页 cookie
session.get('https://live.kuaishou.com/', timeout=10)
time.sleep(1)

# 访问 profile 页面
r = session.get('https://live.kuaishou.com/profile/ltsx1219', timeout=15)
print(f"Status: {r.status_code}, len: {len(r.text)}")

# 提取 __INITIAL_STATE__
m = re.search(r'window\.__INITIAL_STATE__\s*=\s*', r.text)
if m:
    start = m.end()
    brace_count = 0
    json_str = ''
    for ch in r.text[start:]:
        if ch == '{':
            brace_count += 1
        elif ch == '}':
            brace_count -= 1
        json_str += ch
        if brace_count == 0:
            break
    try:
        state = json.loads(json_str)
        print("Top keys:", list(state.keys()))
        
        # 检查 userProfile
        up = state.get('userProfile', {})
        print("userProfile:", json.dumps(up, ensure_ascii=False)[:1000])
        
        # 检查 user
        user = state.get('user', {})
        print("\nuser:", json.dumps(user, ensure_ascii=False)[:1000])
        
        # 检查是否有主播信息
        for key in ['profile', 'liveroom', 'host', 'creator']:
            if key in state:
                print(f"\n{key}:", json.dumps(state[key], ensure_ascii=False)[:500])
        
        # 全局搜索 principalId
        full_state_str = json.dumps(state, ensure_ascii=False)
        ids = re.findall(r'"principalId"\s*:\s*"?(\w+)"?', full_state_str)
        print(f"\nprincipalId found: {ids}")
        
        # 搜索所有 10 位以上的数字 ID
        long_ids = re.findall(r'"id"\s*:\s*"?(\w{10,})"?', full_state_str)
        print(f"Long IDs found: {long_ids[:10]}")
    except Exception as e:
        print(f"Parse error: {e}")
else:
    print("No __INITIAL_STATE__")
    # 看看页面有什么
    for pattern in [r'principalId["\s:=]+["\']?(\w+)', r'userId["\s:=]+["\']?(\w+)', r'"id"\s*:\s*"?(\d{8,})"?']:
        matches = re.findall(pattern, r.text)
        if matches:
            print(f"Pattern [{pattern[:30]}]: {matches[:5]}")
