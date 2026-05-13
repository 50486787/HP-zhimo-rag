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
    2: "tietu", 8: "ziliaoku", 20: "wenben", 30: "ps",
}

MODEL_PAGE_TEMPLATES = {
    "3d": "https://3d.znzmo.com/3dmoxing/{skuid}.html",
    "su": "https://su.znzmo.com/sumoxing/{skuid}.html",
    "sgt": "https://sgt.znzmo.com/sgt/{skuid}.html",
    "tietu": "https://tietu.znzmo.com/tietu/{skuid}.html",
    "ziliaoku": "https://www.znzmo.com/ziliaoku/{skuid}.html",
    "wenben": "https://wenben.znzmo.com/wenben/{skuid}.html",
    "ps": "https://ps.znzmo.com/pschannel/{skuid}.html",
}

IMAGE_COMMODITY_TYPES = {2}


def sanitize_filename(name):
    """去除文件名中的非法字符和首尾空白。"""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def retry_io(func, max_retries=3, base_delay=3):
    """IO 操作重试，应对 NAS 磁盘满/连接中断等瞬态错误。"""
    for attempt in range(max_retries):
        try:
            return func()
        except (OSError, IOError) as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            time.sleep(delay)


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
        "Origin": SITE_BASE,
    })
    if cookies_json:
        cookies = json.loads(cookies_json)
        for c in cookies:
            domain = c.get("domain", "")
            # 只保留 znzmo 相关域的 cookie，避免无关 cookie 干扰
            if not (domain.endswith("znzmo.com") or domain.endswith("znzmo.cn")):
                continue
            session.cookies.set(
                c["name"], c["value"],
                domain=domain,
                path=c.get("path", "/"),
                secure=c.get("secure", False),
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


def _save_url_to_path(url, referer, dest_path):
    """直接下载 URL 到路径，每次调用重新请求（方便重试）。"""
    resp = requests.get(url, headers={
        "User-Agent": random_ua(),
        "Referer": referer,
    }, stream=True, timeout=300)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)


def _extract_full_image_url(page):
    """从页面提取全分辨率图片 URL（贴图等图片类型兜底）。"""
    url = page.evaluate("""() => {
        try {
            const nd = document.getElementById('__NEXT_DATA__');
            if (nd) {
                const data = JSON.parse(nd.textContent);
                const pp = data?.props?.pageProps || {};
                const arr = pp?.imgArr || pp?.largeImgArr || pp?.detailImageArr || [];
                if (arr.length > 0) return arr[0];
                const main = pp?.activeSkuInfo?.mainImageUrl;
                if (main) return main;
            }
        } catch(e) {}
        const imgs = document.querySelectorAll('img');
        for (const img of imgs) {
            if (img.naturalWidth > 500 || img.width > 500) {
                const s = img.src || '';
                if (s && !s.startsWith('data:')) return s;
            }
        }
        return null;
    }""")
    if url:
        url = url.split("?x-oss-process")[0]
    return url


