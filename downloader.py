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
    """获取企业账号信息，返回 enterpriseId、memberLevel 和 nickName。"""
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


def download_one_model(context, model_id, commodity_type, save_dir):
    """用 Playwright 打开模型详情页，点击下载按钮，保存文件到 save_dir。

    返回 (saved_path, suggested_filename, preview_url)。
    """

    type_name = commodity_type_name(commodity_type)
    template = MODEL_PAGE_TEMPLATES.get(type_name)
    if not template:
        raise Exception(f"不支持的 commodityType: {commodity_type}")

    page_url = template.format(skuid=model_id)
    page = context.new_page()

    download_obj = None
    download_filename = None

    def on_download(download):
        nonlocal download_obj, download_filename
        download_obj = download
        download_filename = download.suggested_filename

    page.on("download", on_download)

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

    # 等待下载事件，如果超时则尝试处理弹窗
    for _ in range(20):
        if download_obj:
            break
        page.wait_for_timeout(500)

    if not download_obj:
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
            if download_obj:
                break
            page.wait_for_timeout(500)

    if not download_obj:
        raise Exception("未捕获到下载事件")

    # 用 Playwright 保存下载文件（等待下载完成，防止提前关页面导致中断）
    tmp_path = os.path.join(save_dir, f".tmp_dl_{model_id}")
    try:
        download_obj.save_as(tmp_path)
    finally:
        page.close()
    return tmp_path, download_filename, preview_url


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


