"""从 Script tag 的 onMetadata 提取 width/height 等参数"""
import struct

with open("test_fragment.flv", "rb") as f:
    data = f.read()

# We know first tag at offset 13, type=Script, size=523
offset = 13
tag_type = data[offset]
data_size = (data[offset+1] << 16) | (data[offset+2] << 8) | data[offset+3]

print(f"First tag: type={tag_type}, size={data_size}")

if tag_type == 18:
    tag_data = data[offset+11:offset+11+data_size]
    
    # AMF0 parsing (simplified)
    # First byte should be 0x02 (string "onMetaData")
    marker = tag_data[0]
    print(f"AMF marker: 0x{marker:02x} ({'String' if marker == 2 else 'Other'})")
    
    if marker == 2:
        str_len = (tag_data[1] << 8) | tag_data[2]
        name = tag_data[3:3+str_len].decode('utf-8', errors='replace')
        print(f"Name: {name} (len={str_len})")
        
        # Next is ECMA Array (0x08)
        pos = 3 + str_len
        arr_marker = tag_data[pos]
        print(f"Array marker: 0x{arr_marker:02x} ({'ECMA Array' if arr_marker == 8 else 'Other'})")
        
        if arr_marker == 8:
            # 4 bytes for approximate count (can be ignored)
            pos += 5  # skip marker + 4 bytes count
            
            # Read key-value pairs until 0x00 0x00 0x09 (end marker)
            for _ in range(50):
                if pos + 3 > len(tag_data):
                    break
                
                key_len = (tag_data[pos] << 8) | tag_data[pos+1]
                if key_len == 0 and tag_data[pos+2] == 0x09:
                    print("  [end of object]")
                    break
                
                key = tag_data[pos+2:pos+2+key_len].decode('utf-8', errors='replace')
                pos += 2 + key_len
                
                val_marker = tag_data[pos]
                pos += 1
                
                if val_marker == 0x00:  # Number (double, 8 bytes)
                    val = struct.unpack('>d', tag_data[pos:pos+8])[0]
                    pos += 8
                    if val == int(val):
                        val = int(val)
                    print(f"  {key}: {val}")
                elif val_marker == 0x01:  # Boolean
                    val = tag_data[pos] != 0
                    pos += 1
                    print(f"  {key}: {val}")
                elif val_marker == 0x02:  # String
                    slen = (tag_data[pos] << 8) | tag_data[pos+1]
                    val = tag_data[pos+2:pos+2+slen].decode('utf-8', errors='replace')
                    pos += 2 + slen
                    print(f"  {key}: \"{val}\"")
                elif val_marker == 0x05:  # Null
                    print(f"  {key}: null")
                elif val_marker == 0x06:  # Undefined
                    print(f"  {key}: undefined")
                elif val_marker == 0x0B:  # Date
                    pos += 9
                    print(f"  {key}: [date]")
                else:
                    print(f"  {key}: marker=0x{val_marker:02x} (unknown)")
                    break

# Also check video tag 3 (the big one with codec 12)
print("\n\nVideo Tag 3 (the big keyframe) first bytes:")
offset = 13  # first tag
# Skip tag 0 (Script), tag 1 (Audio), tag 2 (Video small)
for i in range(3):
    tt = data[offset]
    ds = (data[offset+1] << 16) | (data[offset+2] << 8) | data[offset+3]
    offset += 11 + ds + 4

# Now at tag 3
tt = data[offset]
ds = (data[offset+1] << 16) | (data[offset+2] << 8) | data[offset+3]
print(f"Tag 3: type={tt}, size={ds}")

tag3_data = data[offset+11:offset+11+min(ds, 40)]
print(f"First 40 bytes: {' '.join(f'{b:02x}' for b in tag3_data)}")

# For HEVC in FLV: first byte is (frametype << 4) | codec_id
# 0x1C = KEYFRAME(1) << 4 | 12(HEVC)
fb = tag3_data[0]
frame_type = (fb >> 4) & 0x0f
codec_id = fb & 0x0f
print(f"Frame byte: 0x{fb:02x} -> frame_type={frame_type}, codec_id={codec_id}")
print(f"Codec 12 = HEVC/H.265")

# HEVC packet type: tag3_data[1]
hevc_type = tag3_data[1]
hevc_type_names = {0: 'Sequence Header', 1: 'NALU', 2: 'End of Sequence'}
print(f"HEVC packet type: {hevc_type_names.get(hevc_type, hevc_type)}")

if hevc_type == 0 and ds > 10:
    # HEVCDecoderConfigurationRecord
    print(f"HEVC Config data (first 20 bytes): {' '.join(f'{b:02x}' for b in tag3_data[2:22])}")
    
    # VPS/SPS/PPS info
    # Basic HEVC config: configurationVersion, general_profile_space, etc.
    if ds > 5:
        config_ver = tag3_data[2]
        print(f"Configuration version: {config_ver}")
