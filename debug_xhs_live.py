#!/usr/bin/env python3
"""
调试小红书直播间解析 - 带详细日志
"""
import json
import re
import sys
import os

# 添加 embedded_chromium 路径
app_data = os.environ.get('APPDATA', os.path.expanduser('~'))
chromium_path = os.path.join(app_data, 'LiveStreamFetcher', 'embedded_chromium', 'chromium-1208', 'chrome-win64')
if os.path.exists(chromium_path):
    os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH'] = os.path.join(chromium_path, 'chrome.exe')
    print(f"[调试] 使用嵌入式 Chromium: {chromium_path}")

from playwright.sync_api import sync_playwright

def debug_xhs_live(url: str):
    print(f"\n{'='*60}")
    print(f"调试小红书直播间: {url}")
    print(f"{'='*60}\n")
    
    with sync_playwright() as p:
        # 启动浏览器
        browser_path = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH')
        if browser_path and os.path.exists(browser_path):
            print(f"[1] 使用浏览器: {browser_path}")
            browser = p.chromium.launch(
                executable_path=browser_path,
                headless=False,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            )
        else:
            print(f"[1] 使用系统默认 Chromium")
            browser = p.chromium.launch(headless=False)
        
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        )
        
        page = context.new_page()
        
        # 存储所有响应
        all_responses = {}
        
        def on_response(response):
            resp_url = response.url
            # 监听所有 edith API 和含 live/stream 的请求
            if 'edith.xiaohongshu.com' in resp_url or any(kw in resp_url.lower() for kw in ['live', 'stream']):
                try:
                    ct = response.body()
                    if ct and len(ct) < 2000000:
                        data = json.loads(ct.decode('utf-8', errors='replace'))
                        all_responses[resp_url] = data
                        print(f"\n[捕获响应] {resp_url[:100]}...")
                        print(f"  数据大小: {len(ct)} bytes")
                        if isinstance(data, dict):
                            print(f"  顶层键: {list(data.keys())[:10]}")
                except Exception as e:
                    print(f"\n[捕获响应-解析失败] {resp_url[:80]}... - {e}")
        
        page.on('response', on_response)
        
        # 导航到直播间
        print(f"\n[2] 正在打开直播间页面...")
        try:
            page.goto(url, wait_until='networkidle', timeout=60000)
            print(f"[2] 页面加载完成")
        except Exception as e:
            print(f"[2] 页面加载异常: {e}")
        
        # 等待一段时间让 JS 加载
        print(f"\n[3] 等待 10 秒让前端 JS 加载...")
        page.wait_for_timeout(10000)
        
        # 检查 __INITIAL_STATE__
        print(f"\n[4] 检查 __INITIAL_STATE__...")
        try:
            state = page.evaluate('() => window.__INITIAL_STATE__ || null')
            if state:
                print(f"  __INITIAL_STATE__ 存在")
                if isinstance(state, dict):
                    print(f"  顶层键: {list(state.keys())}")
                    if 'liveStream' in state:
                        ls = state['liveStream']
                        print(f"  liveStream 键: {list(ls.keys()) if isinstance(ls, dict) else '非字典'}")
                        if isinstance(ls, dict) and 'roomData' in ls:
                            rd = ls['roomData']
                            print(f"  roomData 键: {list(rd.keys()) if isinstance(rd, dict) else '非字典'}")
                            if isinstance(rd, dict) and 'roomInfo' in rd:
                                ri = rd['roomInfo']
                                print(f"  roomInfo: roomId={ri.get('roomId')}, pullConfig={ri.get('pullConfig')}")
            else:
                print(f"  __INITIAL_STATE__ 不存在")
        except Exception as e:
            print(f"  检查失败: {e}")
        
        # 检查 Vue store
        print(f"\n[5] 检查 Vue Pinia store...")
        try:
            vue_data = page.evaluate("""() => {
                try {
                    const app = document.querySelector('#app').__vue_app__;
                    if (app && app.config.globalProperties.$pinia) {
                        const pinia = app.config.globalProperties.$pinia;
                        const stores = pinia._s || {};
                        const liveStore = stores['liveStream'];
                        if (liveStore) {
                            return {found: true, keys: Object.keys(liveStore), roomData: liveStore.roomData};
                        }
                        return {found: false, availableStores: Object.keys(stores)};
                    }
                    return {found: false, reason: 'no pinia'};
                } catch(e) {
                    return {found: false, error: e.toString()};
                }
            }""")
            print(f"  Vue store 结果: {json.dumps(vue_data, indent=2, ensure_ascii=False)[:500]}")
        except Exception as e:
            print(f"  检查失败: {e}")
        
        # 检查 video 元素
        print(f"\n[6] 检查 video 元素...")
        try:
            videos = page.evaluate("""() => {
                const vids = document.querySelectorAll('video');
                return Array.from(vids).map(v => ({
                    src: v.src,
                    currentSrc: v.currentSrc,
                    readyState: v.readyState,
                    paused: v.paused
                }));
            }""")
            print(f"  找到 {len(videos)} 个 video 元素")
            for i, v in enumerate(videos[:3]):
                print(f"  video[{i}]: src={v.get('src', 'N/A')[:80] if v.get('src') else 'N/A'}...")
        except Exception as e:
            print(f"  检查失败: {e}")
        
        # 检查捕获的响应
        print(f"\n[7] 分析捕获的 API 响应...")
        print(f"  共捕获 {len(all_responses)} 个响应")
        
        for url, data in all_responses.items():
            print(f"\n  URL: {url[:80]}...")
            if isinstance(data, dict):
                # 查找 roomInfo / pullConfig
                def find_room_info(obj, path='', depth=0):
                    if depth > 5:
                        return
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k == 'roomInfo' and isinstance(v, dict):
                                print(f"    找到 roomInfo @ {path}.{k}")
                                if 'pullConfig' in v:
                                    print(f"      pullConfig: {str(v['pullConfig'])[:200]}")
                                if 'roomId' in v:
                                    print(f"      roomId: {v['roomId']}")
                            elif k == 'pullConfig':
                                print(f"    找到 pullConfig @ {path}.{k} = {str(v)[:200]}")
                            elif isinstance(v, (dict, list)):
                                find_room_info(v, f"{path}.{k}", depth+1)
                    elif isinstance(obj, list) and depth < 3:
                        for i, item in enumerate(obj[:3]):
                            find_room_info(item, f"{path}[{i}]", depth+1)
                
                find_room_info(data)
        
        # 保存所有响应到文件
        print(f"\n[8] 保存调试数据到 debug_xhs_responses.json...")
        with open('debug_xhs_responses.json', 'w', encoding='utf-8') as f:
            json.dump(all_responses, f, ensure_ascii=False, indent=2)
        
        print(f"\n{'='*60}")
        print(f"调试完成，按 Enter 关闭浏览器...")
        print(f"{'='*60}")
        input()
        
        context.close()
        browser.close()

if __name__ == '__main__':
    url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.xiaohongshu.com/livestream/570223924590512327'
    debug_xhs_live(url)
