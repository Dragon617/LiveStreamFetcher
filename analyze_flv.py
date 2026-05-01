"""分析淘宝直播 FLV 流的编码信息"""
import requests
import struct
import sys

url = sys.argv[1] if len(sys.argv) > 1 else input("FLV URL: ").strip()

headers = {
    'Referer': 'https://live.taobao.com/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}

print(f"Fetching: {url[:80]}...")
resp = requests.get(url, headers=headers, stream=True, timeout=15)
print(f"Status: {resp.status_code}")

data = b''
for chunk in resp.iter_content(chunk_size=65536):
    data += chunk
    if len(data) > 300000:
        break

print(f"Downloaded {len(data)} bytes")
print(f"Signature: {data[:3]}")
print(f"Version: {data[3]}")
print(f"Flags: {data[4]:08b} -> video={bool(data[4]&1)}, audio={bool(data[4]&4)}")

# Standard FLV: 9 bytes header (3 sig + 1 ver + 1 flags + 4 prev tag size)
prev_tag_size = struct.unpack('>I', data[5:9])[0]
print(f"PrevTagSize0: {prev_tag_size}")

offset = 9
video_tags = []
audio_tags = []
script_tags = []

for i in range(50):
    if offset + 11 > len(data):
        print(f"\nEnd of buffer at offset {offset}")
        break

    tag_type = data[offset]
    data_size = (data[offset+1] << 16) | (data[offset+2] << 8) | data[offset+3]
    ts_low = (data[offset+4] << 16) | (data[offset+5] << 8) | data[offset+6]
    ts_ext = data[offset+7]
    full_ts = ts_low | (ts_ext << 24)

    if data_size > 5000000:
        print(f"\nTag {i}: type={tag_type} size={data_size} TOO LARGE - parsing error, stopping")
        break

    type_name = {8: 'Audio', 9: 'Video', 18: 'Script'}.get(tag_type, f'Type({tag_type})')
    info = f"Tag {i}: {type_name:<7} size={data_size:>8} ts={full_ts:>10}ms"

    if tag_type == 9 and offset + 12 <= len(data):
        fb = data[offset+11]
        frame_type = (fb >> 4) & 0x0f
        codec_id = fb & 0x0f
        codecs = {1:'JPEG', 2:'Sorenson', 7:'AVC/H.264', 4:'VP6', 6:'SV2'}
        frames = {1:'KEYFRAME', 2:'INTER'}
        info += f" | {codecs.get(codec_id, f'C{codec_id}')} | {frames.get(frame_type, f'F{frame_type}')}"
        if codec_id == 7 and offset + 13 <= len(data):
            avc_pkt = data[offset+12]
            pkt_names = {0:'SeqHeader', 1:'NALU', 2:'EndSeq'}
            info += f" | {pkt_names.get(avc_pkt, f'AVC{avc_pkt}')}"
        video_tags.append((full_ts, frame_type, codec_id, data_size))

    elif tag_type == 8 and offset + 12 <= len(data):
        fb = data[offset+11]
        sound_fmt = (fb >> 4) & 0x0f
        formats = {0:'PCM', 1:'ADPCM', 2:'MP3', 7:'ALAW', 8:'ULAW', 10:'AAC', 14:'MP3_8k'}
        info += f" | {formats.get(sound_fmt, f'F{sound_fmt}')}"
        audio_tags.append((full_ts, sound_fmt, data_size))

    elif tag_type == 18:
        script_data = data[offset+11:offset+11+min(data_size, 200)]
        # Try to decode as AMF0 to find width/height
        info += f" | script"
        if b'width' in script_data:
            info += " (has width)"
        if b'height' in script_data:
            info += " (has height)"
        script_tags.append(data_size)

    print(info)
    offset += 11 + data_size + 4

print(f"\n{'='*60}")
print(f"Summary:")
print(f"  Video tags: {len(video_tags)}")
print(f"  Audio tags: {len(audio_tags)}")
print(f"  Script tags: {len(script_tags)}")

if video_tags:
    codecs_found = set()
    keyframes = 0
    for ts, ft, cid, sz in video_tags:
        codecs_found.add(cid)
        if ft == 1:
            keyframes += 1
    codec_names = {1:'JPEG', 2:'Sorenson H.263', 7:'AVC/H.264', 4:'VP6', 6:'ScreenVideo V2'}
    print(f"  Video codec: {', '.join(codec_names.get(c, f'Unknown({c})') for c in codecs_found)}")
    print(f"  Keyframes: {keyframes}/{len(video_tags)}")

if audio_tags:
    fmts_found = set()
    for ts, fmt, sz in audio_tags:
        fmts_found.add(fmt)
    fmt_names = {0:'PCM', 1:'ADPCM', 2:'MP3', 10:'AAC', 14:'MP3_8kHz'}
    print(f"  Audio codec: {', '.join(fmt_names.get(f, f'Unknown({f})') for f in fmts_found)}")
