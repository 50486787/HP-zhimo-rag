"""知末登录模块 —— Playwright 手动登录 + cookie 提取"""
import json
import time
from playwright.sync_api import sync_playwright
from config import SITE_BASE, DB_PATH, PRIVILEGE_PAGE
from db import init_db, save_cookies


def _trigger_api_cookies(page):
    """访问需要登录的页面以触发 API 域 cookie 设置。"""
    try:
        page.goto(PRIVILEGE_PAGE, wait_until="commit", timeout=15000)
    except Exception:
        print("(页面跳转，cookie 已触发)")
    page.wait_for_timeout(2000)


def login_and_save_cookies(gui_mode=False):
    """打开浏览器让用户手动登录，成功后提取 cookie 存入 SQLite。"""
    init_db(DB_PATH)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto(SITE_BASE, wait_until="domcontentloaded")

        if gui_mode:
            print("请在浏览器中完成登录，登录成功后自动保存...")
            for _ in range(150):
                time.sleep(2)
                cookies = context.cookies()
                for c in cookies:
                    if c["name"] == "znzmo-id" and c.get("value", "").strip():
                        print("检测到登录成功，等待 cookie 稳定...")
                        time.sleep(5)
                        _trigger_api_cookies(page)
                        break
                else:
                    continue
                break
        else:
            print("请在浏览器中完成登录（扫码或账号密码）...")
            print("登录成功后按 Enter 继续...")
            input()
            time.sleep(2)
            _trigger_api_cookies(page)

        cookies = context.cookies()
        has_session = any(c["name"] == "SESSION" and "api.znzmo.com" in c.get("domain", "")
                          for c in cookies)
        has_znzmo_id = any(c["name"] == "znzmo-id" for c in cookies)
        print(f"SESSION{' ✅' if has_session else ' ❌'}  znzmo-id{' ✅' if has_znzmo_id else ' ❌'}")

        cookies_json = json.dumps(cookies, ensure_ascii=False)
        save_cookies(DB_PATH, cookies_json)
        print(f"已保存 {len(cookies)} 条 cookie")
        browser.close()


if __name__ == "__main__":
    login_and_save_cookies()
