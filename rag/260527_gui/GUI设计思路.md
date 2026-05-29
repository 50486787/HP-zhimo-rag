# v2 景观搜图系统 GUI 管理台 — 设计思路

## 概述

tkinter 桌面应用，统一管理数据流水线、搜索验证、服务启停。参考知末下载器的 GUI 模式（LabelFrame、Treeview、queue 轮询、线程异步）。

## 架构原则

- **单文件入口** `gui_app.py`，步骤逻辑各自独立文件（step00~step07）
- **线程异步**：所有耗时操作（搜索、流水线、服务启动）放入 daemon 线程，通过 queue 回传结果
- **queue 轮询**：`root.after(100ms)` 轮询 log_queue / result_queue / preview_queue，避免阻塞主线程
- **配置持久化**：`config.json` 存储 Kimi/RAGFlow/BGE/路径/Web 配置
- **隔离原则**：GUI 目录自包含，不依赖外部文件夹

## 四标签页设计

### 1. 配置（⚙）
- 识图模型：API Key、Base URL、模型（Step1/3 标注用）
- 文字模型：API Key、Base URL、模型（Step2/6 翻译归并用）
- RAGFlow：API Key、Base URL、数据集 ID
- BGE Embedding（Xinference）：Base URL、模型名
- 存储路径：NAS 根目录、工作目录（粗标/精标/词表/CSV 自动推导）
- Web 服务：端口、显示本机局域网 IP
- 操作：保存配置、从文件加载

### 2. 流水线（📂）
- 6 个步骤（0~5），全部有运行按钮 + 状态实时刷新
- Step 0：整理文件夹结构出 JSON
- Step 1：Kimi 识图模型批量盲标
- Step 2：文字模型同义词归并 → Top500 词表
- Step 3：Kimi 识图模型 + 受控词表精标
- Step 4：生成 RAGFlow 入库 CSV（支持按子目录拆分）
- Step 5：加载文档 + embedding 验证搜索管线
- 步骤之间不强制依赖，增量时可跳过（只 warn 不拦截）
- 所有子路径从工作目录自动推导，自动创建缺失目录

### 3. 搜索验证（🔍）
- 引擎初始化：加载 Searcher，使用文字模型翻译
- 搜索栏：关键词输入 + 分组筛选下拉框
- 结果列表：Treeview（文件名、分组、风格、得分、匹配方式）
  - 命中（文件名/标签匹配）绿色标记
  - 语义匹配灰色标记
- 预览面板：缩略图 + 标签详情 + JPG/ZIP 状态
- 操作：下载 ZIP、打开原图

### 4. 服务面板（⚡）
- Docker Desktop 状态检测
- RAGFlow 启停（docker compose up/down）
- Xinference 启停（子进程管理）
- Web 搜索服务 启停（显示本机 IP）
- 飞秋搜图 Bot 启停（子进程管理）
- 一键全部启动 / 全部停止
- 延迟启动顺序：RAGFlow → (5s) → Xinference → (3s) → Web → (2s) → 飞秋

## UI 布局

```
┌──────────┬──────────────────────────────┐
│  导航栏   │  标签页内容（可滚动 Canvas）   │
│  ⚙ 配置  │                              │
│  📂 流水线│                              │
│  🔍 搜索  │                              │
│  ⚡ 服务  │                              │
├──────────┴──────────────────────────────┤
│  进度条  │  日志区（彩色 tag：error/success/warn/info）│
└──────────────────────────────────────────┘
```

## tkinter 模式（参考知末下载器）

- **LabelFrame**：分组框，每步一个
- **Treeview**：导航树 + 搜索结果列表
- **PanedWindow**：主布局（导航 | 内容）+ 搜索结果（列表 | 预览）
- **Canvas + Scrollbar**：配置页和流水线页支持鼠标滚轮滚动
- **queue.Queue**：线程 → 主线程通信（log_queue、result_queue、preview_queue）
- **threading.Thread(daemon=True)**：所有后台任务
- **progressbar**：耗时操作时启动 indeterminate 模式

## 步骤接口规范

每个 step 文件导出：
```python
def run(...) -> (ok: bool, msg: str, path: str|None)
def check_done(**paths) -> (done: bool, detail: str)
```

GUI 通过 `threading.Thread` 异步调用 `run()`，结果通过 `log_queue` 回传。

## 设计决策

- **DPI 缩放**：PerMonitorV2 感知，1200×800 默认尺寸，900×600 最小
- **模型分离**：识图模型和文字模型各自独立配置，搜索初始化用文字模型
- **路径简化**：只设工作目录，粗标/精标/词表/CSV 全部自动推导
- **导航去重**：隐藏 Notebook 标签头，仅左侧导航树切换
- **地址显示**：服务面板显示本机局域网 IP，方便远程访问
- **增量友好**：步骤间不强制依赖，可跳过直接运行后续步骤
