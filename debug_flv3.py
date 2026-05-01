"""重新解析：FLV header 5 bytes + PrevTagSize0(4 bytes) = 9 bytes"""
import requests
import struct

url = "https://livecb.alicdn.com/mediaplatform/c814ef39-950c-4db8-8842-c4d9bc93d524.flv?auth_key=1778332207-0-0-b61cc06504ea29d4c86550dd34520311&source=34675810_null_TBLive_live&ali_flv_retain=2"

headers = {
    'Referer': 'https://live.taobao.com/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}

resp = requests.get(url, headers=headers, stream=True, timeout=15)
data = b''
for chunk in resp.iter_content(chunk_size=65536):
    data += chunk
    if len(data) > 500000:
        break

# The standard FLV format:
# Bytes 0-2: "FLV" (3 bytes)
# Byte 3: version (1 byte)
# Byte 4: flags (1 byte)
# Bytes 5-8: DataOffset / PrevTagSize0 (4 bytes, big-endian)
# Byte 9+: first tag

# But PrevTagSize0 = 9 means "the previous tag ended at offset 9"
# which is the size of the FLV header itself. This is normal!

# So first tag IS at offset 9
# But byte 9 = 0x00... Let me look at what's between offset 9 and 13

print(f"Bytes 5-12 hex: {' '.join(f'{b:02x}' for b in data[5:13])}")
print(f"Bytes 5-8 as uint32 BE: {struct.unpack('>I', data[5:9])[0]}")
print(f"Byte 9: 0x{data[9]:02x}")

# Wait - the hex dump showed:
# 0000: 46 4c 56 01 05 00 00 00 09 00 00 00 00 12 00 02
# The PrevTagSize0 at bytes 5-8 = 00 00 00 09 = 9
# Byte 9 = 0x00, byte 10 = 0x00, byte 11 = 0x00, byte 12 = 0x00
# Byte 13 = 0x12 = Script tag type

# This means there are 4 more zero bytes between the PrevTagSize0 and the first tag
# That's NOT standard FLV. Standard FLV has 9 bytes header then tags.

# Unless... the DataOffset field (bytes 5-8) is 9, meaning data starts at offset 9
# And then PrevTagSize0 would be at bytes 9-12? No...

# Let me try: what if bytes 5-8 is NOT PrevTagSize0 but DataOffset?
# If DataOffset = 9, then first tag starts at offset 9
# But we still have PrevTagSize0... 

# Actually in some FLV implementations:
# The field at bytes 5-8 can be EITHER "DataOffset" or "PreviousTagSize0"
# If it's DataOffset = 9, data starts at offset 9, and the first PrevTagSize0
# comes AFTER the first tag.

# Let me try starting at offset 9 and see if 0x00 is just padding
# or if the tag really starts at 13

# Try: tag starts at offset 13
print("\n--- Attempt: tag starts at offset 13 ---")
offset = 13

for i in range(20):
    if offset + 11 > len(data):
        break
    tag_type = data[offset]
    data_size = (data[offset+1] << 16) | (data[offset+2] << 8) | data[offset+3]
    ts_low = (data[offset+4] << 16) | (data[offset+5] << 8) | data[offset+6]
    ts_ext = data[offset+7]
    full_ts = ts_low | (ts_ext << 24)
    
    if data_size > 10000000:
        print(f"  Tag {i}: type={tag_type} size={data_size} TOO LARGE")
        break
    
    type_name = {8:'Audio', 9:'Video', 18:'Script'}.get(tag_type, f'Type({tag_type})')
    
    info = f"  Tag {i}: {type_name:<7} size={data_size:>8} ts={full_ts:>10}ms"
    
    if tag_type == 9 and offset + 12 <= len(data):
        fb = data[offset+11]
        ft = (fb >> 4) & 0x0f
        cid = fb & 0x0f
        codecs = {7:'AVC/H.264', 2:'Sorenson'}
        frames = {1:'KEY', 2:'INTER'}
        info += f" | {codecs.get(cid, f'C{cid}')} | {frames.get(ft, f'F{ft}')}"
    elif tag_type == 8 and offset + 12 <= len(data):
        sf = (data[offset+11] >> 4) & 0x0f
        fmts = {10:'AAC', 2:'MP3', 0:'PCM'}
        info += f" | {fmts.get(sf, f'F{sf}')}"
    
    print(info)
    
    # Move: tag header(11) + data + prev_tag_size(4)
    offset += 11 + data_size + 4

# Now let's check the standard interpretation: header is 9 bytes (3+1+1+4)
# where the last 4 bytes are PrevTagSize0 = 9 (size of FLV header including itself)
# Then first tag at offset 9
# But byte 9 = 0... 

# Could it be that the FLV has DataOffset field that equals 13?
# Some FLV files have a larger header
print("\n--- Checking if DataOffset suggests offset 13 ---")
data_offset = struct.unpack('>I', data[5:9])[0]
print(f"DataOffset/PrevTagSize0 = {data_offset}")

# Try: FLV header 13 bytes (5 byte basic + 4 DataOffset + 4 PrevTagSize0)
# This is non-standard but some encoders do it
# Bytes 5-8: DataOffset = 9 (where the tags start... but we see zeros)
# OR: Bytes 5-8 = PrevTagSize0 = 9, then bytes 9-12 = another PrevTagSize0?

# Let me look at this differently: 
# If DataOffset is in bytes 5-8 and equals 9, tags start at 9
# If PrevTagSize0 is at bytes 5-8 and equals 9, next is at 9

# The 4 zero bytes at 9-12 might be an empty "tag" with type 0, size 0
# Or they might be extra padding

# Let's try: offset = 9 + 4 zeros = 13 for the real first tag
# This is what we did above and it works!

# Now let's also try with yt-dlp/ffmpeg to see how they handle it
print("\n--- Let's check what ffmpeg says ---")

# Save first 100KB for ffmpeg analysis
with open("test_fragment.flv", "wb") as f:
    f.write(data[:200000])
print("Saved test_fragment.flv (200KB)")
