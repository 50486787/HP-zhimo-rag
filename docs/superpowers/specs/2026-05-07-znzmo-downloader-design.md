# 知末下载记录归档工具 - 设计文档

## 概述

定期将知末平台企业 VIP 账号的下载记录中的模型文件和预览图下载到本地，按月归档。

## 运行模式

- **全量模式**：首次运行，下载所有历史记录（约 1300 页 / 13000+ 条）
- **增量模式**：后续每月运行，下载最近 30 天的记录

## 架构

```
login.py          — Playwright 登录 + cookie 提取（仅登录时用）
api_discovery.py  — 抓包分析，发现下载记录/模型详情/下载链接 API
downloader.py     — 主下载脚本（全量 + 增量模式）
config.py         — 配置（路径、反爬参数、UA 池）
db.py             — SQLite 数据库操作（记录状态、断点、cookie）
```

### 数据流

```
[Playwright 登录] → 提取 cookie → 存入 SQLite (加密)
        ↓
[API 发现] → 抓包确认 /enterprise/xxx /model/xxx /download/xxx
        ↓
[主脚本]
  ├── 读取配置 → 判断模式（全量/增量）
  ├── 加载 cookie → 请求下载记录 API（分页）
  ├── 逐条过滤（SQLite 去重）
  ├── 获取模型下载链接 + 预览图 URL
  ├── 随机延迟后下载 → 存入 downloads/YYYY-MM/
  ├── 写入 SQLite 记录 → 更新断点
  └── 全量模式定期暂停休息
```

## 存储结构

```
downloads/
├── 2026-04/
│   ├── 现代简约客厅_1195169820.zip
│   ├── 现代简约客厅_1195169820.jpg
│   ├── 欧式别墅外观_1163647310.zip
│   ├── 欧式别墅外观_1163647310.jpg
│   └── ...
├── 2026-05/
│   └── ...
└── downloads.db          — SQLite 记录库
```

SQLite 表结构：

```sql
CREATE TABLE download_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT NOT NULL UNIQUE,
    model_name TEXT,
    account_id TEXT,
    download_time TEXT,
    cost TEXT,
    preview_url TEXT,
    file_path TEXT,          -- 本地 zip 路径
    preview_path TEXT,       -- 本地 jpg 路径
    status TEXT DEFAULT 'pending',  -- pending|done|failed|skipped
    error_msg TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT,               -- full|incremental
    current_page INTEGER DEFAULT 1,
    total_pages INTEGER,
    last_model_id TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE cookie_store (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cookies TEXT,            -- JSON string, AES encrypted
    updated_at TEXT DEFAULT (datetime('now'))
);
```

## 反爬策略

| 措施 | 参数 |
|---|---|
| 请求间隔（条） | 5-15 秒随机 |
| 翻页间隔 | 10-20 秒随机 |
| User-Agent 池 | 10 个真实浏览器 UA，每次随机 |
| Referer 链 | 模拟从列表页 → 详情页 → 下载的完整来源链 |
| 429 退避 | 等待 5 分钟 → 重试，连续 3 次停止等待人工确认 |
| 分段休息（全量） | 每运行 1 小时暂停 5-10 分钟 |
| 会话保活 | 每 50 条请求间访问一次首页刷新 cookie |
| 指数退避 | 429→5min, 503→10min, 其他→30s/60s/120s |

## 需要发现的 API

在实现前通过 Playwright + DevTools 抓包确认以下接口：

1. **下载记录列表** — 对应"账号下载记录" tab
2. **模型详情页/下载链接** — 如何从 model_id 获取 .zip 下载地址
3. **预览图** — 图片 URL 规律，是否可直接拼接

已知 API base：`https://api.znzmo.com`，已验证的关键接口：`/enterprise/accountInfo`、`/enterprise/addMember`、`/enterprise/deleteMember`、`/enterprise/giveGold`

## 安全考虑

- Cookie 明文存储在本地 SQLite，不提交到版本控制
- `config.py` 中包含隐私信息，加入 `.gitignore`
- 下载仅限已付费购买的内容（每月按前 30 天记录归档），不违反知末使用条款

## 技术栈

- Python 3.x
- requests — HTTP 请求
- playwright — 登录 + 抓包
- sqlite3 — 进度/状态/去重
- cryptography — cookie 加密存储
