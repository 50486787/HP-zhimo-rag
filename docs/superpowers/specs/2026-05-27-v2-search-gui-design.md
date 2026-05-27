# v2 景观搜图系统 — GUI 管理台 设计规格

> **定位：** 运维管理工具，不面向设计师（设计师用 Web 版）。管理配置、数据流水线、搜索验证、Web 服务控制。

**技术栈：** tkinter/ttk, Pillow, Python 3.10+

**约束：** 所有代码放在 `rag/260527_gui/` 下，不修改 `rag/260519_第二版/` 原有文件。

---

## 一、架构

```
rag/260527_gui/
├── config.json          # 持久化配置：API keys、模型名、路径
├── gui_app.py           # 主程序：tkinter UI 层（四标签页）
├── pipeline.py          # 流水线业务逻辑层（无 tkinter 依赖）
└── .thumb_cache/        # 搜索缩略图缓存（自动创建）
```

**与现有代码的关系：**
- `gui_app.py` 从 `../260519_第二版/search_core.py` 导入 `Searcher` 类
- `pipeline.py` 从 `../260519_第二版/step04_构建入库CSV.py` 导入 `build_csv()` 等函数
- Step 0 逻辑目前仅在 `01-第一步整理文件夹出json.py` 中，需提取可复用函数到 `pipeline.py`
- `260519_第二版/` 中的文件不做任何修改

---

## 二、UI 布局

### 整体结构

左侧 `ttk.Treeview` 导航（宽度 ~140px），右侧 `ttk.Notebook` 四个标签页。

```
┌──────────┬──────────────────────────────────────────┐
│ ⚙ 配置   │                                          │
│ 📂 流水线 │         当前标签页内容                    │
│ 🔍 搜索   │                                          │
│ 🌐 服务   │                                          │
│          │                                          │
├──────────┴──────────────────────────────────────────┤
│  [进度条]                         日志区 (5行)      │
└─────────────────────────────────────────────────────┘
```

窗口尺寸：980×680，最小 780×500。

### 标签 1：⚙ 配置

三组配置卡片 + 路径设置：

- **Kimi**（翻译 & 标注）：API Key（`*` 密码遮盖）、Base URL、Model 三个输入框
- **RAGFlow**（文档存储）：API Key、Base URL、Dataset IDs 三个输入框
- **BGE**（Embedding）：Base URL、Model 两个输入框
- **路径**：NAS 根目录（带"浏览"按钮）、规范词表路径（带"浏览"按钮）
- **Web**：端口号、Host

底部操作按钮：`[保存配置]` `[从文件加载]`

行为：
- 启动时自动读取 `config.json` 填充表单
- "保存配置"写入 `config.json`（覆盖）
- "从文件加载"用 `filedialog` 选择其他配置文件

### 标签 2：📂 数据流水线

五个步骤卡片，垂直排列，显示依赖关系：

| 步骤 | 名称 | 运行方式 | 按钮 |
|------|------|---------|------|
| Step 0 | 整理文件夹结构出 JSON | GUI 直接运行 | `[运行]` + 目标目录输入 |
| Step 1 | Kimi 批量盲标 | 命令行脚本 | 无按钮，显示状态 ✓/○ |
| Step 2 | 标签同义词归并 + 词频统计 | GUI 直接运行 | `[运行]` |
| Step 3 | Kimi 批量精标 | 命令行脚本 | 无按钮，显示状态 ✓/○ |
| Step 4 | 构建 RAGFlow 入库 CSV | GUI 直接运行 | `[生成]` + 拆分复选框 |

依赖检测：
- Step 2 运行时检查粗标目录是否存在
- Step 4 运行时检查精标目录是否存在
- 路径均从 config.json 的 paths 字段推导，或由用户在卡片中单独指定

Step 1/3 的状态展示：
- ✓ 已完成（绿色）：检测到输出目录存在且非空
- ○ 待执行（灰色）：输出目录不存在或为空

每步执行时在日志区输出进度信息。执行在线程中运行，不阻塞 UI。

### 标签 3：🔍 搜索验证

复用原型 `gui_app.py` 的设计：

- 顶部搜索栏：输入框 + 搜索按钮 + 分组下拉框
- 结果列表：`ttk.Treeview`（文件名 | 分组 | 风格 | 得分 | 匹配）
- 右侧预览面板：缩略图（Canvas）+ 标签信息（Text）+ 下载/打开按钮
- 引擎初始化状态：搜索标签顶部显示"引擎未初始化 / 就绪 (xxx 条文档)"+ 初始化按钮

### 标签 4：⚡ 服务面板

统一管理所有后台服务，四个服务垂直排列，每个一行：

