"""诊断小红书直播流：检查流结构（是否有音频轨道）、ffmpeg 转码是否正常输出"""

import subprocess
import sys
import os

# 从日志中提取的小红书 FLV 流地址（HEVC 编码）
TEST_URL = "http://live-source-play.xhscdn.com/live/570226057662621357_hcv5401.flv"

HEADERS = (
    "Referer: https://www.xiaohongshu.com/\r\n"
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36\r\n"
)

def find_ffmpeg():
    """查找 ffmpeg"""
    # 嵌入式路径
    import glob
    candidates = []
    
    # 当前目录
    if os.path.exists("ffmpeg.exe"):
        candidates.append(os.path.abspath("ffmpeg.exe"))
    
    # 系统 PATH
    for p in os.environ.get("PATH", "").split(";"):
        fp = os.path.join(p.strip(), "ffmpeg.exe")
        if os.path.exists(fp):
            candidates.append(fp)
    
    # PyInstaller _MEIPASS
    if hasattr(sys, "_MEIPASS"):
        meipass_ff = os.path.join(sys._MEIPASS, "embedded_ffmpeg", "ffmpeg.exe")
        if os.path.exists(meipass_ff):
            candidates.append(meipass_ff)
    
    # AppData 已释放
    appdata_ff = os.path.join(os.environ.get("APPDATA", ""), "LiveStreamFetcher", "embedded_ffmpeg", "ffmpeg.exe")
    if os.path.exists(appdata_ff):
        candidates.append(appdata_ff)
    
    return candidates[0] if candidates else None

def test_1_probe_streams():
    """测试1: 用 ffprobe 检查流的所有轨道（视频+音频）"""
    print("=" * 60)
    print("测试1: ffprobe 检查流轨道信息")
    print("=" * 60)
    
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print("[FAIL] 找不到 ffmpeg")
        return None
    
    ffprobe_path = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
    if not os.path.exists(ffprobe_path):
        print(f"[FAIL] 找不到 ffprobe: {ffprobe_path}")
        return None
    
    # 探测所有流轨道
    cmd = [
        ffprobe_path,
        "-hide_banner",
        "-headers", HEADERS,
        "-i", TEST_URL,
        "-v", "quiet",
        "-show_entries", "stream=index,codec_type,codec_name,sample_rate,channels",
        "-of", "json",
    ]
    
    print(f"命令: {' '.join(cmd[:6])} ...")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=20,
                              creationflags=0x08000000 if sys.platform == 'win32' else 0)
        stdout = result.stdout.decode(errors="replace")
        stderr = result.stderr.decode(errors="replace")
        
        if stdout:
            print(f"\n流轨道信息:\n{stdout}")
            import json
            try:
                info = json.loads(stdout)
                streams = info.get("streams", [])
                print(f"\n共 {len(streams)} 个轨道:")
                for s in streams:
                    stype = s.get("codec_type", "?")
                    codec = s.get("codec_name", "?")
                    extra = ""
                    if stype == "audio":
                        sr = s.get("sample_rate", "?")
                        ch = s.get("channels", "?")
                        extra = f", 采样率={sr}, 声道={ch}"
                    print(f"  [{stype}] {codec}{extra}")
                
                has_video = any(s.get("codec_type") == "video" for s in streams)
                has_audio = any(s.get("codec_type") == "audio" for s in streams)
                print(f"\n结论: 有视频={has_video}, 有音频={has_audio}")
                return {"has_video": has_video, "has_audio": has_audio, "streams": streams}
            except json.JSONDecodeError:
                print(f"(JSON 解析失败，原始输出如上)")
        else:
            print(f"[WARN] 无 stdout 输出")
            if stderr:
                print(f"stderr: {stderr[:500]}")
    except subprocess.TimeoutExpired:
        print("[FAIL] ffprobe 超时 (20s)")
    except Exception as e:
        print(f"[FAIL] 异常: {e}")
    return None

