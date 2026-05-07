# 知末下载记录归档工具 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 构建一个 Python 工具，从知末平台企业 VIP 账号中批量下载历史下载记录的模型文件和预览图，按月归档到本地。

**架构：** 5 个独立模块——config（配置/反爬参数）、db（SQLite 进度/去重/cookie 存储）、login（Playwright 手动登录提取 cookie）、api_discovery（抓包确认 API 端点）、downloader（主脚本，支持全量和增量两种模式）。

**技术栈：** Python 3.x, requests, playwright, sqlite3, cryptography

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `config.py` | 集中管理路径、API base、反爬参数、UA 池 |
| `db.py` | SQLite 建表、CRUD、断点/cookie 读写 |
| `login.py` | Playwright 打开浏览器 → 用户手动登录 → 提取 cookie → 写入 db |
| `api_discovery.py` | Playwright 监听网络请求 → 记录匹配的 API 端点 → 保存到 config |
| `downloader.py` | 主入口：模式判断 → 获取记录 → 逐条下载 → 归档 → 更新进度 |
| `.gitignore` | 排除 downloads/、*.db、config_local.py |
| `tests/test_db.py` | db.py 的单元测试 |
| `tests/test_downloader.py` | downloader 解析/过滤逻辑的单元测试 |

---

### 任务 1：项目骨架搭建

**文件：**
- 创建：`requirements.txt`
- 创建：`.gitignore`
- 创建：`config.py`

- [ ] **步骤 1：创建 requirements.txt**

```txt
requests>=2.28.0
playwright>=1.40.0
cryptography>=41.0.0
```

- [ ] **步骤 2：安装依赖**

运行：`pip install -r requirements.txt`
运行：`playwright install chromium`

- [ ] **步骤 3：创建 .gitignore**

```gitignore
downloads/
*.db
config_local.py
__pycache__/
*.pyc
.env
```

- [ ] **步骤 4：创建 config.py**

```python
import os
import random

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
DB_PATH = os.path.join(BASE_DIR, "downloads.db")

API_BASE = "https://api.znzmo.com"
SITE_BASE = "https://www.znzmo.com"
PRIVILEGE_PAGE = f"{SITE_BASE}/personalCenter/usercenter_privilege.html"

DELAY_MIN = 5
DELAY_MAX = 15
PAGE_DELAY_MIN = 10
PAGE_DELAY_MAX = 20

FULL_MODE_WORK_SECONDS = 3600
FULL_MODE_REST_MIN = 300
FULL_MODE_REST_MAX = 600

MAX_CONSECUTIVE_429 = 3
RATE_LIMIT_BACKOFF = 300

KEEPALIVE_INTERVAL = 50

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def random_delay():
    return random.uniform(DELAY_MIN, DELAY_MAX)


def random_page_delay():
    return random.uniform(PAGE_DELAY_MIN, PAGE_DELAY_MAX)


def random_rest_duration():
    return random.uniform(FULL_MODE_REST_MIN, FULL_MODE_REST_MAX)


def random_ua():
    return random.choice(UA_POOL)
```

- [ ] **步骤 5：Commit**

```bash
git add requirements.txt .gitignore config.py
git commit -m "chore: add project skeleton, config and dependencies"
```

---

### 任务 2：数据库层

**文件：**
- 创建：`db.py`
- 创建：`tests/test_db.py`

- [ ] **步骤 1：编写 db.py 测试 —— 建表和插入**

