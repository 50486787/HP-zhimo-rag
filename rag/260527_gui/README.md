# 景观AI搜图 v2 — GUI 管理台

tkinter 桌面应用，统一管理数据流水线（标注→归并→入库）、搜索验证、服务启停。

## 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)
- Windows（tkinter 依赖）

## 安装

```bash
cd rag/260527_gui

# 创建虚拟环境 + 安装依赖
uv venv
uv pip install -r requirements.txt
```

## 启动

```bash
uv run python gui_app.py
# 或双击：启动GUI.bat
```

## 依赖服务（需提前运行）

| 服务 | 端口 | 说明 |
|------|------|------|
| RAGFlow (Docker) | 9900 | 文档存储与向量检索 |
| Xinference | 9997 | bge-m3 embedding 模型 |

GUI 服务面板可一键启动 Xinference 和 Web 搜索服务。RAGFlow 需单独启动 Docker。

## 四标签页

### 配置

- **识图模型** — Step1 盲标、Step3 精标用的视觉模型（API Key / Base URL / 模型名 / Temperature / 思考开关）
- **文字模型** — Step2 归并、Step6 翻译用的文本模型
- **RAGFlow** — 文档存储后端
- **BGE / Xinference** — embedding 服务配置
- **存储路径** — NAS 根目录（粗标/精标/词表/CSV 自动推导）
- **提示词模板** — Step1/Step2/Step3 的系统提示词，可编辑、保存方案、加载方案

所有配置通过 `config.json` 持久化，步骤文件不硬编码任何 key 或 URL。

### 流水线

6 个步骤，各自独立运行，不强制依赖顺序（增量时可跳过前面的步骤）。

| 步骤 | 说明 | 产出 |
|------|------|------|
| Step 0 | 扫描 NAS 目录结构 | `目录骨架与数量.json` |
| Step 1 | 识图模型批量盲标 | 粗标 JSON → `{NAS}/粗标输出/` |
| Step 2 | 文字模型同义词归并 | `规范词频_Top500.json` |
| Step 3 | 识图模型 + 受控词表精标 | `_精标.json`（写在原图旁边） |
| Step 4 | 生成 RAGFlow 入库 CSV | `ragflow_入库_*.csv`（输出到 NAS 父目录） |
| Step 5 | 搜索验证（加载文档+embedding） | 验证搜索管线可用性 |

每个步骤右上角实时显示状态（✓ 已完成 / ○ 未完成）。

### 搜索验证

- 初始化搜索引擎（加载 RAGFlow 文档 + BGE embedding）
- 关键词搜索 → Treeview 结果列表（命中/语义分色标记）
- 缩略图预览 + 标签详情
- 下载 ZIP / 打开原图

### 服务面板

| 服务 | 操作 |
|------|------|
| Docker Desktop | 状态检测 |
| RAGFlow | 启动/停止（docker compose） |
| Xinference (bge-m3) | 启动/停止（子进程） |
| Web 搜索服务 (HTTPS :8088) | 启动/停止/打开浏览器 |
| 飞秋搜图 Bot (UDP :2425) | 启动/停止 |

一键全部启动：RAGFlow → 5s → Xinference → 3s → Web → 2s → 飞秋 Bot。

## 步骤文件接口

每个 `stepXX.py` 导出统一接口，可被 GUI 调用也可独立运行：

```python
def run(...) -> (ok: bool, msg: str, path: str | None)
def check_done(**paths) -> (done: bool, detail: str)
```

## HTTPS 证书

Web 搜索服务使用 HTTPS。首次使用需要：

**服务器端：** 参考 `HTTPS证书配置.md` 用 mkcert 生成证书。

**员工客户端：** 把 `rootCA.pem` + `安装证书.bat` 放 NAS 共享目录，每人双击一次即可。详细方案见 `局域网HTTPS方案.md`。

## 飞秋搜图 Bot

收到飞秋消息 → 返回搜索链接 → 用户点链接在浏览器查看结果。

需关闭本机飞秋客户端（Bot 独占 2425 端口）。Web 服务需先启动。

## 项目结构

```
260527_gui/
├── gui_app.py              # 主界面入口
├── config.json             # 持久化配置
├── requirements.txt        # Python 依赖
├── 启动GUI.bat             # 双击启动
├── search_core.py          # 搜索引擎核心
├── step00_整理目录.py       # Step 0
├── step01_盲标_kimi_batch.py # Step 1
├── step02_归并.py           # Step 2
├── step03_精标_kimi_batch.py # Step 3
├── step04_构建入库CSV.py    # Step 4
├── step05_search.py        # Step 5
├── step06_web_server.py    # Web 服务 (FastAPI)
├── step07_feiq_bot.py      # 飞秋 Bot
├── static/                 # Web 前端
├── 知末粗标/               # V2 词表数据
├── .cert/                  # HTTPS 证书
├── GUI设计思路.md           # 设计文档
├── HTTPS证书配置.md         # 证书配置步骤
├── 局域网HTTPS方案.md       # 多客户端证书方案
├── 安装证书.bat             # 客户端一键装证书
└── rootCA.pem              # mkcert CA 根证书
```

## 常见问题

**Web 页面显示 404 图片**
检查配置页 NAS 路径是否正确。对于 RAGFlow 路径 `P260527_gui_gui测试`，NAS 应设为 `H:\claude_code\rag\`（包含 `260527_gui/` 的那一级）。

**Xinference 启动后状态显示未启动**
确认 Xinference 配置里 Host 为 `127.0.0.1`，端口为 `9997`。如果模型未加载，去 `http://127.0.0.1:9997/ui/` 手动启动 bge-m3。

**飞秋 Bot 发链接显示 127.0.0.1**
确认 Web 服务先启动，Bot 通过 Web 服务的 LAN IP 构造链接。

**关闭 GUI 后 Xinference 也关了**
GUI 关闭不会杀子进程。用服务面板的停止按钮正常停止。

**浏览器显示"不安全"**
参考 `HTTPS证书配置.md` 生成证书，客户端参考 `局域网HTTPS方案.md` 安装 CA。
