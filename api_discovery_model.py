"""抓模型下载链接 —— 监听下载事件，捕获生成 CDN URL 的请求"""
import json
import re
from playwright.sync_api import sync_playwright
from config import PRIVILEGE_PAGE
from db import init_db, get_cookies


MODEL_ID = "1135455615"


def discover():
    db_path = "downloads.db"
    init_db(db_path)
    cookies_str = get_cookies(db_path)
    if not cookies_str:
        print("没有找到 cookie，请先运行 python login.py")
        return

    cookies = json.loads(cookies_str)
    all_urls = set()
    download_url = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()

        # 监听所有导航和请求
        def on_request(request):
            all_urls.add(request.url)

        def on_download(download):
            nonlocal download_url
            download_url = download.url
            print(f"\n>>> 捕获到下载: {download.url}")

        def on_popup(popup):
            popup.on("request", on_request)
            popup.on("framenavigated", lambda frame: all_urls.add(frame.url))

        page.on("request", on_request)
        page.on("download", on_download)
        page.on("popup", on_popup)

        print(f"正在打开下载记录页...")
        page.goto(PRIVILEGE_PAGE, wait_until="networkidle")

        print(f"\n请操作：")
        print(f"1. 点击'账号下载记录'tab")
        print(f"2. 点击模型 {MODEL_ID} 进入详情页")
        print(f"3. 点下载按钮")
        print(f"4. 等下载弹出来（或新标签页打开）")
        print(f"\n完成后按 Enter...")
        input()

        # 提取所有 cdn url
        page_text = page.content()
        cdn_urls = set(re.findall(r'https?://[^"\s<>]*cdn[^"\s<>]*\.(zip|rar|7z|max|skp|fbx|obj|3ds)[^"\s<>]*', page_text, re.I))
        cdn_urls2 = set(re.findall(r'https?://[^"\s<>]*cdn\.znzmo\.com[^"\s<>]*', page_text))

        print("\n=== 捕获的下载 URL ===")
        if download_url:
            print(f"  {download_url}")

        print("\n=== 页面源码中的 CDN 链接 ===")
        for url in cdn_urls | cdn_urls2:
            print(f"  {url}")

        print("\n=== 所有捕获的 URL（含 cdn）===")
        for url in sorted(all_urls):
            if "cdn" in url or "download" in url.lower() or "attachment" in url:
                print(f"  {url}")

        browser.close()


if __name__ == "__main__":
    discover()
