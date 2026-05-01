"""测试 ffmpeg 直接拉流 + HEVC→H.264 转码"""
import subprocess
import time
import os

url = "https://livecb.alicdn.com/mediaplatform/c814ef39-950c-4db8-8842-c4d9bc93d524.flv?auth_key=1778332207-0-0-b61cc06504ea29d4c86550dd34520311&source=34675810_null_TBLive_live&ali_flv_retain=2"

# ffmpeg 直接拉流，通过 -headers 注入 Referer
cmd = [
    "ffmpeg",
    "-hide_banner", "-loglevel", "info",
    "-headers", "Referer: https://live.taobao.com/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36\r\n",
    "-i", url,
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-tune", "zerolatency",
    "-c:a", "copy",
    "-t", "10",  # 只录 10 秒
    "-f", "flv",
    "output_h264_direct.flv",
]

print("Starting ffmpeg direct pull + transcode...")
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=0x08000000,  # CREATE_NO_WINDOW
)

start = time.time()
while time.time() - start < 20:
    line = proc.stderr.readline()
    if line:
        text = line.decode('utf-8', errors='replace').strip()
        print(f"  {text}")
        if "video:" in text.lower() or "audio:" in text.lower():
            print(f"\n  -> Stream info detected!")
    if proc.poll() is not None:
        print(f"\n  ffmpeg exited with code {proc.returncode}")
        break
    time.sleep(0.1)

# Check output
if os.path.exists("output_h264_direct.flv"):
    size = os.path.getsize("output_h264_direct.flv")
    print(f"\nOutput: output_h264_direct.flv ({size:,} bytes)")
    
    if size > 1000:
        with open("output_h264_direct.flv", "rb") as f:
            data = f.read(min(size, 100000))
        
        # Check codec
        offset = 9
        codec_found = None
        for i in range(30):
            if offset + 12 > len(data):
                break
            tt = data[offset]
            ds = (data[offset+1] << 16) | (data[offset+2] << 8) | data[offset+3]
            if ds > 5000000:
                break
            if tt == 9:
                fb = data[offset+11]
                cid = fb & 0x0f
                ft = (fb >> 4) & 0x0f
                codecs = {7:'AVC/H.264', 12:'HEVC/H.265'}
                frames = {1:'KEY', 2:'INTER'}
                if codec_found is None:
                    codec_found = codecs.get(cid, f'C{cid}')
                if i < 5:
                    print(f"  Video: {codecs.get(cid, f'C{cid}')} | {frames.get(ft, f'F{ft}')} | {ds} bytes")
            elif tt == 8 and i < 3:
                sf = (data[offset+11] >> 4) & 0x0f
                fmts = {10:'AAC', 2:'MP3'}
                print(f"  Audio: {fmts.get(sf, f'F{sf}')} | {ds} bytes")
            offset += 11 + ds + 4
        
        if codec_found == 'AVC/H.264':
            print("\n  SUCCESS: Output is H.264! OBS should display video correctly.")
        else:
            print(f"\n  Output codec: {codec_found}")
    
    os.remove("output_h264_direct.flv")
else:
    print("No output file - ffmpeg failed")
