"""查看 liveStream.roomData 的完整结构"""
import json

with open("xhs_raw_state.json", "r", encoding="utf-8") as f:
    text = f.read()

# 替换 undefined
text = text.replace('undefined', 'null')
state = json.loads(text)

room_data = state.get("liveStream", {}).get("roomData", {})
print("=== roomData 结构 ===")
print(json.dumps(room_data, ensure_ascii=False, indent=2, default=str)[:5000])

# 特别查看 roomInfo
room_info = room_data.get("roomInfo", {})
print("\n=== roomInfo 结构 ===")
print(json.dumps(room_info, ensure_ascii=False, indent=2, default=str)[:5000])

# 搜索所有可能的 URL
def find_urls(obj, path=""):
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.startswith("http"):
                results.append((f"{path}.{k}", v[:200]))
            elif isinstance(v, str) and any(x in v for x in ['.flv', '.m3u8', '.mp4', 'rtmp:', 'pullUrl', 'pushUrl']):
                results.append((f"{path}.{k}", v[:200]))
            elif isinstance(v, dict):
                results.extend(find_urls(v, f"{path}.{k}"))
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, dict):
                        results.extend(find_urls(item, f"{path}.{k}[{i}]"))
    return results

urls = find_urls(room_data, "roomData")
print(f"\n=== roomData 中的 URL ({len(urls)}) ===")
for path, url in urls:
    print(f"  {path} = {url}")
