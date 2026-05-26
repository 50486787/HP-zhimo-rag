# v2 搜索交付层设计

## 目标

将 step05_search.py 的 CLI 搜索能力，以微信 Bot + Web 双通道交付给景观设计团队。

## 架构

```
常开 Win 台式机（内网）
├── 搜索核心（step05 改造，bge-m3 矩阵常驻内存）
├── wxauto 微信监听（独立后台进程）
├── FastAPI Web 服务（端口 8080）
└── 文件服务（映射 NAS 路径，代理图片 + 下载）
```

两个入口共享同一套搜索核心。RAGFlow 不参与查询，仅做增量数据库。

## 微信 Bot（二期）

- 监听微信消息，提取搜索词
- 调用 `/api/search`，回复短链接
- 微信回复只包含链接，不含搜索结果内容
- 示例：用户发"搜 中式凉亭水景" → Bot 回 `http://192.168.1.x:8080/search?q=中式凉亭水景`

## Web 页面（一期先做）

### 页面清单

| 路由 | 用途 |
|------|------|
| `/` | 首页：按月份分组 + 瀑布流浏览 |
| `/search?q=xxx` | 搜索结果页：按打分排序 |
| `/detail/P01/xxx` | 详情页：大图 + 完整标签 + 下载 |
| `/api/search?q=xxx` | JSON API（Bot 也走这个） |
| `/api/browse?month=xxx` | 浏览 API |
| `/img/P01/xxx.jpg` | 代理 NAS 预览图 |
| `/download/P01/xxx.zip` | 触发下载 |

### 首页 `/`

- 顶部搜索框
- 月份标签切换（从数据中提取可用的月份范围）
- 当前选中月份下的瀑布流：每张卡片 = 预览缩略图 + 文件名
- 默认显示最新月份

### 搜索结果页 `/search?q=xxx`

- 与首页相同布局，但按搜索打分排序
- 每张卡片标注命中类型（文件名命中 / 标签命中 / 语义兜底）
- 顶部保留搜索框，可再次搜索

### 详情页 `/detail/{path}/{filename}`

- 大图预览
- 完整标签信息（风格、构筑、植物、材质、光影等维度）
- 文件路径
- 下载 ZIP 按钮

### 瀑布流卡片

- 缩略图（NAS JPG 经 FastAPI 代理，压缩到 400px 宽）
- 文件名
- 风格标签
- 命中标记（搜索结果页才有）

## 技术选型

- **后端**：FastAPI，复用 step05 搜索核心（embed 矩阵后台常驻）
- **前端**：单 HTML 文件内嵌 JS/CSS，无框架
- **瀑布流**：CSS columns 或 grid masonry
- **图片代理**：FastAPI StaticFiles 或 FileResponse 映射 NAS 路径
- **缩略图**：Pillow 实时 resize + 缓存到本地

## 数据流

```
启动 → load_all_docs() → bge-m3 embed → 矩阵常驻内存
查询 → translate(Kimi) → embed query → 矩阵点积 → 分层加权 → JSON
Web → 调用搜索核心 → 渲染 HTML
Bot → 调用 /api/search → 拼接短链接 → 微信回复
```

## 不需要做的事

- ❌ 不引入 rerank
- ❌ 不用 RAGFlow 检索 API
- ❌ 不引入前端框架（React/Vue）
- ❌ 不做用户认证（内网信任环境）
- ❌ 微信 Bot 一期不做