```python
import os
import pytest
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db, insert_download_record, get_download_record, save_checkpoint, get_checkpoint


@pytest.fixture
def test_db():
    db_path = os.path.join(os.path.dirname(__file__), "test.db")
    init_db(db_path)
    yield db_path
    os.remove(db_path)


class TestInitDB:
    def test_creates_all_tables(self, test_db):
        conn = sqlite3.connect(test_db)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "download_records" in table_names
        assert "checkpoint" in table_names
        assert "cookie_store" in table_names


class TestDownloadRecords:
    def test_insert_and_get(self, test_db):
        insert_download_record(test_db, {
            "model_id": "1195169820",
            "model_name": "现代简约客厅",
            "account_id": "21810008241125",
            "download_time": "2026-04-28 10:58:39",
            "cost": "13知币",
        })
        record = get_download_record(test_db, "1195169820")
        assert record["model_name"] == "现代简约客厅"
        assert record["status"] == "pending"

    def test_insert_duplicate_ignored(self, test_db):
        data = {"model_id": "1195169820", "model_name": "test"}
        insert_download_record(test_db, data)
        insert_download_record(test_db, data)
        record = get_download_record(test_db, "1195169820")
        assert record is not None

    def test_update_status(self, test_db):
        from db import update_download_status
        insert_download_record(test_db, {
            "model_id": "1195169820",
            "model_name": "test",
            "account_id": "1",
            "download_time": "2026-04-28",
            "cost": "1知币",
        })
        update_download_status(test_db, "1195169820", "done",
            file_path="/tmp/test.zip", preview_path="/tmp/test.jpg")
        record = get_download_record(test_db, "1195169820")
        assert record["status"] == "done"
        assert record["file_path"] == "/tmp/test.zip"


class TestCheckpoint:
    def test_save_and_get(self, test_db):
        save_checkpoint(test_db, mode="full", current_page=42, total_pages=1313)
        cp = get_checkpoint(test_db)
        assert cp["mode"] == "full"
        assert cp["current_page"] == 42
        assert cp["total_pages"] == 1313
```

- [ ] **步骤 2：运行测试确认失败**

```bash
python -m pytest tests/test_db.py -v
```
预期：ImportError，因为 db.py 还不存在

- [ ] **步骤 3：实现 db.py**

```python
import sqlite3
import json


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS download_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id TEXT NOT NULL UNIQUE,
            model_name TEXT,
            account_id TEXT,
            download_time TEXT,
            cost TEXT,
            preview_url TEXT,
            file_path TEXT,
            preview_path TEXT,
            file_type TEXT DEFAULT 'model',
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS checkpoint (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            mode TEXT,
            current_page INTEGER DEFAULT 1,
            total_pages INTEGER,
            last_model_id TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS cookie_store (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cookies TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


def insert_download_record(db_path, data):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR IGNORE INTO download_records
            (model_id, model_name, account_id, download_time, cost)
        VALUES (?, ?, ?, ?, ?)
    """, (data["model_id"], data.get("model_name"), data.get("account_id"),
          data.get("download_time"), data.get("cost")))
    conn.commit()
    conn.close()


def get_download_record(db_path, model_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM download_records WHERE model_id = ?", (model_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_download_status(db_path, model_id, status, file_path=None, preview_path=None, error_msg=None):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        UPDATE download_records
        SET status = ?, file_path = ?, preview_path = ?, error_msg = ?
        WHERE model_id = ?
    """, (status, file_path, preview_path, error_msg, model_id))
    conn.commit()
    conn.close()


def save_checkpoint(db_path, mode, current_page, total_pages, last_model_id=None):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO checkpoint (id, mode, current_page, total_pages, last_model_id, updated_at)
        VALUES (1, ?, ?, ?, ?, datetime('now'))
    """, (mode, current_page, total_pages, last_model_id))
    conn.commit()
    conn.close()


def get_checkpoint(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM checkpoint WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else None


def save_cookies(db_path, cookies_json):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO cookie_store (id, cookies, updated_at)
        VALUES (1, ?, datetime('now'))
    """, (cookies_json,))
    conn.commit()
    conn.close()


def get_cookies(db_path):
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT cookies FROM cookie_store WHERE id = 1").fetchone()
    conn.close()
    return row[0] if row else None
```

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest tests/test_db.py -v
```
预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: add database layer with SQLite for progress and cookie storage"
```

---

### 任务 3：登录模块

**文件：**
- 创建：`login.py`

- [ ] **步骤 1：实现 login.py**

```python
"""知末登录模块 —— Playwright 手动登录 + cookie 提取"""
import json
import sys
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
```

- [ ] **步骤 2：手动验证登录**

```bash
python login.py
```
手动操作：在打开的浏览器中登录 → 按 Enter → 确认输出 "已保存 N 条 cookie"

- [ ] **步骤 3：Commit**

```bash
git add login.py
git commit -m "feat: add Playwright login module with cookie extraction"
```

---

### 任务 4：API 发现模块

**文件：**
- 创建：`api_discovery.py`

- [ ] **步骤 1：实现 api_discovery.py**

