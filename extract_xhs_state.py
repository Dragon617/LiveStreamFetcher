"""正确提取小红书 __INITIAL_STATE__ - 使用深度括号匹配"""
import requests
import json

url = 'https://www.xiaohongshu.com/livestream/570223924590512327'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Referer': 'https://www.xiaohongshu.com/',
}
resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
text = resp.text

# 找到 __INITIAL_STATE__= 的位置
marker = '__INITIAL_STATE__='
idx = text.find(marker)
if idx < 0:
    print("找不到 __INITIAL_STATE__")
    exit()

start = idx + len(marker)
print(f"找到 __INITIAL_STATE__，起始位置: {start}")

# 深度括号匹配
depth = 0
end = start
in_string = False
string_char = None
escaped = False

for i in range(start, min(start + 2000000, len(text))):
    c = text[i]
    
    if escaped:
        escaped = False
        continue
    
    if c == '\\' and in_string:
        escaped = True
        continue
    
    if c in ('"', "'") and not in_string:
        in_string = True
        string_char = c
        continue
    
    if in_string and c == string_char:
        in_string = False
        continue
    
    if in_string:
        continue
    
    if c == '{':
        depth += 1
    elif c == '}':
        depth -= 1
        if depth == 0:
            end = i + 1
            break

raw_json = text[start:end]
print(f"提取长度: {len(raw_json)}")

if depth != 0:
    print(f"警告: 括号未平衡, depth={depth}")

# 保存 raw
with open("xhs_raw_state.json", "w", encoding="utf-8") as f:
    f.write(raw_json)
print("Raw saved to xhs_raw_state.json")

try:
    state = json.loads(raw_json)
    print(f"JSON 解析成功! 顶层 keys: {list(state.keys())}")
except json.JSONDecodeError as e:
    print(f"JSON 解析失败: {e}")
    print(f"错误位置附近: ...{raw_json[max(0,e.pos-50):e.pos+50]}...")
    
    # 尝试修复：找到 undefined 替换为 null
    fixed = raw_json.replace('undefined', 'null')
    try:
        state = json.loads(fixed)
        print(f"修复后解析成功! 顶层 keys: {list(state.keys())}")
    except json.JSONDecodeError as e2:
        print(f"修复后仍然失败: {e2}")
        state = None

if state and isinstance(state, dict):
    # 搜索 live 相关
    def deep_search(obj, target_key, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == target_key:
                    yield path + "." + k, v
                yield from deep_search(v, target_key, path + "." + k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from deep_search(v, target_key, path + f"[{i}]")
    
    for target in ["liveInfo", "liveRoom", "liveData", "liveStream", "pullUrl", "hlsUrl", "flvUrl", "m3u8Url", "streamUrl", "pushUrl", "rtmpUrl"]:
        found = list(deep_search(state, target))
        if found:
            print(f"\n=== {target} 找到 {len(found)} 处 ===")
            for path, val in found[:5]:
                if isinstance(val, dict):
                    print(f"  {path}: keys={list(val.keys())[:20]}")
                elif isinstance(val, str):
                    print(f"  {path} = {val[:300]}")
                else:
                    print(f"  {path} = {str(val)[:200]}")
    
    # 搜索所有含 live 的 key
    def find_all_live_keys(obj, path="", depth=0):
        results = []
        if isinstance(obj, dict) and depth < 8:
            for k, v in obj.items():
                kl = k.lower()
                if any(x in kl for x in ['live', 'stream', 'pull', 'hls', 'flv', 'm3u8', 'push', 'rtmp']):
                    if isinstance(v, str):
                        results.append((f"{path}.{k}", v[:200]))
                    elif isinstance(v, (int, float, bool)):
                        results.append((f"{path}.{k}", str(v)))
                    elif isinstance(v, dict):
                        results.append((f"{path}.{k}", f"dict({len(v)} keys): {list(v.keys())[:15]}"))
                    elif isinstance(v, list):
                        results.append((f"{path}.{k}", f"list({len(v)})"))
                    else:
                        results.append((f"{path}.{k}", type(v).__name__))
                if isinstance(v, (dict, list)) and depth < 6:
                    results.extend(find_all_live_keys(v, f"{path}.{k}", depth+1))
        return results
    
    live_keys = find_all_live_keys(state)
    print(f"\n=== 所有 live/stream/pull 相关 key ({len(live_keys)}) ===")
    for path, val in live_keys:
        print(f"  {path} = {val}")
