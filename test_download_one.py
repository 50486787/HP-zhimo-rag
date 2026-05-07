"""测试：自动化下载模型 + 第一张预览图（同名保存）"""
import json
import os
import time
import requests
from playwright.sync_api import sync_playwright
from config import SITE_BASE, DOWNLOAD_DIR
from db import init_db, get_cookies


def run():
    db_path = "downloads.db"
    init_db(db_path)
    cookies_json = get_cookies(db_path)
    if not cookies_json:
        print("[失败] 没有 cookie，请先运行 login.py")
        return False

    cookies = json.loads(cookies_json)
    download_url = None
    download_filename = None
    first_image_url = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.add_cookies(cookies)

        popups = []

        def on_page(new_page):
            if new_page.url != "about:blank":
                popups.append(new_page)
                print(f"[弹窗] {new_page.url}")

                def on_dl(download):
                    nonlocal download_url, download_filename
                    download_url = download.url
                    download_filename = download.suggested_filename
                    print(f"[捕获下载] {download.suggested_filename}")

                new_page.on("download", on_dl)

        context.on("page", on_page)
        page = context.new_page()

        # Step 1: 打开特权页面
        print("[1/6] 打开特权页面...")
        page.goto(
            f"{SITE_BASE}/personalCenter/usercenter_privilege.html",
            wait_until="networkidle",
            timeout=60000,
        )
        print("      页面已加载")

        # Step 2: 切换到"账号下载记录"tab
        print("[2/6] 切换到'账号下载记录'tab...")
        page.locator("text=账号下载记录").first.click(timeout=5000)
        page.wait_for_timeout(3000)
        print("      tab已切换")

        # Step 3: 点击模型ID打开详情弹窗
        print("[3/6] 点击模型ID打开详情...")
        page.locator("text=下载模型ID：").first.click(timeout=5000)

        for _ in range(30):
            if popups:
                break
            page.wait_for_timeout(1000)

        if not popups:
            print("[失败] 弹窗未出现")
            browser.close()
            return False

        popup = popups[0]

        # 等待弹窗 React 渲染
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        for _ in range(10):
            try:
                children = popup.evaluate(
                    "() => document.querySelector('#__next')?.children.length || 0"
                )
                if children > 0:
                    break
            except Exception:
                pass
            page.wait_for_timeout(1000)
        page.wait_for_timeout(3000)
        print("      弹窗已渲染")

        # Step 4: 提取第一张预览图 URL
        print("[4/6] 提取第一张预览图...")
        try:
            first_image_url = popup.evaluate("""() => {
                const nd = document.getElementById('__NEXT_DATA__');
                if (nd) {
                    try {
                        const data = JSON.parse(nd.textContent);
                        const pp = data?.props?.pageProps;
                        // imgArr 直接包含预览图 URL
                        const imgArr = pp?.imgArr || [];
                        if (imgArr.length > 0) return imgArr[0];
                        // activeSkuInfo.mainImageUrl
                        const mainUrl = pp?.activeSkuInfo?.mainImageUrl;
                        if (mainUrl) return mainUrl;
                        // largeImgArr
                        const largeArr = pp?.largeImgArr || [];
                        if (largeArr.length > 0) return largeArr[0];
                        // detailImageArr
                        const detailArr = pp?.detailImageArr || [];
                        if (detailArr.length > 0) return detailArr[0];
                    } catch (e) {}
                }
                return null;
            }""")
            if first_image_url:
                # 去掉 OSS 样式参数获取原图
                first_image_url = first_image_url.split("?x-oss-process")[0]
                print(f"      {first_image_url[:100]}...")
            else:
                print("      未找到预览图")
        except Exception as e:
            print(f"      提取预览图失败: {e}")

        # Step 5: 点击下载按钮
        print("[5/6] 点击下载按钮...")
        clicked = False
        for text in ["企业免费下载", "VIP免费下载", "免费下载", "下载模型"]:
            try:
                el = popup.locator(f":text('{text}')").first
                if el.count() > 0:
                    print(f"      文本'{text}'")
                    el.click(timeout=3000)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            pats = ["[class*=vipFreeDownload]", "[class*=normalDownload]",
                     "[class*=downloadBtn]", "[class*=vipFree]"]
            for pat in pats:
                try:
                    el = popup.locator(pat).first
                    if el.count() > 0:
                        print(f"      CSS: {pat}")
                        el.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    continue

        if not clicked:
            print("[失败] 未找到下载按钮")
            browser.close()
            return False

        # 等待下载事件
        print("[6/6] 等待下载事件...")
        for _ in range(30):
            if download_url:
                break
            try:
                page.wait_for_timeout(1000)
            except Exception:
                break

        if not download_url:
            print("[失败] 未捕获到下载URL")
            browser.close()
            return False

        browser.close()

    # === 下载文件 ===
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    month = time.strftime("%Y-%m")
    month_dir = os.path.join(DOWNLOAD_DIR, month)
    os.makedirs(month_dir, exist_ok=True)

    # 用下载文件名确定基础名（去掉扩展名）
    base_name = download_filename or "unknown.rar"
    base_name = os.path.splitext(base_name)[0]

    # 下载模型文件
    model_ext = os.path.splitext(download_filename)[1] or ".zip"
    model_dest = os.path.join(month_dir, f"{base_name}{model_ext}")

    print(f"\n下载模型: {base_name}{model_ext}")
    resp = session.get(download_url, stream=True, timeout=300)
    resp.raise_for_status()
    total = 0
    with open(model_dest, "wb") as f:
        for chunk in resp.iter_content(65536):
            f.write(chunk)
            total += len(chunk)
    print(f"  [模型] {model_dest} ({total / 1024 / 1024:.1f} MB)")

    # 下载预览图（同名 .jpg）
    if first_image_url:
        img_dest = os.path.join(month_dir, f"{base_name}.jpg")
        print(f"下载预览图: {base_name}.jpg")
        try:
            resp = session.get(first_image_url, stream=True, timeout=60)
            resp.raise_for_status()
            img_total = 0
            with open(img_dest, "wb") as f:
                for chunk in resp.iter_content(65536):
                    f.write(chunk)
                    img_total += len(chunk)
            print(f"  [预览] {img_dest} ({img_total / 1024:.0f} KB)")
        except Exception as e:
            print(f"  [预览] 下载失败: {e}")
    else:
        img_dest = None
        print("无预览图可下载")

    print(f"\n[完成] 模型: {model_dest}")
    if img_dest:
        print(f"[完成] 预览: {img_dest}")

    return True


if __name__ == "__main__":
    ok = run()
    if ok:
        print("\n流程验证通过！")
    else:
        print("\n流程验证失败。")
