"""v7: 修复版 - 先捕获弹窗保存HTML，再捕获下载API"""
import json
import re
from playwright.sync_api import sync_playwright
from config import SITE_BASE
from db import init_db, get_cookies


def capture():
    db_path = "downloads.db"
    init_db(db_path)
    cookies_str = get_cookies(db_path)
    if not cookies_str:
        print("没有 cookie")
        return

    cookies = json.loads(cookies_str)
    all_api = []
    popup_ready = False
    popup_page = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.add_cookies(cookies)

        page = context.new_page()

        # 监听弹窗（从主页面）
        def on_popup(popup):
            nonlocal popup_ready, popup_page
            print(f"\n[弹窗创建] {popup.url}")
            popup_page = popup

            # 监听弹窗内的所有 API 请求
            def popup_request(request):
                if "api.znzmo.com" in request.url:
                    all_api.append({
                        "method": request.method,
                        "url": request.url,
                        "post_data": request.post_data,
                        "ct": request.headers.get("content-type", ""),
                    })

            def popup_response(response):
                if "api.znzmo.com" in response.url and \
                   any(k in response.url for k in ["download", "qualify", "fileUrl"]):
                    try:
                        body = response.body().decode('utf-8', errors='replace')
                    except:
                        body = "(binary)"
                    print(f"\n[{response.status}] {response.request.method} {response.url}")
                    print(f"  Resp: {body[:500]}")
                    if response.request.post_data:
                        print(f"  Req: {response.request.post_data}")

            def popup_download(download):
                print(f"\n[下载事件] {download.url}")

            popup.on("request", popup_request)
            popup.on("response", popup_response)
            popup.on("download", popup_download)

            # 监听页面加载完成
            popup.wait_for_load_state("domcontentloaded")
            try:
                html = popup.content()
                with open("model_page_debug.html", "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"[HTML已保存] {len(html)} 字节")

                # 快速查找关键 JS
                for kw in ['sign', 'fileUrl', 'getDownloadUrl', 'downloadFile']:
                    matches = re.findall(r'[^;]{0,100}' + re.escape(kw) + r'[^;]{0,200}', html)
                    if matches:
                        print(f"\n  含 '{kw}' 的代码片段:")
                        for m in matches[:3]:
                            print(f"    ...{m.strip()[:200]}...")
            except Exception as e:
                print(f"[HTML保存失败] {e}")

            popup_ready = True

        page.on("popup", on_popup)

        # 也可以监听 context 级别的新页面
        def on_context_page(new_page):
            nonlocal popup_ready, popup_page
            if new_page != page and not popup_ready:
                print(f"\n[context新页面] {new_page.url}")
                on_popup(new_page)

        context.on("page", on_context_page)

        print("打开特权页面...")
        page.goto(f"{SITE_BASE}/personalCenter/usercenter_privilege.html",
                  wait_until="networkidle")

        print("\n1. 点击'账号下载记录'tab")
        print("2. 点击任意模型ID打开详情（新标签页）")
        print("3. 等模型页面完全加载")
        print("\n完成后按 Enter...")
        input()

        if popup_page:
            print(f"\n弹窗URL: {popup_page.url}")
        else:
            print("\n警告: 未检测到弹窗")

        print("\n现在点击下载按钮，等待下载弹窗/完成，然后按 Enter...")
        input()

        print(f"\n=== 结果: {len(all_api)} 条API请求 ===")
        for req in all_api:
            if any(k in req["url"] for k in ["download", "qualify", "fileUrl"]):
                print(f"\n{req['method']} {req['url']}")
                if req.get("post_data"):
                    print(f"  Body: {req['post_data']}")

        browser.close()


if __name__ == "__main__":
    capture()
