"""诊断脚本 v2：用多种方式查找 mitmproxy 证书"""
import winreg
import subprocess
import sys

print("=" * 60)
print("CA证书诊断工具 v2")
print("=" * 60)

# 方法1: 用 certutil 列出 Root 证书
print("\n--- 方法1: certutil 列出 -Root (受信任根证书) ---")
try:
    result = subprocess.run(
        ["certutil", "-store", "-user", "Root"],
        capture_output=True, text=True, timeout=30
    )
    output = result.stdout + result.stderr
    
    # 搜索 mitmproxy 相关行
    lines = output.split('\n')
    found_lines = [l for l in lines if 'mitm' in l.lower()]
    
    if found_lines:
        print(f"找到 {len(found_lines)} 行含 'mitm':")
        for line in found_lines:
            print(f"  {line.strip()}")
    else:
        print("certutil -store -user Root 中未找到 'mitm'")
        
    # 显示总证书数（通过计数 "Certificate: " 开头的行）
    cert_lines = [l for l in lines if l.strip().startswith('=====')]
    print(f"Root 存储中共约 {len(cert_lines)} 个证书条目")
        
except Exception as e:
    print(f"certutil 失败: {e}")

# 方法2: certutil -store Root (本地计算机)
print("\n--- 方法2: certutil 列出 -store Root (本地计算机) ---")
try:
    result = subprocess.run(
        ["certutil", "-store", "Root"],
        capture_output=True, text=True, timeout=30
    )
    output = result.stdout + result.stderr
    
    found_lines = [l for l in output.split('\n') if 'mitm' in l.lower()]
    if found_lines:
        for line in found_lines:
            print(f"  {line.strip()}")
    else:
        print("未找到 'mitm'")
except Exception as e:
    print(f"失败: {e}")

# 方法3: 用 PowerShell 直接查证书存储
print("\n--- 方法3: PowerShell Get-ChildItem cert:\LocalMachine\Root ---")
try:
    ps_cmd = '''
Get-ChildItem -Path "Cert:\\LocalMachine\\Root" | 
    Where-Object { $_.Subject -like "*mitm*" } |
    Format-List Subject, Issuer, Thumbprint, HasPrivateKey
'''
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True, text=True, timeout=30
    )
    if result.stdout.strip():
        print(result.stdout)
    else:
        print("本地计算机 Root 中未找到 mitm 证书")
except Exception as e:
    print(f"失败: {e}")

# 方法4: PowerShell 查 CurrentUser
print("\n--- 方法4: PowerShell Get-ChildItem cert:\\CurrentUser\\Root ---")
try:
    ps_cmd = '''
Get-ChildItem -Path "Cert:\\CurrentUser\\Root" | 
    Where-Object { $_.Subject -like "*mitm*" } |
    Format-List Subject, Issuer, Thumbprint, HasPrivateKey
'''
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True, text=True, timeout=30
    )
    if result.stdout.strip():
        print(result.stdout)
    else:
        print("当前用户 Root 中未找到 mitm 证书")
except Exception as e:
    print(f"失败: {e}")

# 方法5: 宽泛搜索所有包含 mitm 的证书
print("\n--- 方法5: 全局搜索所有证书存储中的 'mitm' ---")
try:
    ps_cmd = '''
$stores = @("Cert:\\LocalMachine\\Root","Cert:\\LocalMachine\\CA",
            "Cert:\\CurrentUser\\Root","Cert:\\CurrentUser\\CA",
            "Cert:\\CurrentUser\\My","Cert:\\LocalMachine\\My",
            "Cert:\\LocalMachine\\AuthRoot","Cert:\\CurrentUser\\AuthRoot")
foreach ($s in $stores) {
    try {
        $certs = Get-ChildItem $s -ErrorAction SilentlyContinue | 
                 Where-Object { $_.Subject -like "*mitm*" }
        foreach ($c in $certs) {
            Write-Host "STORE: $s"
            Write-Host "  Subject: $($c.Subject)"
            Write-Host "  Issuer:  $($c.Issuer)"
            Write-Host "  Thumb:   $($c.Thumbprint)"
        }
    } catch {}
}
'''
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True, text=True, timeout=60
    )
    if result.stdout.strip():
        print(result.stdout)
    else:
        print("所有证书存储中都未找到 mitm 证书！")
        print("\n结论：p12 证书可能没有正确导入到任何受信任的证书存储")
except Exception as e:
    print(f"失败: {e}")

# 方法6: 检查 p12 文件是否存在
print("\n--- 方法6: 检查 p12 文件是否还存在 ---")
import os
p12_paths = [
    os.path.expandvars(r"%USERPROFILE%\Downloads\mitmproxy-ca-cert.p12"),
    os.path.expandvars(r"%USERPROFILE%\Desktop\mitmproxy-ca-cert.p12"),
    os.path.expandvars(r"%USERPROFILE%\Documents\mitmproxy-ca-cert.p12"),
]
for p in p12_paths:
    if os.path.exists(p):
        size = os.path.getsize(p)
        print(f"  找到: {p} ({size} bytes)")
    # 也搜索 Downloads 下所有 .p12
downloads = os.path.expandvars(r"%USERPROFILE%\Downloads")
if os.path.isdir(downloads):
    for f in os.listdir(downloads):
        if f.endswith('.p12') or f.endswith('.cer') or f.endswith('.crt'):
            fp = os.path.join(downloads, f)
            size = os.path.getsize(fp)
            print(f"  Downloads/{f} ({size} bytes)")

print("\n" + "=" * 60)