class DownloadJob:
    """下载任务，可从 GUI 或 CLI 调用。"""

    def __init__(self, mode, start_date=None, end_date=None,
                 skip_downloaded=True, download_dir=None,
                 log_callback=None, progress_callback=None):
        self.mode = mode
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime("%Y-%m-%d")
        self.skip_downloaded = skip_downloaded
        self.download_dir = download_dir or DOWNLOAD_DIR
        self.log_callback = log_callback or (lambda lvl, msg: print(msg))
        self.progress_callback = progress_callback or (lambda p, t, c: None)
        self.stop_flag = False
        if self.mode == "incremental" and not self.start_date:
            raise ValueError("start_date is required for incremental mode")

    def stop(self):
        self.stop_flag = True

    def log(self, msg, level="info"):
        self.log_callback(level, msg)

    def ensure_job_dir(self, path):
        os.makedirs(path, exist_ok=True)

    def run(self):
        """执行下载任务（原 run() 逻辑）。"""
        init_db(DB_PATH)
        cookies_json = get_cookies(DB_PATH)
        if not cookies_json:
            self.log("未找到 cookie，请先登录", "error")
            return

        session = build_session(cookies_json)
        cookies = json.loads(cookies_json)

        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=False, args=[
            "--window-position=-32000,-32000",
        ])
        pw_context = browser.new_context()
        pw_context.add_cookies(cookies)

        try:
            self.log("获取企业信息...")
            ent_info = get_enterprise_info(session)
            enterprise_id = ent_info["enterprise_id"]
            nick_name = ent_info.get("nick_name", "")
            self.log(f"账号: {nick_name} (ID: {enterprise_id})")
            member_level = ent_info["member_level"]
        except Exception as e:
            self.log(f"获取企业信息失败: {e}", "error")
            pw_context.close()
            browser.close()
            playwright.stop()
            return

        api_url = get_consumer_list_api(member_level)

        if self.mode == "full":
            cp = get_checkpoint(DB_PATH)
            start_page = cp["current_page"] if cp and cp["mode"] == "full" else 1
        else:
            start_page = 1
            cutoff_time = self.start_date + " 00:00:00" if self.start_date else None
            end_time = self.end_date + " 23:59:59"

        # 启动日志
        if self.mode == "full":
            self.log(f"全量模式: 从第 {start_page} 页开始")
        else:
            self.log(f"增量模式: {self.start_date} 至 {self.end_date}")

        consecutive_429 = 0
        request_count = 0
        downloaded_count = 0
        work_start = time.time()
        page = start_page
        highest_page_seen = start_page
        total_pages = None

        while True:
            if self.stop_flag:
                self.log("收到停止信号，正在退出...", "warn")
                save_checkpoint(DB_PATH, self.mode, highest_page_seen, total_pages)
                break

            if self.mode == "full" and time.time() - work_start > FULL_MODE_WORK_SECONDS:
                rest = random_rest_duration()
                self.log(f"已运行约 1 小时，休息 {rest:.0f} 秒...")
                for _ in range(int(rest)):
                    if self.stop_flag:
                        break
                    time.sleep(1)
                if self.stop_flag:
                    save_checkpoint(DB_PATH, self.mode, highest_page_seen, total_pages)
                    break
                work_start = time.time()

            self.log(f"获取第 {page} 页...")
            try:
                records, total = fetch_records_page(session, api_url, enterprise_id, page)
            except Exception as e:
                self.log(f"获取第 {page} 页失败: {e}", "error")
                save_checkpoint(DB_PATH, self.mode, page, total_pages)
                break

            if records is None:  # 429
                consecutive_429 += 1
                if consecutive_429 >= MAX_CONSECUTIVE_429:
                    self.log(f"连续 {MAX_CONSECUTIVE_429} 次 429，停止。", "error")
                    save_checkpoint(DB_PATH, self.mode, page, total_pages)
                    break
                delay = RATE_LIMIT_BACKOFF * consecutive_429
                self.log(f"429 限流，等待 {delay}s...", "warn")
                time.sleep(delay)
                continue

            consecutive_429 = 0

            if total and not total_pages:
                total_pages = (total + 9) // 10
                self.log(f"总记录数: {total}, 约 {total_pages} 页")

            if not records:
                self.log(f"第 {page} 页无记录，结束。")
                break

            # 增量模式：过滤日期范围
            if self.mode == "incremental":
                # API 按时间倒序，本页全部比范围上限还新 → 跳过继续翻页
                oldest_on_page = min(r.get("createTime", "") for r in records)
                if oldest_on_page > end_time:
                    self.log(f"第 {page} 页: 全部比 {self.end_date} 新，跳过")
                    page += 1
                    save_checkpoint(DB_PATH, self.mode, page - 1, total_pages)
                    time.sleep(1)
                    continue
                # 本页全部比范围下限还旧 → 到达边界，停止
                newest_on_page = max(r.get("createTime", "") for r in records)
                if newest_on_page < cutoff_time:
                    self.log(f"已到达日期范围边界，结束。")
                    break
                filtered = [r for r in records
                            if r.get("createTime", "") >= cutoff_time
                            and r.get("createTime", "") <= end_time]
                if len(filtered) < len(records):
                    self.log(f"第 {page} 页: {len(records)} 条中 {len(filtered)} 条在日期范围内")
                records = filtered

            # 翻页间隔（实际有内容的翻页才等）
            if page > start_page:
                delay = random_page_delay()
                self.log(f"翻页间隔 {delay:.1f}s...")
                time.sleep(delay)

            self.log(f"第 {page} 页: {len(records)} 条记录")

            for item in records:
                if self.stop_flag:
                    break
                record = parse_record(item)
                model_id = record["model_id"]
                commodity_type = record["commodity_type"]
                is_img = is_image_type(commodity_type)

                if self.skip_downloaded:
                    existing = get_download_record(DB_PATH, model_id)
                    if existing and existing["status"] == "done":
                        self.log(f"  [{model_id}] 已下载，跳过")
                        continue

                insert_download_record(DB_PATH, record)

                month = record["month"] or self.end_date[:7]
                type_name = commodity_type_name(commodity_type)
                if type_name == "su":
                    month_dir = os.path.join(self.download_dir, month)
                else:
                    month_dir = os.path.join(self.download_dir, month, type_name)
                self.ensure_job_dir(month_dir)

                self.ensure_job_dir(month_dir)

                try:
                    tmp_path, dl_filename, preview_url = download_one_model(
                        pw_context, model_id, commodity_type, month_dir)
                except Exception as e:
                    self.log(f"  [{model_id}] 获取下载链接失败: {e}", "error")
                    update_download_status(DB_PATH, model_id, "failed", error_msg=str(e))
                    continue

                if dl_filename:
                    name_no_ext, ext = os.path.splitext(dl_filename)
                    if not ext:
                        ext = ".zip"
                else:
                    name_no_ext = model_id
                    ext = ".zip"

                model_name_part = sanitize_filename(name_no_ext)

                if is_img:
                    file_path = os.path.join(month_dir, f"{model_name_part}_{model_id}{ext}")
                    preview_path = None
                else:
                    file_path = os.path.join(month_dir, f"{model_name_part}_{model_id}{ext}")
                    preview_path = os.path.join(month_dir, f"{model_name_part}_{model_id}.jpg")

                delay = random_delay()
                self.log(f"  [{model_id}] 下载 {model_name_part}{ext} (等待 {delay:.1f}s)...")
                time.sleep(delay)

                try:
                    # 重命名 temp 文件到最终路径
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    os.replace(tmp_path, file_path)
                except Exception as e:
                    self.log(f"  [{model_id}] 保存文件失败: {e}", "error")
                    update_download_status(DB_PATH, model_id, "failed", error_msg=str(e))
                    continue

                if not is_img and preview_url:
                    try:
                        time.sleep(random.uniform(2, 5))
                        download_file(session, preview_url, preview_path, timeout=60)
                        self.log(f"  [{model_id}] 预览图已保存")
                    except Exception as e:
                        self.log(f"  [{model_id}] 预览图下载失败: {e}", "error")
                        preview_path = None

                update_download_status(DB_PATH, model_id, "done",
                                       file_path=file_path, preview_path=preview_path)
                self.log(f"  [{model_id}] 完成")
                downloaded_count += 1

                request_count += 1
                if request_count % KEEPALIVE_INTERVAL == 0:
                    try:
                        session.get(SITE_BASE, timeout=10)
                        self.log("  [keepalive] 刷新会话")
                    except Exception:
                        pass

            if self.stop_flag:
                save_checkpoint(DB_PATH, self.mode, highest_page_seen, total_pages)
                break

            highest_page_seen = page
            self.progress_callback(page, total_pages, downloaded_count)
            save_checkpoint(DB_PATH, self.mode, highest_page_seen, total_pages)
            page += 1

            if self.mode == "full" and total_pages and page > total_pages:
                self.log(f"已到达最后一页 (第 {total_pages} 页)，结束。")
                break

        pw_context.close()
        browser.close()
        playwright.stop()
        self.progress_callback(highest_page_seen, total_pages, downloaded_count)
        self.log(f"下载完成。共 {downloaded_count} 条，最后页码: {highest_page_seen}")


def run(mode, force=False, days=30):
    """CLI 入口，兼容原有命令行调用。"""
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d") if mode == "incremental" else None
    end_date = datetime.now().strftime("%Y-%m-%d")
    job = DownloadJob(
        mode=mode,
        start_date=start_date,
        end_date=end_date,
        skip_downloaded=not force,
    )
    job.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="知末下载记录归档工具")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental",
                        help="全量模式 (full) 或增量模式 (incremental)")
    parser.add_argument("--force", action="store_true",
                        help="强制重新下载，跳过已下载记录的去重检查")
    parser.add_argument("--days", type=int, default=30,
                        help="增量模式的天数范围 (默认 30)")
    args = parser.parse_args()
    run(args.mode, force=args.force, days=args.days)
