"""直接在 offset 9 处检查 tag"""
import requests

url = "https://livecb.alicdn.com/mediaplatform/c814ef39-950c-4db8-8842-c4d9bc93d524.flv?auth_key=1778332207-0-0-b61cc06504ea29d4c86550dd34520311&source=34675810_null_TBLive_live&ali_flv_retain=2"

headers = {
    'Referer': 'https://live.taobao.com/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}

resp = requests.get(url, headers=headers, stream=True, timeout=15)
data = b''
for chunk in resp.iter_content(chunk_size=65536):
    data += chunk
    if len(data) > 100000:
        break

print(f"Total: {len(data)} bytes")
print()

# Detailed hex dump first 30 bytes
print("Byte-by-byte analysis:")
for i in range(min(30, len(data))):
    b = data[i]
    ch = chr(b) if 32 <= b < 127 else '.'
    role = ""
    if i == 0: role = " <- 'F'"
    elif i == 1: role = " <- 'L'"
    elif i == 2: role = " <- 'V'"
    elif i == 3: role = " <- FLV version"
    elif i == 4: role = " <- flags (audio+video)"
    elif i >= 5 and i <= 8: role = " <- PrevTagSize0"
    elif i == 9: role = " <- First tag type"
    elif i >= 10 and i <= 11: role = " <- DataSize[high,mid]"
    elif i == 12: role = " <- DataSize[low]"
    print(f"  [{i:3d}] 0x{b:02x} = {b:3d} '{ch}'{role}")

print()
# First tag analysis
print(f"Byte 9 (tag type): 0x{data[9]:02x} = {data[9]}")
tag_type = data[9]
if tag_type == 18:
    print("  -> Script Data tag")
elif tag_type == 9:
    print("  -> Video tag")
elif tag_type == 8:
    print("  -> Audio tag")
else:
    print(f"  -> Unknown tag type!")

data_size = (data[10] << 16) | (data[11] << 8) | data[12]
print(f"Byte 10-12 (data size): 0x{data[10]:02x}{data[11]:02x}{data[12]:02x} = {data_size} bytes")

ts = (data[13] << 16) | (data[14] << 8) | data[15]
ts_ext = data[16]
full_ts = ts | (ts_ext << 24)
print(f"Byte 13-16 (timestamp): {full_ts}ms")

print(f"\nTag data starts at offset 20, first 60 bytes:")
tag_data = data[20:80]
for i in range(0, len(tag_data), 16):
    hex_part = ' '.join(f'{b:02x}' for b in tag_data[i:i+16])
    ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in tag_data[i:i+16])
    print(f"  {20+i:04x}: {hex_part:<48s} {ascii_part}")
