"""获取 ltsx1219 的数字 principalId"""
import sys, json, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Playwright not available")
    sys.exit(1)

url = 'https://live.kuaishou.com/u/ltsx1219'

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=[
        "--disable-blink-features=AutomationControlled",
    ])
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = context.new_page()
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
    """)

    # 拦截所有请求，查找 principalId
    api_calls = {}

    def on_request(request):
        url = request.url
        if 'principalId' in url or 'userId' in url or 'graphql' in url:
            api_calls[url] = True

    def on_response(response):
        url = response.url
        if any(k in url for k in ["graphql", "user/profile", "live/user", "profile"]):
            try:
                ct = response.body()
                if ct and len(ct) < 100000:
                    api_calls[f"RESP:{url}"] = json.loads(ct.decode("utf-8", errors="replace"))
            except:
                pass

    page.on("request", on_request)
    page.on("response", on_response)

    print("Visiting page...")
    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(5000)

    # 检查最终 URL（可能有重定向）
    print(f"Final URL: {page.url}")

    # 检查所有包含 principalId 的请求
    print(f"\nAPI calls with principalId/userId: {len(api_calls)}")
    for k, v in api_calls.items():
        print(f"  {k[:120]}")

    # 检查 CAPTCHA
    captcha_info = page.evaluate("() => window.__CAPTCHA_INFO__ || null")
    print(f"\nCAPTCHA info: {json.dumps(captcha_info, ensure_ascii=False)[:500] if captcha_info else 'None'}")

    # 检查 VUE 实例中的数据
    vue_data = page.evaluate("""() => {
        // 尝试从 Vue 实例中获取数据
        const app = document.querySelector('#app');
        if (app && app.__vue_app__) {
            const store = app.__vue_app__.config.globalProperties.$store;
            if (store) {
                const state = store.state;
                return { hasVueApp: true, stateKeys: Object.keys(state) };
            }
        }
        // 检查 __VUE_SSR_SETTERS__
        const setters = window.__VUE_SSR_SETTERS__;
        if (setters) {
            return { hasVueApp: true, ssrSetters: Object.keys(setters) };
        }
        return { hasVueApp: false };
    }""")
    print(f"\nVue data: {json.dumps(vue_data, ensure_ascii=False)}")

    # 尝试从 network 请求日志中获取更多信息
    print("\n--- Trying to find principalId from network requests ---")
    # 查看页面中所有 JS 请求
    network_urls = page.evaluate("""() => {
        return performance.getEntriesByType('resource')
            .filter(r => r.name.includes('liveroom') || r.name.includes('principalId') || r.name.includes('graphql'))
            .map(r => r.name);
    }""")
    for nurl in network_urls:
        print(f"  {nurl[:150]}")

    browser.close()
