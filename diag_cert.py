"""诊断脚本：查看 Windows 注册表中 mitmproxy 证书的实际情况"""
import winreg
import sys

print("=" * 60)
print("CA证书诊断工具")
print("=" * 60)

found_any = False

for hive_name, hive, path in [
    ("HKCU (当前用户)", winreg.HKEY_CURRENT_USER,
     r"SOFTWARE\Microsoft\SystemCertificates\Root\Certificates"),
    ("HKLM (本地计算机)", winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\Microsoft\SystemCertificates\Root\Certificates"),
]:
    print(f"\n--- {hive_name}: {path} ---")
    try:
        key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        i = 0
        cert_count = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(key, i)
                subkey = winreg.OpenKey(key, subkey_name)
                subject, _ = winreg.QueryValueEx(subkey, "Subject")
                issuer, _ = winreg.QueryValueEx(subkey, "Issuer")
                cert_count += 1
                
                # 检查是否包含 mitmproxy
                is_mitm = "mitmproxy" in subject.lower()
                marker = " <<< MITMPROXY!" if is_mitm else ""
                
                print(f"  [{cert_count}] Thumbprint: {subkey_name[:16]}...")
                print(f"      Subject: {subject}")
                print(f"      Issuer:  {issuer}{marker}")
                
                if is_mitm:
                    found_any = True
                
                winreg.CloseKey(subkey)
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
        print(f"  共 {cert_count} 个证书")
    except Exception as e:
        print(f"  无法打开: {e}")

print("\n" + "=" * 60)
if found_any:
    print("结果: 找到 mitmproxy 证书!")
else:
    print("结果: 未找到 mitmproxy 证书")
    print("\n可能原因:")
    print("1. p12 证书导入到了 '受信任的根证书颁发机构' 以外的存储位置")
    print("2. 证书 Subject 字段不含 'mitmproxy' 字符串")
    print("3. 导入时选择了 '将所有证书放入以下存储' 但选错了位置")
    print("\n建议:")
    print("- 打开 certlm.msc (本地计算机证书) 或 certmgr.msc (用户证书)")
    print("- 确认 mitmproxy 证书在 '受信任的根证书颁发机构' -> '证书' 文件夹中")
print("=" * 60)
