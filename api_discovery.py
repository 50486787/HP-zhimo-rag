"""知末 API 发现模块 —— 通过浏览器抓包确认下载记录/模型详情/下载 API"""
import json
from playwright.sync_api import sync_playwright
from config import PRIVILEGE_PAGE
from db import init_db, get_cookies


def discover_apis():
    """打开已登录的浏览器，监听网络请求，发现关键 API 端点。"""
    db_path = "downloads.db"
    init_db(db_path)
    cookies_str = get_cookies(db_path)
    if not cookies_str:
        print("没有找到 cookie，请先运行 python login.py")
        return

    cookies = json.loads(cookies_str)
    captured = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()

        def log_request(request):
            url = request.url
            # 跳过明显的静态资源
            skip_ext = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".css", ".woff", ".woff2", ".ttf", ".eot")
            if any(url.endswith(ext) for ext in skip_ext):
                return
            captured.append({
                "method": request.method,
                "url": url,
                "type": request.resource_type,
            })

        page.on("request", log_request)

        print("正在打开下载记录页面...")
        page.goto(PRIVILEGE_PAGE, wait_until="networkidle")

        print("请手动操作：点击'账号下载记录'tab，翻几页，点击模型ID看详情...")
        print("操作完成后按 Enter 继续...")
        input()

        keywords = ["download", "record", "list", "page", "enterprise", "model",
                    "detail", "privilege", "member", "gold", "coin", "order"]
        print("\n=== 可疑 API 请求（匹配关键词）===")
        seen_kw = set()
        for req in captured:
            url = req["url"]
            if any(kw in url.lower() for kw in keywords):
                if url not in seen_kw:
                    seen_kw.add(url)
                    print(f"  {req['method']} {url}")

        print("\n=== 全部 XHR/Fetch 请求 ===")
        seen = set()
        for req in captured:
            if req["url"] not in seen:
                seen.add(req["url"])
                print(f"  {req['method']} {req['url']}")

        browser.close()
        print(f"\n共捕获 {len(captured)} 个请求。请将可疑 API 端点记下来。")


if __name__ == "__main__":
    discover_apis()
