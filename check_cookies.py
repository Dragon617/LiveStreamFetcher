import sqlite3

cookies_path = r'C:\Users\15346\AppData\Roaming\LiveStreamFetcher\xiaohongshu_browser_data\Default\Network\Cookies'
conn = sqlite3.connect(cookies_path)
c = conn.cursor()

# 测试代码中的查询
c.execute("SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%xiaohongshu%'")
count = c.fetchone()[0]
print(f"LIKE query count: {count}")

# 列出所有 host_key
c.execute("SELECT DISTINCT host_key FROM cookies")
for row in c.fetchall():
    print(f"  host_key: {row[0]}")

conn.close()
