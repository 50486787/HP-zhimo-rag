"""知末下载器 —— 全量/增量模式下载模型文件和预览图"""
import os
import re
import random
import time
import json
import argparse
import requests
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

from config import (
    API_BASE, SITE_BASE, DOWNLOAD_DIR, DB_PATH,
    random_delay, random_page_delay, random_rest_duration, random_ua,
    FULL_MODE_WORK_SECONDS, MAX_CONSECUTIVE_429, RATE_LIMIT_BACKOFF,
    KEEPALIVE_INTERVAL,
)
from db import (
    init_db, insert_download_record, get_download_record,
    update_download_status, save_checkpoint, get_checkpoint, get_cookies,
)

# === API 端点（由 api_discovery 确认） ===
ACCOUNT_INFO_API = f"{API_BASE}/enterprise/accountInfo"
CONSUMER_LIST_API = f"{API_BASE}/enterprise/consumerList"
CHILD_CONSUMER_LIST_API = f"{API_BASE}/enterprise/childConsumerList"
# === commodityType 映射 ===
COMMODITY_TYPE_MAP = {
    0: "3d", 3: "3d", 4: "su", 5: "sgt",
    2: "tietu", 8: "ziliaoku", 20: "wenben",
}

MODEL_PAGE_TEMPLATES = {
    "3d": "https://3d.znzmo.com/3dmoxing/{skuid}.html",
    "su": "https://su.znzmo.com/sumoxing/{skuid}.html",
    "sgt": "https://sgt.znzmo.com/sgt/{skuid}.html",
    "tietu": "https://tietu.znzmo.com/tietu/{skuid}.html",
    "ziliaoku": "https://www.znzmo.com/ziliaoku/{skuid}.html",
    "wenben": "https://wenben.znzmo.com/wenben/{skuid}.html",
}

IMAGE_COMMODITY_TYPES = {2}


def sanitize_filename(name):
    """去除文件名中的非法字符和首尾空白。"""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def is_image_type(commodity_type):
    """贴图类 (commodityType=2) 本身即是图片，无需单独下载预览图。"""
    return commodity_type in IMAGE_COMMODITY_TYPES


def commodity_type_name(commodity_type):
    """返回 commodityType 对应的子域名前缀。"""
    return COMMODITY_TYPE_MAP.get(commodity_type, "other")


def get_month_from_time(dt_str):
    """从 'YYYY-MM-DD HH:MM:SS' 提取 'YYYY-MM'。"""
    return dt_str[:7] if dt_str else ""


def parse_record(item):
    """将 API 返回的下载记录解析为统一格式。"""
    skuid = str(item.get("skuid", ""))
    return {
        "model_id": skuid,
        "model_name": sanitize_filename(skuid),
        "account_id": item.get("accountId", ""),
        "account_name": item.get("nickName", ""),
        "download_time": item.get("createTime", ""),
        "cost": (item.get("goldAmount", 0) or 0) / 100,
        "commodity_type": item.get("commodityType", 0),
        "month": get_month_from_time(item.get("createTime", "")),
    }


