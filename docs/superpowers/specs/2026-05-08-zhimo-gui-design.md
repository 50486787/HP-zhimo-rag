# 知末下载器 GUI 规格说明

**日期**: 2026-05-08  
**技术栈**: Python tkinter（标准库，零额外依赖）

## 概述

将命令行工具 `python downloader.py --mode incremental --days 7` 封装为桌面 GUI，双击即用。

## 界面布局

单窗口，从上到下 5 个区域：

### 1. 账号与存储
- 登录状态标签（已登录/未登录，绿色/红色）
- 账号名称（从 accountInfo API 获取 nickName）
- 企业 ID（从 accountInfo API 获取 enterpriseId）
- "重新登录"按钮 → 调用 Playwright 打开浏览器登录
- 下载目录显示 + "浏览"按钮 → 文件夹选择对话框

### 2. 下载设置
- 起始日期输入框（默认 7 天前）
- 结束日期输入框（默认今天）
- 快捷按钮：最近7天、本月、上月（点击自动填充日期）
- 复选框："跳过已下载过的"（默认勾选，对应 `--force` 的反向逻辑）

### 3. 控制区
- "开始下载"按钮 → 在后台线程启动下载
- "停止"按钮 → 设置停止标志
- 进度文字："第 X/Y 页 · 已下载 N 条"
- 进度条

### 4. 运行日志
- 浅色背景、等宽字体、只读文本框
- 实时追加日志行（成功=绿色，失败=红色，信息=默认）
- 自动滚动到底部

### 5. 下载历史
- 表格视图：日期 | 总数 | 成功 | 失败
- 从 SQLite 按日期聚合查询
- 点击某行展开/弹窗显示当天详细记录

## 数据流

```
GUI (tkinter)
  ├── 主线程：UI 渲染、事件处理
  ├── 后台线程：运行下载逻辑（复用 downloader.py 核心函数）
  └── 通过 queue.Queue 将日志/进度推送到主线程更新 UI
```

登录时复用 `login.py` 的 Playwright 登录流程，在主线程打开浏览器（阻塞 UI 直到登录完成）。

## 需要改动的现有代码

### downloader.py
- 抽取 `run()` 中的核心逻辑为可被 GUI 调用的函数
- 接受回调参数用于推送日志和进度
- 支持 `start_date` / `end_date` 参数（替换现有的 `days` 参数）
- 支持停止标志

### config.py
- 无需改动

### db.py
- 新增按日期聚合查询函数：`get_daily_summary(db_path)`
- 新增按日期查询详情函数：`get_records_by_date(db_path, date)`

### login.py
- 无需改动（GUI 直接调用 `login_and_save_cookies()`）

## 新增文件

### gui.py
单文件，约 300-400 行。结构：

```
class DownloaderGUI:
    def __init__(self):       # 构建界面
    def setup_ui(self):       # 各区域布局
    def on_login(self):       # 登录按钮回调
    def on_start(self):       # 开始下载回调
    def on_stop(self):        # 停止回调
    def start_download_thread(self):  # 启动后台线程
    def log(self, msg, level):  # 线程安全日志
    def update_progress(self, page, total_pages, count):
    def refresh_history(self):  # 刷新历史表格
```

## 非目标
- 不实现 Windows 安装包（直接 `python gui.py`）
- 不实现下载完成通知
- 不实现定时任务
- 不实现多账号切换