def test_2_transcode_output():
    """测试2: ffmpeg 转码并输出到文件，验证输出是否有效"""
    print("\n" + "=" * 60)
    print("测试2: ffmpeg HEVC→H.264 转码输出 (5秒)")
    print("=" * 60)
    
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print("[FAIL] 找不到 ffmpeg")
        return
    
    output_file = os.path.join(os.path.dirname(__file__), "test_transcode_output.flv")
    
    cmd = [
        ffmpeg_path,
        "-hide_banner", "-loglevel", "info",
        "-headers", HEADERS,
        "-i", TEST_URL,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-c:a", "copy",
        "-t", "5",  # 只转5秒
        "-f", "flv",
        "-y",  # 覆盖输出
        output_file,
    ]
    
    print(f"命令: {ffmpeg_path} -i <url> -c:v libx264 -c:a copy -t 5 -f flv {output_file}")
    print("(运行5秒...)")
    
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
            creationflags=0x08000000 if sys.platform == 'win32' else 0,
        )
        
        stdout = proc.stdout.decode(errors="replace")
        stderr = proc.stderr.decode(errors="replace")
        
        if os.path.exists(output_file):
            size = os.path.getsize(output_file)
            print(f"\n输出文件: {output_file} ({size:,} bytes)")
            
            if size > 100:
                # 用 ffprobe 验证输出文件
                ffprobe_path = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
                verify_cmd = [
                    ffprobe_path, "-hide_banner",
                    "-i", output_file,
                    "-v", "quiet",
                    "-show_entries", "stream=index,codec_type,codec_name,duration",
                    "-of", "csv=p=0",
                ]
                vr = subprocess.run(verify_cmd, capture_output=True, timeout=10)
                verify_out = vr.stdout.decode(errors="replace").strip()
                print(f"输出文件轨道:\n{verify_out}")
                
                # 清理
                try:
                    os.remove(output_file)
                    print("\n(测试文件已清理)")
                except:
                    pass
                
                # 检查 ffmpeg stderr 中的关键信息
                if stderr:
                    lines = stderr.strip().split("\n")
                    # 找关键行
                    for line in lines:
                        ll = line.lower()
                        if "error" in ll or "invalid" in ll or "not found" in ll:
                            print(f"  [!] {line}")
                        elif "video:" in ll or "audio:" in ll or "stream mapping:" in ll:
                            print(f"  [>] {line}")
                    
                return True
            else:
                print(f"[FAIL] 输出文件太小 ({size} bytes)，可能转码失败")
                if stderr:
                    print(f"stderr 最后500字:\n{stderr[-500:]}")
        else:
            print(f"[FAIL] 没有生成输出文件")
            if stderr:
                print(f"stderr:\n{stderr[:1000]}")
            
    except subprocess.TimeoutExpired:
        print("[FAIL] ffmpeg 超时 (30s)")
    except Exception as e:
        print(f"[FAIL] 异常: {e}")
    
    return False