def _extract_download_url_from_page(page):
    """从页面 __NEXT_DATA__ 或 DOM 链接中提取下载 URL（兜底）。"""
    url = page.evaluate("""() => {
        try {
            const nd = document.getElementById('__NEXT_DATA__');
            if (nd) {
                const data = JSON.parse(nd.textContent);
                const pp = data?.props?.pageProps || {};
                const u = pp?.downloadUrl || pp?.fileUrl ||
                          pp?.activeSkuInfo?.downloadUrl ||
                          pp?.activeSkuInfo?.fileUrl ||
                          pp?.activeSkuInfo?.panoramaUrl;
                if (u) return u;
            }
        } catch(e) {}
        return null;
    }""")
    if not url:
        url = page.evaluate("""() => {
            const links = document.querySelectorAll('a[href]');
            for (const a of links) {
                const h = a.href || '';
                if (/\\.(zip|rar|7z|skp|max|dwg|fbx|obj|stl|glb|gltf|usd|usdz|blend|ma|mb|c4d|3ds|dxf|igs|stp|step|pdf|doc|ppt|psd|ai|cdr)$/i.test(h))
                    return h;
            }
            return null;
        }""")
    return url


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

    # 下载按钮文本（具体→通用），主页和弹窗共用
    _DOWNLOAD_BTN_TEXTS = [
        "立即下载", "企业免费下载", "VIP免费下载",
        "免费下载贴图", "免费下载素材", "免费下载",
        "下载贴图", "下载素材", "下载图片", "下载模型",
        "下载CAD", "CAD下载", "下载图纸", "免费下载CAD",
        "下载SU模型", "下载SketchUp", "下载SU",
        "同意并下载", "下载协议", "确定", "确认下载", "确认",
        "同意", "Agree", "OK", "Confirm",
        "下载",  # 最通用，放最后
    ]

    # 点击下载按钮
    clicked = False

    # 第一轮：精确文本匹配
    for text in _DOWNLOAD_BTN_TEXTS:
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

    # 第三轮：CSS 选择器（含 SU 等子站专属类名）
    if not clicked:
        for sel in [
            # 通用
            "[class*=vipFreeDownload]", "[class*=downloadBtn]",
            "[class*=vipFree]", "[class*=freeDownload]",
            "[class*=downloadWrap]", "[class*=btnBox]",
            "a[class*=download]", "button[class*=download]",
            ".download-btn", "#download-btn",
            # SU / CAD 等子站可能使用不同命名
            "[class*=Download]", "[class*=downloadBtnBox]",
            "[class*=toolBar]", "[class*=toolbar]",
            "[class*=actionBar]", "[class*=action]",
            "[class*=operation]", "[class*=operate]",
            # 资料库 / 文本等页面
            "[class*=fileDownload]", "[class*=file-download]",
            "a[href*=download]", "[class*=downLoad]",
        ]:
            try:
                el = page.locator(sel).first
                if el.count() > 0:
                    el.click(timeout=3000)
                    clicked = True
                    break
            except Exception:
                continue

    # 第四轮：遍历页面上所有按钮/链接，找包含"下载"文字的那个
    if not clicked:
        buttons = page.locator(
            "button, a, [role=button], span[onclick], div[onclick]").all()
        for btn in buttons:
            try:
                text = btn.inner_text()
                if text and ("下载" in text or "download" in text.lower()):
                    btn.click(timeout=3000)
                    clicked = True
                    break
            except Exception:
                continue

    # 第五轮：SU/CAD 等子站可能有 iframe 承载下载按钮
    if not clicked:
        frames = page.frames
        for frame in frames[1:]:  # 跳过主 frame
            try:
                for text in _DOWNLOAD_BTN_TEXTS:
                    el = frame.locator(f":text-is('{text}')").first
                    if el.count() > 0:
                        el.click(timeout=3000)
                        clicked = True
                        break
                if clicked:
                    break
                for btn in frame.locator("button, a, [role=button]").all():
                    try:
                        t = btn.inner_text()
                        if t and ("下载" in t or "download" in t.lower()):
                            btn.click(timeout=3000)
                            clicked = True
                            break
                    except Exception:
                        continue
                if clicked:
                    break
            except Exception:
                continue

    # 图片类型（贴图等）兜底：没找到按钮时直接提取原图 URL 下载
    if not clicked and is_image_type(commodity_type):
        image_url = _extract_full_image_url(page)
        if image_url:
            page.close()
            ext = os.path.splitext(image_url.split("?")[0])[1] or ".jpg"
            tmp_path = os.path.join(save_dir, f".tmp_dl_{model_id}")
            _save_url_to_path(image_url, page_url, tmp_path)
            return tmp_path, f"{model_id}{ext}", None
        page.close()
        raise Exception(
            f"贴图类未找到下载按钮/图片 (type={commodity_type}, url={page_url})")

    if not clicked:
        page.close()
        raise Exception(
            f"未找到下载按钮 (type={commodity_type}/{type_name}, url={page_url})")

    # ── 下载等待 + 弹窗主动检测 ──
    # 点完主页按钮后主动检测是否出现弹窗（modal/dialog），
    # 而不是被动等超时后再猜。

    def _wait_download(timeout_sec=12):
        for _ in range(int(timeout_sec * 2)):
            if download_obj:
                return True
            page.wait_for_timeout(500)
        return False

    def _detect_popup():
        """检测页面上是否出现了可见的弹窗/模态框。"""
        popup_selectors = [
            "[role=dialog]", "[role=alertdialog]",
            ".ant-modal-wrap", ".ant-modal-content", ".ant-modal",
            ".el-dialog__wrapper", ".el-dialog", ".el-message-box",
            ".modal", ".dialog", ".popup",
            "[class*=modal]", "[class*=dialog]", "[class*=popup]",
            "[class*=Modal]", "[class*=Dialog]",
        ]
        for sel in popup_selectors:
            try:
                el = page.locator(sel).last
                if el.count() > 0 and el.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _click_popup_button():
        """点击弹窗/页面中最新出现的下载按钮（.last 获取弹窗内的按钮）。"""
        for text in _DOWNLOAD_BTN_TEXTS:
            try:
                el = page.locator(f":text-is('{text}')").last
                if el.count() > 0 and el.is_visible():
                    el.click(timeout=3000)
                    return True
            except Exception:
                continue
        return False

    def _direct_download_fallback():
        """兜底：从页面 JS/DOM 提取下载链接直接下载，或贴图提取原图。"""
        download_url = _extract_download_url_from_page(page)
        if download_url:
            page.close()
            tmp_path = os.path.join(save_dir, f".tmp_dl_{model_id}")
            _dl = lambda: _save_url_to_path(download_url, page_url, tmp_path)
            retry_io(_dl, max_retries=2, base_delay=5)
            return tmp_path, os.path.basename(download_url).split("?")[0], preview_url

        if is_image_type(commodity_type):
            image_url = _extract_full_image_url(page)
            if image_url:
                page.close()
                ext = os.path.splitext(image_url.split("?")[0])[1] or ".jpg"
                tmp_path = os.path.join(save_dir, f".tmp_dl_{model_id}")
                _save_url_to_path(image_url, page_url, tmp_path)
                return tmp_path, f"{model_id}{ext}", None

        page.close()
        raise Exception("未捕获到下载事件")

    # 先等页面响应点击（弹窗动画通常在 1-2 秒内完成）
    page.wait_for_timeout(2500)

    if download_obj:
        # 单层弹窗：第一个按钮直接触发了下载
        pass
    elif _detect_popup():
        # 检测到弹窗 → 点弹窗内的下载按钮
        _click_popup_button()
        if not _wait_download(10):
            page.wait_for_timeout(1000)
            _click_popup_button()
            if not _wait_download(12):
                return _direct_download_fallback()
    else:
        # 没有弹窗也没有下载事件 → 可能是网络慢或单层异步触发
        if not _wait_download(8):
            if _detect_popup():
                _click_popup_button()
            if not _wait_download(15):
                return _direct_download_fallback()

    # 用 Playwright 保存下载文件（重试应对 NAS 瞬态错误）
    tmp_path = os.path.join(save_dir, f".tmp_dl_{model_id}")
    try:
        retry_io(lambda: download_obj.save_as(tmp_path), max_retries=3, base_delay=5)
    finally:
        page.close()
    return tmp_path, download_filename, preview_url


