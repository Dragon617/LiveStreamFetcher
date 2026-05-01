import os, sqlite3

# 模拟 EXE 中的 _check_xhs_login_status 完整逻辑
base = os.environ.get("APPDATA", os.path.expanduser("~"))
data_dir = os.path.join(base, "LiveStreamFetcher", "xiaohongshu_browser_data")
cookies_path = os.path.join(data_dir, "Default", "Cookies")

print(f"data_dir exists: {os.path.exists(data_dir)}")
print(f"cookies_path: {cookies_path}")
print(f"cookies_path exists: {os.path.exists(cookies_path)}")

# 第一步：检查 Default/Cookies
if os.path.exists(cookies_path):
    try:
        conn = sqlite3.connect(cookies_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%xiaohongshu%'"
        )
        count = cursor.fetchone()[0]
        conn.close()
        print(f"Default/Cookies count: {count}")
    except Exception as e:
        print(f"Default/Cookies error: {e}")
else:
    print("Default/Cookies NOT FOUND")

# 第二步：检查 Network/Cookies
network_cookies = os.path.join(data_dir, "Default", "Network", "Cookies")
print(f"network_cookies: {network_cookies}")
print(f"network_cookies exists: {os.path.exists(network_cookies)}")

if os.path.exists(network_cookies):
    try:
        conn = sqlite3.connect(network_cookies)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%xiaohongshu%'"
        )
        count = cursor.fetchone()[0]
        conn.close()
        print(f"Network/Cookies count: {count}")
    except Exception as e:
        print(f"Network/Cookies error: {e}")

# 结论
print("\n--- 结论 ---")
if not os.path.exists(data_dir):
    print("返回 'never'")
elif (os.path.exists(cookies_path) and "count" in dir()) or (os.path.exists(network_cookies) and "count" in dir()):
    print("返回 'logged_in'")
else:
    print("返回 'expired'")
