"""知末登录模块 —— Playwright 手动登录 + cookie 提取"""
import json
from playwright.sync_api import sync_playwright
from config import SITE_BASE
from db import init_db, save_cookies


def login_and_save_cookies():
    """打开浏览器让用户手动登录，成功后提取 cookie 存入 SQLite。"""
    db_path = "downloads.db"
    init_db(db_path)

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

        print("请在浏览器中完成登录（扫码或账号密码）...")
        print("登录成功后，按 Enter 继续...")
        input()

        cookies = context.cookies()
        cookies_json = json.dumps(cookies, ensure_ascii=False)
        save_cookies(db_path, cookies_json)

        print(f"已保存 {len(cookies)} 条 cookie 到 {db_path}")
        browser.close()


if __name__ == "__main__":
    login_and_save_cookies()
