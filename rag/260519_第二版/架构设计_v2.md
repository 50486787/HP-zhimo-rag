# v2 搜图架构设计 — BGE 单层检索 + 分层加权

## 背景

v1 搜图链路: Kimi翻译 → RAGFlow向量检索 → LLM裁判重排。受知乎文章《从Embedding困局到确定性返回》启发，v2 改造核心思路：

- **不用 rerank**（用户反馈精度差）
- **RAGFlow 降级为纯数据存储**，不做检索
- **bge-m3 embedding 做基础分**，配合确定性规则加权
- **文件名 = 标题层**，权重最高（人工精选的关键词组合）

## 架构总览

```
用户口语
   ↓
Kimi k2.5 翻译 → {style, keywords[], vector_query}
   ↓
bge-m3 embed(vector_query) → 1×1024
   ↓
与预计算的 N×1024 文档矩阵做余弦相似度 → ×100 → 基础分 [0~100]
   ↓
确定性加权:
  - 文件名命中 keyword → +300
  - 标签命中 keyword → +100
  - 多关键词命中 → 额外加成
   ↓
排序 → Top-K → 拼接NAS路径 → 展示 jpg + zip
```

## 组件职责

| 组件 | 角色 | 接口 |
|------|------|------|
| **RAGFlow** | 增量数据库 + 管理后台 | /api/v1/retrieval (全量拉取) |
| **Xinference bge-m3** | 向量编码器 | /v1/embeddings |
| **Kimi k2.5** | 口语翻译 + 关键词提取 | OpenAI-compatible API |
| **search_v2.py** | 编排上述三个服务 | — |

## 数据流

### 启动加载

1. 调 RAGFlow retrieval API (所有 dataset_ids, top_k=2000, threshold=0) 拉全量
2. 解析每条 content: path, filename, tags
3. 构造 doc_text = filename + " " + tags (用于 embedding)
4. 调 bge-m3 批量 embed → N×1024 矩阵存内存
5. 同时存 filename 列表、path 列表（与矩阵行对齐）
6. 打印 "已加载 N 条文档"

### 查询

1. Kimi 翻译口语 → `{style, keywords[], vector_query}`
2. bge-m3 embed vector_query → 1×1024
3. numpy 矩阵点积 → cosine × 100 → 基础分
4. 遍历每条文档:
   - 文件名命中任意 keyword → +300
   - 标签命中任意 keyword → +100
   - 多关键词命中 → 额外叠加
5. argsort 降序 → Top-K
6. path 拼接 NAS base → 展示 jpg + zip

## 打分公式

```
最终分 = cosine(query_vec, doc_vec) × 100
       + Σ(filename命中keyword ? 300 : 0)
       + Σ(tags命中keyword ? 100 : 0)
```

cosine 值域 [-1, 1] → ×100 映射到 [-100, 100]。文件名命中一次 +300 即可碾压纯语义匹配。多关键词命中可叠加（每个额外 keyword 命中文件名再 +50）。匹配仅对长度 ≥2 的关键词生效，避免单字误匹配。

## 增量更新

新月份数据 → step04 生成 CSV → 导入 RAGFlow 新 dataset → 重启 search 脚本 → 自动加载全部文档

## 涉及改动

| 文件 | 改动 |
|------|------|
| `search_v2.py` | 重写：去掉 llm_weighted_rerank，新增启动加载 bge-m3 预计算、分层打分 |
| `step04_构建入库CSV.py` | 不改 |
| Xinference | 不改（bge-m3 已就绪） |
| RAGFlow | 不改（dataset 已导入 P01，待导入 P02-P04） |

## 关键决策

1. **不用 bge-reranker-v2-m3** — 用户反馈 rerank 精度差，且 cross-encoder 每次 1360 次 forward 太慢
2. **不用 RAGFlow 做检索** — RAGFlow 退化为纯数据存储，提供增量管理和 Web 后台
3. **分层打分来自文章启发** — 标题(文件名) = 最重信号，标签 = 中等信号，embedding = 兜底
4. **文件名命中用 Python 字符串匹配判定** — 确定性、可审计，不受 embedding 不确定性影响