def download_file(session, url, dest_path, timeout=300):
    """下载文件到指定路径，带 IO 重试。"""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    resp = session.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    tmp_path = dest_path + ".tmp"

    def _write():
        nonlocal downloaded
        downloaded = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)

    retry_io(_write, max_retries=2, base_delay=3)
    if total > 0 and downloaded < total:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False
    retry_io(lambda: os.replace(tmp_path, dest_path), max_retries=3, base_delay=3)
    return True


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

    def _cleanup_playwright(self):
        """安全释放 Playwright 资源，异常安全。"""
        for obj in (self._pw_context, self._browser, self._playwright):
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        self._pw_context = self._browser = self._playwright = None

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

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=False, args=[
            "--window-position=-32000,-32000",
        ])
        self._pw_context = self._browser.new_context()
        self._pw_context.add_cookies(cookies)
        pw_context = self._pw_context

        try:
            self.log("获取企业信息...")
            ent_info = get_enterprise_info(session)
            enterprise_id = ent_info["enterprise_id"]
            nick_name = ent_info.get("nick_name", "")
            self.log(f"账号: {nick_name} (ID: {enterprise_id})")
            member_level = ent_info["member_level"]
        except Exception as e:
            self.log(f"获取企业信息失败: {e}", "error")
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
                        file_path = existing.get("file_path", "")
                        if file_path and os.path.exists(file_path):
                            self.log(f"  [{model_id}] 已下载，跳过")
                            continue
                        else:
                            self.log(f"  [{model_id}] 文件已删除，重新下载")

                insert_download_record(DB_PATH, record)

                month = record["month"] or self.end_date[:7]
                type_name = commodity_type_name(commodity_type)
                if type_name == "su":
                    month_dir = os.path.join(self.download_dir, month)
                else:
                    month_dir = os.path.join(self.download_dir, month, type_name)
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
                    # 服务端返回的通用文件名，改用 model_id
                    if name_no_ext.lower() in ("znzmo", "download", "file", "model", "untitled"):
                        name_no_ext = model_id
                else:
                    name_no_ext = model_id
                    ext = ".zip"

                model_name_part = sanitize_filename(name_no_ext)

                # 服务端文件名通常已含 model_id（如 xxxID_1192564496），避免重复追加
                if model_id in model_name_part:
                    stem = model_name_part
                else:
                    stem = f"{model_name_part}_{model_id}"
                file_path = os.path.join(month_dir, f"{stem}{ext}")
                if is_img:
                    preview_path = None
                else:
                    preview_path = os.path.splitext(file_path)[0] + ".jpg"

                delay = random_delay()
                self.log(f"  [{model_id}] 下载 {model_name_part}{ext} (等待 {delay:.1f}s)...")
                time.sleep(delay)

                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    retry_io(lambda: os.replace(tmp_path, file_path), max_retries=3, base_delay=5)
                except Exception as e:
                    self.log(f"  [{model_id}] 保存文件失败: {e}", "error")
                    update_download_status(DB_PATH, model_id, "failed", error_msg=str(e))
                    # 清理临时文件
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
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

        self._cleanup_playwright()
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
