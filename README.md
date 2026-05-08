# 知末企业VIP下载器

自动下载知末（znzmo.com）企业VIP账号下的模型、贴图、PS素材等资源。支持 **GUI 图形界面** 和 **命令行** 两种模式。

## 设计思路

知末企业VIP每天会产生大量下载记录，逐条手动下载耗时巨大。本工具的思路：

1. **模拟浏览器行为** — 通过 Playwright 操作真实浏览器（Chromium），触发页面下载按钮，让服务端生成真实的下载文件。这比直接拼 URL 更可靠，因为服务端有鉴权和过期机制。
2. **登录态持久化** — 用户只需登录一次，Cookie 保存到 SQLite，后续自动复用，避免反复扫码。
3. **断点续传** — 每次下载都会记录到数据库，下次运行时自动跳过已下载的模型（且验证文件是否真实存在）。
4. **反爬保护** — 随机 UA 池、请求间隔、429 退避策略，降低被封风险。

## 架构

```
                    ┌────────────┐
                    │   gui.py   │  tkinter 图形界面
                    └─────┬──────┘
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
      ┌──────────┐ ┌───────────┐ ┌──────────┐
      │ login.py │ │downloader.py│ │ config.py│
      │Playwright│ │  DownloadJob │ │ 配置常量  │
      └────┬─────┘ └─────┬─────┘ └──────────┘
           │             │
           ▼             ▼
      ┌─────────────────────────┐
      │         db.py           │  SQLite 存储
      │  cookie / 记录 / 断点   │
      └─────────────────────────┘
```

- **login.py** — Playwright 打开浏览器，用户手动登录，提取 Cookie 存入 SQLite
- **downloader.py** — 核心下载逻辑：分页拉取 API 下载记录，逐条用 Playwright 点击下载按钮获取文件
- **db.py** — 三张表：`cookie_store`（登录态）、`download_records`（下载记录）、`checkpoint`（断点续传）
- **gui.py** — tkinter 界面：账号、日期范围、实时日志、下载历史
- **config.py** — API 地址、延时范围、UA 池等配置

## 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip

## 安装

```bash
# 克隆项目
git clone <repo-url>
cd zhi_mo_downloader

# uv 一键安装（自动创建虚拟环境 + 安装依赖）
uv sync

# 安装 Playwright 浏览器（仅需一次，约 300MB）
uv run playwright install chromium
```

如果不用 uv，也可以用 pip：

```bash
pip install -r requirements.txt
playwright install chromium
```

## 使用说明

### 1. 登录（仅需一次）

**GUI 方式：**

```bash
uv run python gui.py
```

点击"重新登录"按钮，在弹出的 Chromium 浏览器中扫码或输入账号密码完成登录。登录成功后 Cookie 自动保存到 `downloads.db`。

**命令行方式：**

```bash
uv run python login.py
```

按提示在浏览器中登录，完成后按 Enter。

### 2. 下载

**GUI 方式（推荐）：**

1. 启动 `uv run python gui.py`
2. 确认账号状态显示"已登录"，下方显示你的昵称和企业ID
3. 选择下载目录（默认 `./downloads`）
4. 设置日期范围（快捷按钮：最近7天 / 本月 / 上月）
5. 勾选"跳过已下载过的"
6. 点击"开始下载"

下载文件按 `月份/类型/` 目录结构保存：

```
downloads/
├── 2026-05/
│   ├── 3d/           # 3D模型
│   │   ├── 模型名_ID.zip
│   │   └── 模型名_ID.jpg   # 预览图
│   ├── su/           # SketchUp模型
│   ├── tietu/        # 贴图（无预览图）
│   ├── ziliaoku/     # 资料库
│   ├── wenben/       # 文本
│   └── ps/           # PS素材
└── ...
```

**命令行方式：**

```bash
# 增量模式：下载指定日期范围的记录
uv run python downloader.py --mode incremental --start 2026-05-01 --end 2026-05-07

# 增量模式：下载最近 N 天
uv run python downloader.py --mode incremental --days 7

# 全量模式：下载全部历史记录（自动休息防封）
uv run python downloader.py --mode full
```

参数说明：

| 参数 | 说明 |
|------|------|
| `--mode incremental` | 增量模式，按日期范围下载 |
| `--mode full` | 全量模式，下载所有历史记录 |
| `--start YYYY-MM-DD` | 开始日期（增量模式） |
| `--end YYYY-MM-DD` | 结束日期（增量模式） |
| `--days N` | 最近 N 天（增量模式快捷写法） |
| `--force` | 强制重新下载已存在的文件 |

### 3. 查看下载历史

GUI 底部"下载历史"面板显示按日期汇总的统计数据。双击某一天可查看该日所有模型的详细下载状态。

## 常见问题

**Q: 提示"登录过期"？**
A: 点击"重新登录"重新扫码。Cookie 有效期取决于知末服务端，通常持续数天。

**Q: 下载数量为 0？**
A: 检查日期范围内是否有下载记录。如果日期范围内有记录但显示"全部比 XXX 新，跳过"，说明日期设得太早，记录还没被 API 翻到，尝试缩小日期范围。

**Q: 某些模型提示"不支持的 commodityType"？**
A: 知末新增了素材类型，需要更新 `COMMODITY_TYPE_MAP`。可以提 issue 或在日志中找到类型 ID 后自行添加。

**Q: 下载速度慢？**
A: 默认每个文件间隔 3-8 秒（可修改 `config.py` 中 `DELAY_MIN/MAX`），这是为了避免触发频率限制。调太快可能导致 429 错误。

## 配置

编辑 `config.py` 可调整：

```python
DELAY_MIN = 3          # 下载间隔最小值（秒）
DELAY_MAX = 8          # 下载间隔最大值（秒）
PAGE_DELAY_MIN = 2     # 翻页间隔最小值（秒）
PAGE_DELAY_MAX = 5     # 翻页间隔最大值（秒）
MAX_CONSECUTIVE_429 = 3  # 连续 429 错误后暂停
RATE_LIMIT_BACKOFF = 300 # 暂停时长（秒）
```

## License

MIT
