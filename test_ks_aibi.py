"""从 profile 页面提取 authorInfoById"""
import sys, json, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import requests, time

session = requests.Session()
ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
session.headers.update({'User-Agent': ua})

session.get('https://live.kuaishou.com/', timeout=10)
time.sleep(1)

r = session.get('https://live.kuaishou.com/profile/ltsx1219', timeout=15)

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
    state = json.loads(json_str)
    
    # authorInfoById
    aibi = state.get('authorInfoById', {})
    print("authorInfoById:", json.dumps(aibi, ensure_ascii=False)[:2000])
    
    # followBtn
    fb = state.get('followBtn', {})
    print("\nfollowBtn:", json.dumps(fb, ensure_ascii=False)[:1000])
    
    # 完整 state dump（排除大字段）
    for key, val in state.items():
        val_str = json.dumps(val, ensure_ascii=False)
        if len(val_str) > 200:
            print(f"\n{key} ({len(val_str)} chars): {val_str[:200]}...")
        else:
            print(f"\n{key}: {val_str}")