def build_session(cookies_json):
    """用保存的 cookie 构建 requests.Session。"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": SITE_BASE,
    })
    if cookies_json:
        cookies = json.loads(cookies_json)
        for c in cookies:
            session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )
    return session


def api_ok(data):
    """检查 API 返回是否成功 (errorCode 可能是字符串或整数 0)。"""
    code = data.get("error", {}).get("errorCode")
    return code in (0, "0")


def get_enterprise_info(session):
    """获取企业账号信息，返回 enterpriseId 和 memberLevel。"""
    resp = session.get(ACCOUNT_INFO_API, headers={"Referer": SITE_BASE + "/"})
    resp.raise_for_status()
    data = resp.json()
    if not api_ok(data):
        raise Exception(f"accountInfo API error: {data}")
    info = data.get("data", {})
    return {
        "enterprise_id": info.get("enterpriseId"),
        "member_level": info.get("memberLevel", 0),
        "nick_name": info.get("nickName", ""),
    }


def get_consumer_list_api(member_level):
    """memberLevel==11 时用子账号接口，否则用主账号接口。"""
    if member_level == 11:
        return CHILD_CONSUMER_LIST_API
    return CONSUMER_LIST_API


def fetch_records_page(session, api_url, enterprise_id, page, page_size=10):
    """获取一页下载记录。"""
    params = f"?page={page}&pageSize={page_size}&enterpriseId={enterprise_id}"
    url = api_url + params
    resp = session.post(
        url,
        headers={
            "Referer": SITE_BASE + "/personalCenter/usercenter_privilege.html",
            "User-Agent": random_ua(),
            "Content-Type": "application/json",
        },
    )
    if resp.status_code == 429:
        return None, 429
    resp.raise_for_status()
    data = resp.json()
    if not api_ok(data):
        raise Exception(f"consumerList API error: {data}")
    result = data.get("data", {})
    return result.get("list", []), result.get("totalCount", 0)


def download_one_model(context, model_id, commodity_type):
    """用 Playwright 打开模型详情页，点击下载按钮，捕获 CDN URL 和预览图 URL。

    返回 (download_url, suggested_filename, preview_url)。
    """
    type_name = commodity_type_name(commodity_type)
    template = MODEL_PAGE_TEMPLATES.get(type_name)
    if not template:
        raise Exception(f"不支持的 commodityType: {commodity_type}")

    page_url = template.format(skuid=model_id)
    page = context.new_page()

    download_url = None
    download_filename = None

    def on_download(download):
        nonlocal download_url, download_filename
        download_url = download.url
        download_filename = download.suggested_filename

    page.on("download", on_download)

    try:
        page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        # 等待 React 水合完成
        try:
            page.wait_for_selector("#__NEXT_DATA__", timeout=8000)
        except Exception:
            page.wait_for_timeout(1500)

        # 从 __NEXT_DATA__ 提取第一张预览图 URL
        preview_url = page.evaluate("""() => {
            const nd = document.getElementById('__NEXT_DATA__');
            if (nd) {
                try {
                    const data = JSON.parse(nd.textContent);
                    const pp = data?.props?.pageProps;
                    const imgArr = pp?.imgArr || [];
                    if (imgArr.length > 0) return imgArr[0];
                    const mainUrl = pp?.activeSkuInfo?.mainImageUrl;
                    if (mainUrl) return mainUrl;
                    // 贴图类: 尝试 detailImageArr / largeImgArr
                    const detailArr = pp?.detailImageArr || pp?.largeImgArr || [];
                    if (detailArr.length > 0) return detailArr[0];
                } catch (e) {}
            }
            return null;
        }""")
        if preview_url:
            preview_url = preview_url.split("?x-oss-process")[0]

        # 点击下载按钮
        clicked = False

        # 第一轮：精确文本匹配
        button_texts = [
            "企业免费下载", "VIP免费下载", "免费下载", "下载模型",
            "下载", "下载贴图", "下载素材", "下载图片", "免费下载贴图",
            "免费下载素材", "立即下载", "下载协议", "同意并下载",
        ]
        for text in button_texts:
            try:
                el = page.locator(f":text-is('{text}')").first
                if el.count() > 0:
                    el.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                continue

        # 第二轮：模糊文本匹配（包含关键词即可）
        if not clicked:
            for kw in ["下载", "download"]:
                try:
                    el = page.locator(f"[class*={kw} i], [id*={kw} i]").first
                    if el.count() > 0:
                        el.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    continue

        # 第三轮：CSS 选择器
        if not clicked:
            for sel in [
                "[class*=vipFreeDownload]", "[class*=downloadBtn]",
                "[class*=vipFree]", "[class*=freeDownload]",
                "[class*=downloadWrap]",  # 贴图页 detail__downloadWrap
                "[class*=btnBox]",         # 按钮容器兜底
                "a[class*=download]", "button[class*=download]",
                ".download-btn", "#download-btn",
            ]:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0:
                        el.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    continue

        # 第四轮：遍历页面上所有按钮，找包含"下载"文字的那个
        if not clicked:
            buttons = page.locator("button, a, [role=button]").all()
            for btn in buttons:
                try:
                    text = btn.inner_text()
                    if text and ("下载" in text or "download" in text.lower()):
                        btn.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    continue

        if not clicked:
            raise Exception(
                f"未找到下载按钮 (type={commodity_type}/{type_name}, url={page_url})")

        # 等待下载事件，如果超时则尝试处理弹窗（如"下载协议"确认弹窗）
        for _ in range(20):
            if download_url:
                break
            page.wait_for_timeout(500)

        if not download_url:
            # 可能弹出了协议确认弹窗，尝试点击"同意"/"确认"
            for agree_text in ["同意", "确定", "同意并下载", "同意下载", "确认下载",
                                "Agree", "OK", "Confirm"]:
                try:
                    el = page.locator(f":text-is('{agree_text}')").first
                    if el.count() > 0 and el.is_visible():
                        el.click(timeout=3000)
                        break
                except Exception:
                    continue
            # 再等下载事件
            for _ in range(60):
                if download_url:
                    break
                page.wait_for_timeout(500)

        if not download_url:
            raise Exception("未捕获到 CDN 下载 URL")

        return download_url, download_filename, preview_url
    finally:
        page.close()


def download_file(session, url, dest_path, timeout=300):
    """下载文件到指定路径。"""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    resp = session.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    tmp_path = dest_path + ".tmp"
    with open(tmp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
            downloaded += len(chunk)
    if total > 0 and downloaded < total:
        os.remove(tmp_path)
        return False
    os.replace(tmp_path, dest_path)
    return True


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def run(mode, target_month=None, force=False, days=30):
    """主入口。"""
    init_db(DB_PATH)
    cookies_json = get_cookies(DB_PATH)
    if not cookies_json:
        print("未找到 cookie，请先运行: python login.py")
        return

    session = build_session(cookies_json)

    # 启动 Playwright 浏览器（用于触发下载，获取 CDN URL）
    cookies = json.loads(cookies_json)
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False, args=[
        "--window-position=-32000,-32000",  # 窗口移到屏幕外，不弹窗
    ])
    pw_context = browser.new_context()
    pw_context.add_cookies(cookies)

    # 获取企业信息
    print("获取企业信息...")
    try:
        ent_info = get_enterprise_info(session)
        enterprise_id = ent_info["enterprise_id"]
        member_level = ent_info["member_level"]
        nick_name = ent_info["nick_name"]
        print(f"  enterpriseId: {enterprise_id}, memberLevel: {member_level}, nickName: {nick_name}")
    except Exception as e:
        print(f"获取企业信息失败: {e}")
        pw_context.close()
        browser.close()
        playwright.stop()
        return

    api_url = get_consumer_list_api(member_level)

    # 断点恢复
    ...
    if mode == "full":
        cp = get_checkpoint(DB_PATH)
        start_page = cp["current_page"] if cp and cp["mode"] == "full" else 1
        print(f"全量模式: 从第 {start_page} 页开始")
    else:
        start_page = 1
        cutoff_time = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        target = target_month or f"最近{days}天"
        if force:
            target += " (强制重新下载)"
        print(f"增量模式: {target}")

    consecutive_429 = 0
    request_count = 0
    work_start = time.time()
    page = start_page
    highest_page_seen = start_page
    total_pages = None

    while True:
        # 全量模式分段休息
        if mode == "full" and time.time() - work_start > FULL_MODE_WORK_SECONDS:
            rest = random_rest_duration()
            print(f"已运行约 1 小时，休息 {rest:.0f} 秒...")
            time.sleep(rest)
            work_start = time.time()

        # 翻页间隔
        if page > start_page:
            delay = random_page_delay()
            print(f"翻页间隔 {delay:.1f}s...")
            time.sleep(delay)

        print(f"获取第 {page} 页...")
        try:
            records, total = fetch_records_page(session, api_url, enterprise_id, page)
        except Exception as e:
            print(f"获取第 {page} 页失败: {e}")
            save_checkpoint(DB_PATH, mode, page, total_pages)
            break

        if records is None:  # 429
            consecutive_429 += 1
            if consecutive_429 >= MAX_CONSECUTIVE_429:
                print(f"连续 {MAX_CONSECUTIVE_429} 次 429，停止。")
                save_checkpoint(DB_PATH, mode, page, total_pages)
                pw_context.close()
                browser.close()
                playwright.stop()
                return
            delay = RATE_LIMIT_BACKOFF * consecutive_429
            print(f"429 限流，等待 {delay}s...")
            time.sleep(delay)
            continue

        consecutive_429 = 0

        if total and not total_pages:
            total_pages = (total + 9) // 10
            print(f"总记录数: {total}, 约 {total_pages} 页")

        if not records:
            print(f"第 {page} 页无记录，结束。")
            break

        # 增量模式：过滤时间范围
        if mode == "incremental":
            filtered = [r for r in records if r.get("createTime", "") >= cutoff_time]
            if len(filtered) < len(records):
                print(f"第 {page} 页: {len(records)} 条中 {len(filtered)} 条在 {days} 天内")
            if not filtered:
                print(f"已到达 {days} 天边界，结束。")
                break
            records = filtered

        print(f"第 {page} 页: {len(records)} 条记录")

        for item in records:
            record = parse_record(item)
            model_id = record["model_id"]
            commodity_type = record["commodity_type"]
            is_img = is_image_type(commodity_type)

            # 去重（--force 时跳过此检查）
            if not force:
                existing = get_download_record(DB_PATH, model_id)
                if existing and existing["status"] == "done":
                    print(f"  [{model_id}] 已下载，跳过")
                    continue

            insert_download_record(DB_PATH, record)

            month = record["month"] or (target_month if target_month else "unknown")
            type_name = commodity_type_name(commodity_type)
            if type_name == "su":
                month_dir = os.path.join(DOWNLOAD_DIR, month)
            else:
                month_dir = os.path.join(DOWNLOAD_DIR, month, type_name)
            ensure_dir(month_dir)

            # 用 Playwright 浏览器触发下载，捕获 CDN URL
            try:
                cdn_url, cdn_filename, preview_url = download_one_model(
                    pw_context, model_id, commodity_type)
            except Exception as e:
                print(f"  [{model_id}] 获取下载链接失败: {e}")
                update_download_status(DB_PATH, model_id, "failed", error_msg=str(e))
                continue

            # 从 CDN 文件名确定扩展名和基础名
            if cdn_filename:
                name_no_ext, ext = os.path.splitext(cdn_filename)
                if not ext:
                    ext = ".zip"
            else:
                name_no_ext = model_id
                ext = ".zip"

            model_name_part = sanitize_filename(name_no_ext)

            # 文件路径
            if is_img:
                file_path = os.path.join(month_dir, f"{model_name_part}_{model_id}{ext}")
                preview_path = None
            else:
                file_path = os.path.join(month_dir, f"{model_name_part}_{model_id}{ext}")
                preview_path = os.path.join(month_dir, f"{model_name_part}_{model_id}.jpg")

            # 随机延迟后下载模型文件
            delay = random_delay()
            print(f"  [{model_id}] 下载 {model_name_part}{ext} (等待 {delay:.1f}s)...")
            time.sleep(delay)

            try:
                ok = download_file(session, cdn_url, file_path)
                if not ok:
                    raise Exception("文件大小不匹配")
            except Exception as e:
                print(f"  [{model_id}] 下载失败: {e}")
                update_download_status(DB_PATH, model_id, "failed", error_msg=str(e))
                continue

            # 非贴图类型：下载预览图
            if not is_img and preview_url:
                try:
                    time.sleep(random.uniform(2, 5))
                    download_file(session, preview_url, preview_path, timeout=60)
                    print(f"  [{model_id}] 预览图已保存")
                except Exception as e:
                    print(f"  [{model_id}] 预览图下载失败: {e}")
                    preview_path = None

            update_download_status(DB_PATH, model_id, "done",
                                   file_path=file_path, preview_path=preview_path)
            print(f"  [{model_id}] 完成")

            request_count += 1
            if request_count % KEEPALIVE_INTERVAL == 0:
                try:
                    session.get(SITE_BASE, timeout=10)
                    print("  [keepalive] 刷新会话")
                except Exception:
                    pass

        highest_page_seen = page
        save_checkpoint(DB_PATH, mode, highest_page_seen, total_pages)
        page += 1

        # 全量模式下给一个明确的页数感知
        if mode == "full" and total_pages and page > total_pages:
            print(f"已到达最后一页 (第 {total_pages} 页)，结束。")
            break

    pw_context.close()
    browser.close()
    playwright.stop()
    print(f"下载完成。最后页码: {highest_page_seen}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="知末下载记录归档工具")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental",
                        help="全量模式 (full) 或增量模式 (incremental)")
    parser.add_argument("--month", help="指定月份 (YYYY-MM)")
    parser.add_argument("--force", action="store_true",
                        help="强制重新下载，跳过已下载记录的去重检查")
    parser.add_argument("--days", type=int, default=30,
                        help="增量模式的天数范围 (默认 30)")
    args = parser.parse_args()
    run(args.mode, args.month, force=args.force, days=args.days)