```python
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
            if "api.znzmo.com" in url:
                captured.append({"method": request.method, "url": url})

        page.on("request", log_request)

        print("正在打开下载记录页面...")
        page.goto(PRIVILEGE_PAGE, wait_until="networkidle")

        print("请手动操作：点击'账号下载记录'tab，翻几页，点击模型ID看详情...")
        print("操作完成后按 Enter 继续...")
        input()

        print("\n=== 捕获到的 API 请求 ===")
        seen = set()
        for req in captured:
            if req["url"] not in seen:
                seen.add(req["url"])
                print(f"  {req['method']} {req['url']}")

        browser.close()
        print("\n请将上面可疑的 API 端点记下来，用于配置 downloader.py")


if __name__ == "__main__":
    discover_apis()
```

- [ ] **步骤 2：手动验证抓包**

```bash
python api_discovery.py
```
手动操作：在打开的浏览器中点击"账号下载记录"tab，翻几页，点击几个模型 ID 看详情页面，然后按 Enter。确认输出中有 API 端点列表。

- [ ] **步骤 3：Commit**

```bash
git add api_discovery.py
git commit -m "feat: add API discovery module for network request capture"
```

---

### 任务 5：下载器核心模块

**文件：**
- 创建：`downloader.py`
- 创建：`tests/test_downloader.py`

**注意：** 此任务的步骤 1-3 在 API 发现完成后执行，届时将替换 `RECORDS_API` 和 `MODEL_DETAIL_API` 为真实端点。

- [ ] **步骤 1：编写解析逻辑的测试**

```python
import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from downloader import parse_record, is_image_type, sanitize_filename, get_month_from_time


class TestSanitizeFilename:
    def test_removes_invalid_chars(self):
        assert sanitize_filename("现代简约客厅*?") == "现代简约客厅"

    def test_strips_whitespace(self):
        assert sanitize_filename("  现代简约客厅  ") == "现代简约客厅"


class TestIsImageType:
    def test_image_extensions(self):
        assert is_image_type(".jpg") == True
        assert is_image_type(".png") == True
        assert is_image_type(".psd") == True
        assert is_image_type(".tif") == True

    def test_model_extensions(self):
        assert is_image_type(".zip") == False
        assert is_image_type(".skp") == False
        assert is_image_type(".max") == False
        assert is_image_type(".fbx") == False


class TestGetMonthFromTime:
    def test_extracts_month(self):
        assert get_month_from_time("2026-04-28 10:58:39") == "2026-04"
        assert get_month_from_time("2026-05-01 00:00:00") == "2026-05"


class TestParseRecord:
    def test_parse_valid_record(self):
        html_record = {
            "account_id": "19004605301512",
            "account_name": "caojuntao",
            "download_time": "2026-04-28 10:58:39",
            "cost": "13知币",
            "model_id": "1195169820",
            "model_name": "现代简约客厅",
        }
        result = parse_record(html_record)
        assert result["model_id"] == "1195169820"
        assert result["model_name"] == "现代简约客厅"
        assert result["month"] == "2026-04"
```

- [ ] **步骤 2：运行测试确认失败**

```bash
python -m pytest tests/test_downloader.py -v
```
预期：ImportError 或 FAIL

- [ ] **步骤 3：实现 downloader.py**

