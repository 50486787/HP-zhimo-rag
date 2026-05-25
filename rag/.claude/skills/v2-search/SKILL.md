---
name: v2-search
description: v2 景观搜图系统 — bge-m3 全量打分 + 分层加权。涉及 step05_search.py 或 search_v2.py 的任何改动时使用此技能。
---

# v2 景观搜图系统

## 架构速览

```
启动: RAGFlow chunks API → 全量加载 → bge-m3 预计算 1360×1024 矩阵 → 存内存
查询: Kimi翻译 → bge-m3 embed query → 矩阵点积 → 文件名+300/标签+100 → 排序
```

## 核心决策

1. **不用 RAGFlow 做检索** — RAGFlow 只是增量数据库，查询不走它
2. **不用 rerank** — 用户认为精度差。bge-m3 单层打分 + 规则加权替代
3. **文件名 = 最高权重** — 文件名是人工精选的关键词串，匹配 +300/词
4. **口语→规范词映射** — Kimi 负责翻译，"院子"→"庭院"，"水池"→"水景"
5. **复合词拆分去元标签** — "奶油风格"→只保留"奶油"，"风格""材质"不放 keywords
6. **不分 chunk** — 一文件一行，1360 条不改动

## 文件地图

| 文件 | 用途 |
|------|------|
| `step05_search.py` | **正式搜索脚本**（当前使用） |
| `step04_构建入库CSV.py` | 精标 JSON → RAGFlow 入库 CSV |
| `step03_精标_kimi_batch.py` | 批量精标 |
| `step02_标签同义词归并.py` | 词汇归纳 |
| `step01_盲标_kimi_batch.py` | 盲标 |
| `架构设计_v2.md` | 完整设计文档 |
| `知末粗标/规范词频_Top500.json` | Top 500 受控词表 |
| `知末精标/` | 精标 JSON（按 P01-P04 分目录）+ 入库 CSV |
| `RAG环境备忘.md` | Docker、RAGFlow、Xinference 安装备忘 |
| `启动_Xinference.bat` | Xinference 启动（必须 127.0.0.1） |
| `启动_RAGFlow.bat` | RAGFlow Docker 启动 |

## 打分公式

```
最终分 = cosine(query_vec, doc_vec) × 100
       + Σ(filename命中keyword? 300：0)
       + Σ(tags命中keyword? 100：0)
```

- 文件名按 `_` 分词后逐段匹配，每 keyword 最多命中一次
- 仅 len ≥ 2 的关键词参与匹配
- 命中全部显示，纯语义最多 5 条，分页 30 条

## 依赖服务

| 服务 | 地址 | 用途 |
|------|------|------|
| RAGFlow | http://127.0.0.1:9900 | 增量存储 |
| Xinference bge-m3 | http://127.0.0.1:9997 | embedding |
| Kimi k2.5 | api.moonshot.cn | 口语翻译 |

## 运行

```powershell
# 1. 确保 Xinference bge-m3 已启动（http://127.0.0.1:9997）
# 2. 确保 RAGFlow 已启动（http://127.0.0.1:9900）
python step05_search.py
# 输入 NAS 路径：\\192.168.1.203\知末备份
```

## 增量更新

新数据 → `step04_构建入库CSV.py` 生成 CSV → RAGFlow 导入 → 重启 step05

## 已知局限

- 2字短词可能误匹配长词的一部分（如"客厅"误中"会客厅"），无词典无法根治
- Kimi 翻译偶尔不遵循映射规则，prompt 已做约束但无法保证 100%
- RAGFlow 路径列的 P 前缀（P01）是为了防止数字/日期解析

## 修改 search 时必须遵守

1. 所有代码放在 `260519_第二版/`
2. 不要引入 rerank
3. 不要用 RAGFlow 检索 API（用 chunks 列表 API）
4. Kimi prompt 改后必须测试"电梯间""奶油风格""院子"等易错 case
