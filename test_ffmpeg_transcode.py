"""测试 ffmpeg HEVC → H.264 转码"""
import subprocess
import sys
import requests
import threading

url = "https://livecb.alicdn.com/mediaplatform/c814ef39-950c-4db8-8842-c4d9bc93d524.flv?auth_key=1778332207-0-0-b61cc06504ea29d4c86550dd34520311&source=34675810_null_TBLive_live&ali_flv_retain=2"

headers = {
    'Referer': 'https://live.taobao.com/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}

print(f"Fetching stream with HEVC codec...")
resp = requests.get(url, headers=headers, stream=True, timeout=15)
print(f"Status: {resp.status_code}")

# Read probe data
probe_data = b''
for chunk in resp.iter_content(chunk_size=8192):
    probe_data += chunk
    if len(probe_data) >= 50000:
        break
print(f"Probe data: {len(probe_data)} bytes")

# Start ffmpeg to transcode
cmd = [
    "ffmpeg",
    "-hide_banner", "-loglevel", "info",
    "-f", "flv",
    "-i", "pipe:0",
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-tune", "zerolatency",
    "-c:a", "copy",
    "-f", "flv",
    "output_h264.flv",
]

print(f"\nStarting ffmpeg...")
proc = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=0x08000000,  # CREATE_NO_WINDOW
)

# Write probe data
print(f"Writing probe data to ffmpeg stdin...")
proc.stdin.write(probe_data)

# Write remaining stream data for 10 seconds
def write_stream():
    count = 0
    for chunk in resp.iter_content(chunk_size=65536):
        if count > 20:  # ~20 chunks
            break
        try:
            proc.stdin.write(chunk)
            count += 1
        except (BrokenPipeError, OSError):
            break
    proc.stdin.close()

writer = threading.Thread(target=write_stream, daemon=True)
writer.start()

# Read stderr for status
print("FFmpeg output:")
import time
start = time.time()
while time.time() - start < 15:
    line = proc.stderr.readline()
    if line:
        print(f"  {line.decode('utf-8', errors='replace').strip()}")
    if proc.poll() is not None:
        print(f"  ffmpeg exited with code {proc.returncode}")
        break
    time.sleep(0.1)

proc.terminate()
resp.close()

# Check output file
import os
if os.path.exists("output_h264.flv"):
    size = os.path.getsize("output_h264.flv")
    print(f"\nOutput: output_h264.flv ({size:,} bytes)")
    if size > 1000:
        # Check if it's H.264 now
        with open("output_h264.flv", "rb") as f:
            data = f.read(min(size, 50000))
        
        # Parse tags
        offset = 9
        for i in range(20):
            if offset + 11 > len(data):
                break
            tt = data[offset]
            ds = (data[offset+1] << 16) | (data[offset+2] << 8) | data[offset+3]
            if ds > 5000000:
                break
            if tt == 9 and offset + 12 <= len(data):
                fb = data[offset+11]
                cid = fb & 0x0f
                ft = (fb >> 4) & 0x0f
                codecs = {7:'AVC/H.264', 12:'HEVC/H.265'}
                frames = {1:'KEY', 2:'INTER'}
                print(f"  Video tag: {codecs.get(cid, f'C{cid}')} | {frames.get(ft, f'F{ft}')} | {ds} bytes")
            elif tt == 8 and offset + 12 <= len(data):
                sf = (data[offset+11] >> 4) & 0x0f
                fmts = {10:'AAC', 2:'MP3'}
                print(f"  Audio tag: {fmts.get(sf, f'F{sf}')} | {ds} bytes")
            offset += 11 + ds + 4
    os.remove("output_h264.flv")
else:
    print("No output file created")