```python
"""知末下载器 —— 主脚本，全量/增量模式下载模型和预览图"""
import os
import re
import sys
import time
import json
import random
import argparse
import requests
from datetime import datetime, timedelta

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

# 由 api_discovery 确认后填入
RECORDS_API = f"{API_BASE}/enterprise/downloadList"
MODEL_DETAIL_API = f"{API_BASE}/model/detail"


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".psd", ".tif", ".tiff", ".svg"}


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def is_image_type(ext):
    return ext.lower() in IMAGE_EXTENSIONS


def get_month_from_time(dt_str):
    return dt_str[:7]


def parse_record(item):
    """将从 API 返回的下载记录解析为统一格式。"""
    return {
        "model_id": item.get("model_id", ""),
        "model_name": sanitize_filename(item.get("model_name", item.get("model_id", "unknown"))),
        "account_id": item.get("account_id", ""),
        "download_time": item.get("download_time", ""),
        "cost": item.get("cost", ""),
        "month": get_month_from_time(item.get("download_time", "")),
    }


def build_session(cookies_json):
    """从保存的 cookie 构建 requests.Session。"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    })
    if cookies_json:
        cookies = json.loads(cookies_json)
        for c in cookies:
            session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    return session


def fetch_records_page(session, page_num):
    """获取指定页的下载记录。"""
    params = {"page": page_num, "pageSize": 10}
    resp = session.get(RECORDS_API, params=params, headers={"Referer": SITE_BASE + "/"})
    resp.raise_for_status()
    data = resp.json()
    if data.get("ret") != 0:
        raise Exception(f"API error: {data}")
    return data.get("data", {})


def get_download_url(session, model_id):
    """根据模型 ID 获取下载链接和预览图 URL。"""
    resp = session.get(
        f"{MODEL_DETAIL_API}?modelId={model_id}",
        headers={"Referer": f"{SITE_BASE}/model/{model_id}.html"},
    )
    resp.raise_for_status()
    data = resp.json()
    detail = data.get("data", {})
    return {
        "download_url": detail.get("download_url", ""),
        "preview_url": detail.get("preview_url", detail.get("cover_url", "")),
        "file_ext": detail.get("file_ext", ".zip"),
        "model_name": detail.get("model_name", ""),
    }


def download_file(session, url, dest_path):
    """下载文件到指定路径，返回是否成功。"""
    resp = session.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
    if total > 0 and downloaded < total:
        os.remove(dest_path)
        return False
    return True


def run(mode, target_month=None):
    """主入口：全量或增量模式运行下载。"""
    init_db(DB_PATH)
    cookies_json = get_cookies(DB_PATH)
    if not cookies_json:
        print("未找到 cookie，请先运行: python login.py")
        return

    session = build_session(cookies_json)
    consecutive_429 = 0
    request_count = 0
    work_start = time.time()

    if mode == "full":
        cp = get_checkpoint(DB_PATH)
        start_page = cp["current_page"] if cp else 1
        total_pages = cp["total_pages"] if cp else None
        print(f"全量模式：从第 {start_page} 页开始")
    else:
        start_page = 1
        total_pages = None
        print(f"增量模式：下载最近 30 天记录")

    page = start_page
    while True:
        # 全量模式分段休息
        if mode == "full" and time.time() - work_start > FULL_MODE_WORK_SECONDS:
            rest = random_rest_duration()
            print(f"已运行 1 小时，休息 {rest:.0f} 秒...")
            time.sleep(rest)
            work_start = time.time()

        # 翻页间隔
        if page > start_page:
            delay = random_page_delay()
            print(f"翻页间隔 {delay:.1f}s...")
            time.sleep(delay)

        # 请求记录
        resp = session.get(
            RECORDS_API,
            params={"page": page, "pageSize": 10},
            headers={
                "Referer": f"{SITE_BASE}/personalCenter/usercenter_privilege.html",
                "User-Agent": random_ua(),
            },
        )
        if resp.status_code == 429:
            consecutive_429 += 1
            if consecutive_429 >= MAX_CONSECUTIVE_429:
                print(f"连续 {MAX_CONSECUTIVE_429} 次 429，请手动检查。")
                save_checkpoint(DB_PATH, mode, page, total_pages)
                return
            print(f"429 限流，等待 {RATE_LIMIT_BACKOFF}s...")
            time.sleep(RATE_LIMIT_BACKOFF)
            continue
        consecutive_429 = 0
        resp.raise_for_status()
        data = resp.json()

        records = data.get("data", {}).get("list", [])
        total_pages = data.get("data", {}).get("totalPages", total_pages)

        if not records:
            print(f"第 {page} 页无记录，结束。")
            break

        print(f"第 {page}/{total_pages} 页，共 {len(records)} 条记录")

        # 增量模式：检查时间范围
        if mode == "incremental":
            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            records = [r for r in records if r.get("download_time", "") >= cutoff]
            if len(records) < 10:
                print("已到达 30 天边界，结束。")
                break

        for item in records:
            record = parse_record(item)
            model_id = record["model_id"]

            # 去重检查
            existing = get_download_record(DB_PATH, model_id)
            if existing and existing["status"] == "done":
                print(f"  [{model_id}] 已下载，跳过")
                continue

            # 存入待下载记录
            insert_download_record(DB_PATH, record)

            # 获取下载链接
            try:
                detail = get_download_url(session, model_id)
            except Exception as e:
                print(f"  [{model_id}] 获取详情失败: {e}")
                update_download_status(DB_PATH, model_id, "failed", error_msg=str(e))
                continue

            model_name = sanitize_filename(detail.get("model_name") or record["model_name"])
            month_dir = os.path.join(DOWNLOAD_DIR, record["month"])
            os.makedirs(month_dir, exist_ok=True)

            file_ext = detail.get("file_ext", ".zip").lower()
            is_img = is_image_type(file_ext)

            # 下载文件
            download_url = detail.get("download_url") or detail.get("preview_url")
            if not download_url:
                print(f"  [{model_id}] 无下载链接")
                update_download_status(DB_PATH, model_id, "failed", error_msg="no download url")
                continue

            # 贴图类型：扩展名用实际文件的
            if is_img:
                file_path = os.path.join(month_dir, f"{model_name}_{model_id}{file_ext}")
                preview_path = None
            else:
                file_path = os.path.join(month_dir, f"{model_name}_{model_id}.zip")
                preview_path = os.path.join(month_dir, f"{model_name}_{model_id}.jpg")

            delay = random_delay()
            print(f"  [{model_id}] 下载 {model_name} (等待 {delay:.1f}s)...")
            time.sleep(delay)

            try:
                download_file(session, download_url, file_path)
                # 非贴图类型：下载预览图
                if not is_img and detail.get("preview_url"):
                    time.sleep(random.uniform(2, 5))
                    download_file(session, detail["preview_url"], preview_path)
                update_download_status(DB_PATH, model_id, "done",
                    file_path=file_path, preview_path=preview_path)
                print(f"  [{model_id}] 完成")
            except Exception as e:
                print(f"  [{model_id}] 下载失败: {e}")
                update_download_status(DB_PATH, model_id, "failed", error_msg=str(e))
                continue

            request_count += 1
            if request_count % KEEPALIVE_INTERVAL == 0:
                try:
                    session.get(SITE_BASE, timeout=10)
                except Exception:
                    pass

        save_checkpoint(DB_PATH, mode, page, total_pages)
        page += 1

    print("下载完成。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="知末下载记录归档工具")
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental",
                        help="全量模式或增量模式")
    parser.add_argument("--month", help="指定月份 (YYYY-MM)，仅增量模式有效")
    args = parser.parse_args()
    run(args.mode, args.month)
```

