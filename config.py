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
