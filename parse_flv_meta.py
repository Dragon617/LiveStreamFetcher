"""解析 FLV onMetadata 获取分辨率等参数"""
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
    if len(data) > 300000:
        break

# Parse FLV tags properly
# FLV header: 9 bytes (3+1+1+4)
offset = 9

print("FLV Tag Analysis:")
print("=" * 70)

video_codec = None
audio_codec = None
video_width = None
video_height = None
framerate = None
video_keyframe_seen = False
audio_aac_seen = False

for i in range(200):
    if offset + 11 > len(data):
        break

    tag_type = data[offset]
    data_size = (data[offset+1] << 16) | (data[offset+2] << 8) | data[offset+3]
    ts_low = (data[offset+4] << 16) | (data[offset+5] << 8) | data[offset+6]
    ts_ext = data[offset+7]
    full_ts = ts_low | (ts_ext << 24)

    if data_size > 5000000:
        break

    tag_data = data[offset+11:offset+11+data_size]
    
    if tag_type == 18:  # Script
        # Parse onMetaData
        try:
            text = tag_data.decode('utf-8', errors='replace')
            # Extract known fields
            for field in ['width', 'height', 'framerate', 'fps', 'videocodecid', 'audiocodecid', 'duration', 'videodatarate', 'audiodatarate']:
                idx = text.find(field)
                if idx >= 0:
                    # Show context
                    context = text[idx:idx+50]
                    print(f"  Metadata: {context}")
        except:
            pass

    elif tag_type == 9:  # Video
        if len(tag_data) >= 2:
            fb = tag_data[0]
            frame_type = (fb >> 4) & 0x0f
            codec_id = fb & 0x0f
            
            codecs = {1:'JPEG', 2:'Sorenson H.263', 3:'Screen', 4:'VP6', 5:'VP6a', 6:'SV2', 7:'AVC/H.264'}
            frames = {1:'KEYFRAME', 2:'INTER', 5:'INFO'}
            
            video_codec = codecs.get(codec_id, f'Unknown({codec_id})')
            
            extra = ""
            if codec_id == 7 and len(tag_data) >= 2:
                avc_type = tag_data[1]
                if avc_type == 0:
                    extra = " AVC SeqHeader"
                    if len(tag_data) >= 4:
                        # AVCDecoderConfigurationRecord
                        profile = tag_data[2]
                        level = tag_data[3]
                        profile_names = {66:'Baseline', 77:'Main', 100:'High', 118:'High4:2:2'}
                        extra += f" profile={profile_names.get(profile, profile)} level={level}"
                elif avc_type == 1:
                    extra = " AVC NALU"
                    # Check NALU type
                    if len(tag_data) >= 5:
                        nalu_type = tag_data[4] & 0x1f
                        nalu_names = {1:'NonIDR', 5:'IDR/Keyframe', 6:'SEI', 7:'SPS', 8:'PPS'}
                        extra += f" type={nalu_names.get(nalu_type, nalu_type)}"
                        if nalu_type == 5:
                            video_keyframe_seen = True
                elif avc_type == 2:
                    extra = " AVC EndSeq"
            
            frame_name = frames.get(frame_type, f'Frame({frame_type})')
            
            if i < 15 or frame_type == 1:
                print(f"  Tag {i}: VIDEO | {video_codec} | {frame_name}{extra} | size={data_size} ts={full_ts}ms")
    
    elif tag_type == 8:  # Audio
        if len(tag_data) >= 1:
            fb = tag_data[0]
            sound_fmt = (fb >> 4) & 0x0f
            fmt_names = {0:'PCM', 1:'ADPCM', 2:'MP3', 4:'Nelly16', 10:'AAC', 11:'Speex', 14:'MP3_8k'}
            audio_codec = fmt_names.get(sound_fmt, f'Unknown({sound_fmt})')
            
            extra = ""
            if sound_fmt == 10 and len(tag_data) >= 2:
                aac_type = tag_data[1]
                if aac_type == 0:
                    extra = " AAC SeqHeader"
                    if len(tag_data) >= 5:
                        # AudioSpecificConfig
                        obj_type = (tag_data[2] >> 3) & 0x1f
                        freq_idx = ((tag_data[2] & 0x07) << 1) | ((tag_data[3] >> 7) & 0x01)
                        chan_cfg = (tag_data[3] >> 3) & 0x0f
                        freq_names = {0:96000,1:88200,2:64000,3:48000,4:44100,5:32000,6:24000,7:22050,8:16000,9:12000,10:11025,11:8000}
                        chan_names = {1:'Mono',2:'Stereo'}
                        freq = freq_names.get(freq_idx, freq_idx)
                        chan = chan_names.get(chan_cfg, f'{chan_cfg}ch')
                        extra += f" freq={freq}Hz chan={chan}"
                        audio_aac_seen = True
                elif aac_type == 1:
                    extra = " AAC Raw"
            
            if i < 15:
                print(f"  Tag {i}: AUDIO | {audio_codec}{extra} | size={data_size} ts={full_ts}ms")

    offset += 11 + data_size + 4

print(f"\n{'='*70}")
print(f"Stream Info:")
print(f"  Video Codec: {video_codec}")
print(f"  Audio Codec: {audio_codec}")
print(f"  AVC Keyframe found: {video_keyframe_seen}")
print(f"  AAC Config found: {audio_aac_seen}")

# Check total bytes of video vs audio
total_video = 0
total_audio = 0
offset = 9
for i in range(200):
    if offset + 11 > len(data):
        break
    tt = data[offset]
    ds = (data[offset+1] << 16) | (data[offset+2] << 8) | data[offset+3]
    if ds > 5000000:
        break
    if tt == 9:
        total_video += ds
    elif tt == 8:
        total_audio += ds
    offset += 11 + ds + 4

print(f"  Total video data: {total_video:,} bytes")
print(f"  Total audio data: {total_audio:,} bytes")
print(f"  Video/Audio ratio: {total_video/max(total_audio,1):.2f}")