- [ ] **步骤 4：运行解析逻辑测试**

```bash
python -m pytest tests/test_downloader.py -v
```
预期：parse_record、sanitize_filename、is_image_type、get_month_from_time 相关测试 PASS

- [ ] **步骤 5：Commit**

```bash
git add downloader.py tests/test_downloader.py
git commit -m "feat: add downloader core with full/incremental modes and anti-crawl measures"
```

---

### 任务 6：API 发现后配置修正

在任务 4（API 发现）运行之后执行此任务，将发现的真实 API 端点填入 downloader.py。

- [ ] **步骤 1：更新 downloader.py 中的 API 端点**

根据 `api_discovery.py` 的运行结果，修改 `downloader.py` 中的：
- `RECORDS_API` — 下载记录列表接口
- `MODEL_DETAIL_API` — 模型详情/下载链接接口

- [ ] **步骤 2：调整 fetch_records_page 和 get_download_url**

根据真实 API 返回的数据结构调整解析逻辑（字段名映射）。

- [ ] **步骤 3：Commit**

```bash
git add downloader.py
git commit -m "fix: update API endpoints and parsing logic based on discovery"
```

---

### 任务 7：端到端验证

- [ ] **步骤 1：增量模式试运行**

```bash
python downloader.py --mode incremental
```
预期：获取最近 30 天记录，逐条下载，存入 `downloads/YYYY-MM/`，无 429 错误。

- [ ] **步骤 2：检查下载结果**

```bash
dir downloads\2026-04\
```
预期：看到 `.zip` 和 `.jpg` 文件成对出现，文件名包含模型名称和 ID。

- [ ] **步骤 3：验证断点续传**

运行增量模式 → 中途 Ctrl+C 中断 → 再次运行 → 确认自动跳过已下载的记录。

- [ ] **步骤 4：Commit**

```bash
git add -A
git commit -m "test: verify end-to-end download flow"
```

---

## 使用说明

```bash
# 首次使用：登录
python login.py

# 发现 API（首次需要）
python api_discovery.py

# 全量下载（首次归档，可中断续传）
python downloader.py --mode full

# 增量下载（每月运行）
python downloader.py --mode incremental

# 增量下载指定月份
python downloader.py --mode incremental --month 2026-05
```