| 服务 | 启动方式 | 检测方式 | 管理 |
|------|---------|---------|------|
| Docker Desktop | 用户手动启动 | `docker info` 命令 | 只检测，不管理 |
| RAGFlow | `docker compose up -d` | HTTP GET `:9900` | 启停 + 打开网页 |
| Xinference (bge-m3) | `xinference-local --host 0.0.0.0 --port 9997` | HTTP GET `:9997` | 启停 + 打开网页 |
| Web 搜索服务 | `python step06_web_server.py` | HTTP GET `:8088` | 启停 + 打开浏览器 |

每行显示：图标 + 服务名 + 启动命令（灰色小字）+ 状态指示（● 运行中 / ● 已停止）+ 地址 + 启停按钮。

底部操作栏：`[一键全部启动]` `[全部停止]`，跳过已运行的服务。

服务日志：底部共享日志区，显示最近启动/停止操作的输出。

各服务通过 `subprocess.Popen` 启动，stdout/stderr 管道读取到日志区，停止时 `terminate()`。状态检测通过定时轮询（每 5 秒）或按钮点击时触发。

---

## 三、数据流

### 配置流

```
启动 gui_app.py → 读取 config.json → 填充表单
用户修改 → 点击保存 → 写入 config.json
pipeline.py 执行 Step 时 → 读取 config.json 获取 API Key 和路径
搜索标签初始化引擎时 → 读取 config.json 构造 Searcher()
```

### 流水线执行流

```
用户点击 [运行] → gui_app 开线程 → pipeline.run_stepX(config, ...)
→ 输出写入日志队列 → UI 轮询更新日志区
→ 完成后 → 显示结果路径 / 错误信息
```

### 搜索流

```
用户输入查询 → gui_app 开线程 → Searcher.search(query)
→ 结果放入队列 → UI 更新 Treeview
→ 单击结果 → 开线程加载缩略图 → Canvas 显示
```

### 服务控制流

```
点击启动 → subprocess.Popen("python step06_web_server.py", ...)
→ stdout 管道读取 → 日志队列 → 日志区
点击停止 → process.terminate()
```

---

## 四、config.json 格式

```json
{
  "kimi": {
    "api_key": "sk-xxx...",
    "base_url": "https://api.moonshot.cn/v1",
    "model": "kimi-k2.5"
  },
  "ragflow": {
    "api_key": "ragflow-xxx...",
    "base_url": "http://127.0.0.1:9900/api/v1",
    "dataset_ids": ["ae52896e580711f1ba3a0fbde202da50"]
  },
  "bge": {
    "base_url": "http://127.0.0.1:9997/v1",
    "model": "bge-m3"
  },
  "paths": {
    "nas_base": "\\\\192.168.1.203\\知末备份",
    "vocab": "知末粗标/规范词频_Top500.json",
    "raw_label_dir": "知末粗标",
    "refined_label_dir": "知末精标",
    "output_dir": ""
  },
  "web": {
    "port": 8088,
    "host": "0.0.0.0"
  }
}
```

---

## 五、pipeline.py 接口

```python
# pipeline.py — 无 tkinter 依赖，可独立测试

def run_step0(target_dir: str, output_dir: str = None) -> tuple[bool, str]:
    """扫描目录结构 → 生成 目录骨架与数量.json。
    Returns: (success, output_path_or_error)"""

def run_step2(raw_label_dir: str, output_path: str = None) -> tuple[bool, str]:
    """同义词归并 + 词频统计 → 生成 规范词频_Top500.json。
    Returns: (success, output_path_or_error)"""

def run_step4(refined_label_dir: str, output_dir: str = None, split: bool = True) -> tuple[bool, str]:
    """构建 RAGFlow 入库 CSV。
    Returns: (success, output_dir_or_error)"""

def check_step_done(step: int, **paths) -> bool:
    """检测某步骤的输出目录是否存在且非空"""
```

---

## 六、不需要做的事

- ❌ 不在 GUI 内跑 Kimi 批量标注（Step 1/3）
- ❌ 不做微信 Bot
- ❌ 不做用户认证
- ❌ 不做 Web 版的瀑布流前端（那是给设计师的）
- ❌ 不修改 260519_第二版/ 原有文件
- ❌ 不做多语言 / 主题切换
- ❌ 不做数据统计 / 仪表盘

---

## 七、错误处理策略

- 配置文件缺失：启动时提示，使用代码中硬编码的默认值
- RAGFlow/Xinference 不可达：搜索标签初始化失败时日志报错，允许重试
- NAS 路径不可达：图片预览显示"无预览图"，文件操作弹窗提示
- Step 依赖不满足：弹窗提示"请先完成 Step X"，不执行
- 线程异常：捕获后写入日志队列，不崩溃主窗口
