"""快速验证新检测方法是否有效"""
import subprocess

ps_cmd = (
    '$stores = @("Cert:\\LocalMachine\\Root", "Cert:\\CurrentUser\\Root"); '
    'foreach ($s in $stores) { '
    'try { '
    '$certs = Get-ChildItem $s -ErrorAction SilentlyContinue | '
    'Where-Object { $_.Subject -like "*mitm*" }; '
    'foreach ($c in $certs) { Write-Host ("FOUND:" + $c.Subject + "|" + $c.Thumbprint) } '
    '} catch {} }'
)

result = subprocess.run(
    ["powershell", "-NoProfile", "-Command", ps_cmd],
    capture_output=True, text=True, timeout=15,
)
output = result.stdout.strip()
if output:
    print(f"检测成功: {output}")
else:
    print("未找到")
    print(f"stderr: {result.stderr}")
