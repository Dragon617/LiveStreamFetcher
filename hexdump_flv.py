"""十六进制 dump 分析 FLV 原始字节"""
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
    if len(data) > 50000:
        break

# Hex dump first 200 bytes
print(f"Total downloaded: {len(data)} bytes")
print(f"\nHex dump (first 200 bytes):")
for i in range(0, min(200, len(data)), 16):
    hex_part = ' '.join(f'{b:02x}' for b in data[i:i+16])
    ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i:i+16])
    print(f"  {i:04x}: {hex_part:<48s} {ascii_part}")

# Check for known patterns
print(f"\nPattern analysis:")
print(f"  Starts with FLV: {data[:3] == b'FLV'}")
print(f"  Byte 0-2: {data[0]:02x} {data[1]:02x} {data[2]:02x} = '{chr(data[0])}{chr(data[1])}{chr(data[2])}'")
print(f"  Byte 3 (version): {data[3]}")
print(f"  Byte 4 (flags): {data[4]:08b}")

# Check if there's HTTP chunked transfer encoding artifact
if data[:5] != b'FLV\x01\x05':
    print("  WARNING: Not standard FLV header!")
    # Look for FLV signature elsewhere
    idx = data.find(b'FLV')
    if idx > 0:
        print(f"  Found 'FLV' at offset {idx}")

# Look for AVC/H.264 signatures
avc_idx = data.find(b'\x17\x01')  # AVC keyframe + sequence header
if avc_idx >= 0:
    print(f"  Found AVC keyframe seq header at offset {avc_idx}")

aac_idx = data.find(b'\xaf\x00')  # AAC + sequence header
if aac_idx >= 0:
    print(f"  Found AAC sequence header at offset {aac_idx}")

# Look for FLV tag type bytes (0x08=audio, 0x09=video, 0x12=script)
for tag_byte, name in [(0x09, 'Video'), (0x08, 'Audio'), (0x12, 'Script')]:
    positions = []
    pos = 0
    while True:
        pos = data.find(bytes([tag_byte]), pos)
        if pos < 0:
            break
        positions.append(pos)
        pos += 1
        if len(positions) > 5:
            break
    if positions:
        print(f"  TagType({tag_byte})='{name}' found at offsets: {positions}")