def test_3_direct_flv_check():
    """测试3: 直接拉取一小段 FLV 数据，解析 FLV tag 结构"""
    print("\n" + "=" * 60)
    print("测试3: 直接下载 FLV 数据，解析 tag 结构")
    print("=" * 60)
    
    try:
        import urllib.request
        req = urllib.request.Request(TEST_URL)
        req.add_header("Referer", "https://www.xiaohongshu.com/")
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read(100000)  # 读 100KB
            
        print(f"下载了 {len(data)} bytes 的 FLV 数据")
        
        # 解析 FLV header
        if len(data) < 13:
            print("[FAIL] 数据太短，不是有效的 FLV")
            return
        
        signature = data[0:3]
        version = data[3]
        flags = data[4]
        offset = int.from_bytes(data[5:9], 'big')
        
        print(f"FLV Header: signature={signature}, version={version}, flags=0x{flags:02X}, data_offset={offset}")
        print(f"  Audio flag: {(flags >> 2) & 1}, Video flag: {flags & 1}")
        
        # 解析 FLV tags
        pos = offset  # 跳过 header
        tag_count = 0
        video_tags = 0
        audio_tags = 0
        video_codecs = set()
        audio_codecs = set()
        
        while pos + 11 <= len(data) and tag_count < 50:
            tag_type = data[pos]
            data_size = (data[pos+1] << 16) | (data[pos+2] << 8) | data[pos+3]
            timestamp = (data[pos+4] << 16) | (data[pos+5] << 8) | data[pos+6] | (data[pos+7] << 24)
            # stream_id = data[pos+8:11]
            
            if data_size > 1000000 or data_size == 0:
                break
            
            tag_body_start = pos + 11
            if tag_body_start + data_size > len(data):
                print(f"  [tag #{tag_count}] type={tag_type}, size={data_size} (截断)")
                break
            
            if tag_type == 8:  # Audio
                audio_tags += 1
                sound_fmt = (data[tag_body_start] >> 4) & 0x0F
                fmt_names = {0:'LinearPCM', 1:'ADPCM', 2:'MP3', 10:'AAC', 11:'Speex', 14:'MP3 8kHz', 15:'Raw'}
                audio_codecs.add(fmt_names.get(sound_fmt, f'Fmt{sound_fmt}'))
                if audio_tags <= 3:
                    print(f"  [tag #{tag_count}] AUDIO | format={fmt_names.get(sound_fmt, f'{sound_fmt}')} | size={data_size} | ts={timestamp}ms")
                    
            elif tag_type == 9:  # Video
                video_tags += 1
                frame_type = (data[tag_body_start] >> 4) & 0x0F
                codec_id = data[tag_body_start] & 0x0F
                ft_names = {1:'KEYFRAME', 2:'INTER', 3:'DISPOSABLE', 5:'VIDEOINFO'}
                codec_names = {1:'JPEG', 2:'Sorenson', 3:'Screen', 4:'VP6', 5:'VP6A', 7:'AVC/H.264', 12:'HEVC/H.265', 13:'AV1'}
                video_codecs.add(codec_names.get(codec_id, f'C{codec_id}'))
                if video_tags <= 5:
                    print(f"  [tag #{tag_count}] VIDEO | codec={codec_names.get(codec_id, f'{codec_id}')} | frame={ft_names.get(frame_type, f'{frame_type}')} | size={data_size} | ts={timestamp}ms")
            elif tag_type == 18:  # Script
                if tag_count == 0 or True:
                    print(f"  [tag #{tag_count}] SCRIPT (metadata) | size={data_size}")
            
            tag_count += 1
            pos += 11 + data_size + 4  # PreviousTagSize
        
        print(f"\n统计: {tag_count} tags, 视频={video_tags} ({video_codecs}), 音频={audio_tags} ({audio_codecs})")
        
        if audio_tags == 0:
            print("*** 重要发现: 此 FLV 流没有音频轨道！这就是 OBS 没声音的原因 ***")
        if video_tags > 0:
            print(f"视频编码: {video_codecs}")
        
        return {"video_tags": video_tags, "audio_tags": audio_tags, 
                "video_codecs": video_codecs, "audio_codecs": audio_codecs}
        
    except Exception as e:
        print(f"[FAIL] 下载/解析异常: {e}")
        return None


if __name__ == "__main__":
    print("小红书直播流诊断工具")
    print(f"目标 URL: {TEST_URL}\n")
    
    r1 = test_1_probe_streams()
    r3 = test_3_direct_flv_check()
    r2 = test_2_transcode_output()
    
    print("\n" + "=" * 60)
    print("诊断总结")
    print("=" * 60)
    if r3:
        if r3["audio_tags"] == 0:
            print("""
*** 问题确认: 小红书 FLV 流不包含音频轨道 ***
- 视频和音频是分开传输的
- 当前 ffmpeg 命令用 -c:a copy 无法复制不存在的音频
- OBS 自然也就没有声音

解决方案选项:
1. 查找小红书 API 返回的独立音频流 URL
2. 如果 pullConfig 中无音频流，则此为平台限制
""")
