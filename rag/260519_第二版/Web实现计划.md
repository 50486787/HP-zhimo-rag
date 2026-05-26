# Web 搜索交付 实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 将 step05 搜索核心封装为 FastAPI Web 服务，提供按分组瀑布流浏览 + 搜索功能

**架构：** 提取 Searcher 类（search_core.py）→ FastAPI 启动时初始化 → 单 HTML 前端（`/static/index.html`）通过 JSON API 获取数据 → CSS columns 瀑布流展示

**技术栈：** FastAPI, uvicorn, Pillow, vanilla JS/CSS（无框架）

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `search_core.py` | Searcher 类：加载词表、翻译、embed、搜索 | **新建** |
| `step06_web_server.py` | FastAPI 应用 + API 路由 + 静态文件 | **新建** |
| `static/index.html` | 单页前端：瀑布流、搜索框、详情弹窗 | **新建** |
| `step05_search.py` | 保持不变，CLI 搜索仍可用 | 不改 |

---

### 任务 1：提取 search_core.py 共享搜索模块

**文件：** 创建 `rag/260519_第二版/search_core.py`

- [ ] **步骤 1：创建 Searcher 类骨架**

将 step05 中的配置、翻译、加载、embed、打分逻辑提取为 `Searcher` 类：

```python
"""v2 搜索核心 — 可被 CLI 和 Web 共用"""
import sys, os, json, re, time
import numpy as np
import requests
from openai import OpenAI


STYLES = ["现代", "新中式", "中式", "轻奢", "极简", "古风", "意式", "欧式", "日式", "热带度假风", "禅意", "赛博朋克", "宋式", "田园", "卡通风", "工业风"]


class Searcher:
    def __init__(self, vocab_path, ragflow_key, ragflow_ds_ids, ragflow_base, kimi_api_key, kimi_base_url, bge_base_url):
        self.vocab = self._load_vocab(vocab_path)
        self.translate_prompt = self._make_translate_prompt()
        self.ragflow_key = ragflow_key
        self.ragflow_ds_ids = ragflow_ds_ids
        self.ragflow_base = ragflow_base
        self.kimi = OpenAI(api_key=kimi_api_key, base_url=kimi_base_url)
        self.bge = OpenAI(api_key="not-needed", base_url=bge_base_url)

        # 初始化后填充
        self.doc_paths = []
        self.doc_fns = []
        self.doc_tags_list = []
        self.doc_matrix = None
        self.ready = False

    def _load_vocab(self, path):
        with open(path, encoding="utf-8-sig") as f:
            return [item["词"] for item in json.load(f)]

    def _make_translate_prompt(self):
        words = ", ".join(self.vocab)
        return f"""# Role
你是景观图库检索翻译官。将用户口语转化为精确搜索关键词。

# 受控词表（优先使用）
{words}

# 任务
1. 识别用户意图中的风格（限：{", ".join(STYLES)}），未提及则留空，不要猜测
2. 提取核心构筑元素——关键：将用户口语词映射到受控词表中的规范词（如"院子"→"庭院"，"水池"→"水景"），受控词表里有的就用规范词
3. 提取植物类型，保留品种名
4. 提取材质类型
5. 保留用户原话的视觉特征词

# 重要规则
- keywords 必须包含用户原话的全部核心实体词，不能丢失或替换
- vector_query 用 keywords 拼接生成，不要添加用户未提及的风格/材质/植物等无关维度
- 复合词拆分时去掉元标签：如"奶油风格"→只保留"奶油"，"风格""材质""颜色"等元标签词不要放进 keywords

# Output JSON
{{{{
  "style": "风格 或 空",
  "keywords": ["核心词1", "核心词2", ...],
  "vector_query": "keywords用空格拼接"
}}}}
"""

    def translate(self, query):
        completion = self.kimi.chat.completions.create(
            model="kimi-k2.5",
            messages=[
                {"role": "system", "content": self.translate_prompt},
                {"role": "user", "content": f"用户输入：{query}"}
            ],
            temperature=0.6,
            extra_body={"thinking": {"type": "disabled"}},
        )
        content = completion.choices[0].message.content
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return {"style": "", "keywords": [query], "vector_query": query}

    def load_docs(self):
        headers = {"Authorization": f"Bearer {self.ragflow_key}"}
        all_chunks = []
        for ds_id in self.ragflow_ds_ids:
            r = requests.get(f"{self.ragflow_base}/datasets/{ds_id}/documents", headers=headers, timeout=30)
            if r.status_code != 200:
                print(f"  [WARN] dataset {ds_id} 获取失败: {r.status_code}")
                continue
            for doc in r.json()["data"]["docs"]:
                doc_id = doc["id"]
                chunk_count = doc["chunk_count"]
                r2 = requests.get(
                    f"{self.ragflow_base}/datasets/{ds_id}/documents/{doc_id}/chunks",
                    params={"page": 1, "page_size": chunk_count + 100},
                    headers=headers, timeout=30)
                if r2.status_code != 200:
                    print(f"  [WARN] {doc['name']} chunks 获取失败: {r2.status_code}")
                    continue
                chunks = r2.json()["data"]["chunks"]
                all_chunks.extend(chunks)
                print(f"  {doc['name']}: {len(chunks)} chunks")
        print(f"总计 {len(all_chunks)} 条文档")
        return all_chunks

    def _parse_chunk(self, raw):
        m_path = re.search(r'- .?path:\s*(.+)', raw)
        m_fn = re.search(r'- filename:\s*(.+)', raw)
        m_ct = re.search(r'- content:\s*(.+)', raw, re.DOTALL)
        return (
            m_path.group(1).strip() if m_path else "",
            m_fn.group(1).strip() if m_fn else "",
            m_ct.group(1).strip() if m_ct else raw,
        )

    def _embed(self, texts):
        resp = self.bge.embeddings.create(model="bge-m3", input=texts)
        return np.array([d.embedding for d in resp.data], dtype=np.float32)

    def _normalize(self, vecs):
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1e-10
        return vecs / norms

    def initialize(self):
        """启动时调用：加载文档 + 预计算 embedding 矩阵"""
        t0 = time.time()
        print("加载 RAGFlow 文档...")
        chunks = self.load_docs()
        if not chunks:
            raise RuntimeError("无文档")

        self.doc_paths, self.doc_fns, self.doc_tags_list, doc_texts = [], [], [], []
        for c in chunks:
            raw = c.get("content_with_weight", c.get("content", ""))
            path, fn, tags = self._parse_chunk(raw)
            self.doc_paths.append(path)
            self.doc_fns.append(fn)
            self.doc_tags_list.append(tags)
            doc_texts.append(f"{fn} {tags}")

        print(f"embedding {len(doc_texts)} 条文档 (bge-m3)...")
        self.doc_matrix = self._embed(doc_texts)
        self.doc_matrix = self._normalize(self.doc_matrix)
        self.ready = True
        print(f"就绪，矩阵 {self.doc_matrix.shape}，耗时 {time.time() - t0:.1f}s")

    def get_groups(self):
        """返回所有分组（按 path 字段去重排序）"""
        groups = list(dict.fromkeys(self.doc_paths))
        groups.sort()
        return groups

    def get_group_items(self, group):
        """返回某个分组下的所有文档索引"""
        return [i for i, p in enumerate(self.doc_paths) if p == group]

    @staticmethod
    def _kw_score(keywords, text, weight):
        score = 0
        segments = text.split("_") if "_" in text else [text]
        for kw in keywords:
            if len(kw) < 2:
                continue
            for seg in segments:
                if kw in seg:
                    score += weight
                    break
        return score

    def search(self, query):
        """执行搜索，返回结果列表"""
        if not self.ready:
            raise RuntimeError("Searcher 未初始化，请先调用 initialize()")

        trans = self.translate(query)
        keywords = trans.get("keywords", [])
        vq = trans.get("vector_query", query)

        q_vec = self._embed([vq])
        q_vec = self._normalize(q_vec)
        q_norm = q_vec[0]

        base = (self.doc_matrix @ q_norm) * 100

        hits, semantic = [], []
        for i in range(len(self.doc_fns)):
            fn_score = self._kw_score(keywords, self.doc_fns[i], 300)
            tag_score = self._kw_score(keywords, self.doc_tags_list[i], 100)
            score = float(base[i]) + fn_score + tag_score

            item = {
                "filename": self.doc_fns[i],
                "path": self.doc_paths[i],
                "tags": self.doc_tags_list[i],
                "base_score": round(float(base[i]), 1),
                "final_score": round(score, 1),
                "fn_hit": fn_score > 0,
                "tag_hit": tag_score > 0,
            }
            (hits if (fn_score + tag_score > 0) else semantic).append(item)

        hits.sort(key=lambda x: x["final_score"], reverse=True)
        semantic.sort(key=lambda x: x["final_score"], reverse=True)

        return {
            "style": trans.get("style", ""),
            "keywords": keywords,
            "vector_query": vq,
            "results": hits + semantic[:5],
            "total": len(hits) + min(len(semantic), 5),
            "n_hit": len(hits),
            "n_semantic": min(len(semantic), 5),
        }
```

- [ ] **步骤 2：验证 search_core.py 可导入**

```
python -c "from search_core import Searcher; print('OK')"
```

预期：无报错，输出 `OK`

- [ ] **步骤 3：Commit**

```bash
git add rag/260519_第二版/search_core.py
git commit -m "feat: extract Searcher class from step05 into search_core.py"
```

---

### 任务 2：创建 FastAPI Web 服务

**文件：** 创建 `rag/260519_第二版/step06_web_server.py`

- [ ] **步骤 1：编写 FastAPI 应用骨架**

```python
"""step06: FastAPI Web 搜索服务"""
import sys, os, io, re
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

# 将当前目录加入 sys.path 以导入 search_core
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from search_core import Searcher

# ======== 配置 ========
VOCAB_PATH = os.path.join(os.path.dirname(__file__), "知末粗标", "规范词频_Top500.json")
RAGFLOW_KEY = "ragflow-GELaRwRQXrL4oNxlciCEtmXJUUhj_9Ma0UwT38Yn1xU"
RAGFLOW_DS = ["ae52896e580711f1ba3a0fbde202da50"]
RAGFLOW_BASE = "http://127.0.0.1:9900/api/v1"
KIMI_KEY = "sk-g9ys55fFGKkJTsuTkEYhjC5sqEY83HfUyxRmVe1pm8QJmnN3"
KIMI_BASE = "https://api.moonshot.cn/v1"
BGE_BASE = "http://127.0.0.1:9997/v1"

# NAS 路径通过环境变量或启动参数传入
NAS_BASE = os.environ.get("NAS_BASE", "")
THUMB_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".thumb_cache")

# ======== 初始化 Searcher ========
searcher = Searcher(
    vocab_path=VOCAB_PATH,
    ragflow_key=RAGFLOW_KEY,
    ragflow_ds_ids=RAGFLOW_DS,
    ragflow_base=RAGFLOW_BASE,
    kimi_api_key=KIMI_KEY,
    kimi_base_url=KIMI_BASE,
    bge_base_url=BGE_BASE,
)

app = FastAPI(title="景观搜图 v2")

@app.on_event("startup")
async def startup():
    os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
    searcher.initialize()
    if not NAS_BASE:
        print("[WARN] NAS_BASE 未设置，图片和下载功能不可用。请设置环境变量 NAS_BASE")


# ======== 页面路由 ========

@app.get("/", response_class=HTMLResponse)
async def index():
    """首页：分组瀑布流浏览"""
    static_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(static_path):
        with open(static_path, encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>static/index.html 未找到</h1>", status_code=404)


# ======== API 路由 ========

@app.get("/api/groups")
async def api_groups():
    return {"groups": searcher.get_groups()}


@app.get("/api/browse")
async def api_browse(group: str = Query(...), page: int = Query(1), page_size: int = Query(50)):
    indices = searcher.get_group_items(group)
    total = len(indices)
    start = (page - 1) * page_size
    end = start + page_size
    batch = indices[start:end]

    items = []
    for i in batch:
        items.append({
            "filename": searcher.doc_fns[i],
            "path": searcher.doc_paths[i],
            "tags": searcher.doc_tags_list[i],
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/api/search")
async def api_search(q: str = Query(...)):
    result = searcher.search(q)
    return result


@app.get("/api/detail")
async def api_detail(path: str = Query(...), filename: str = Query(...)):
    """返回单条文档的详细信息"""
    for i in range(len(searcher.doc_fns)):
        if searcher.doc_paths[i] == path and searcher.doc_fns[i] == filename:
            return {
                "filename": searcher.doc_fns[i],
                "path": searcher.doc_paths[i],
                "tags": searcher.doc_tags_list[i],
                "jpg_exists": os.path.exists(os.path.join(NAS_BASE, path, filename + ".jpg")) if NAS_BASE else False,
                "zip_exists": os.path.exists(os.path.join(NAS_BASE, path, filename + ".zip")) if NAS_BASE else False,
            }
    raise HTTPException(status_code=404, detail="未找到")


# ======== 图片代理 ========

@app.get("/img/{rest_path:path}")
async def serve_image(rest_path: str, w: int = Query(400)):
    """代理 NAS 图片，自动缩略"""
    if not NAS_BASE:
        raise HTTPException(status_code=503, detail="NAS_BASE 未配置")

    full_path = os.path.join(NAS_BASE, rest_path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="图片不存在")

    # 缩略图缓存
    cache_key = f"{rest_path.replace('/', '_')}_{w}.jpg"
    cache_path = os.path.join(THUMB_CACHE_DIR, cache_key)
    if os.path.exists(cache_path):
        return FileResponse(cache_path, media_type="image/jpeg")

    try:
        img = Image.open(full_path)
        img = img.convert("RGB")
        ratio = w / img.width
        h = int(img.height * ratio)
        img = img.resize((w, h), Image.LANCZOS)
        img.save(cache_path, "JPEG", quality=80)
        return FileResponse(cache_path, media_type="image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======== 下载 ========

@app.get("/download/{rest_path:path}")
async def download_file(rest_path: str):
    """下载 ZIP 文件"""
    if not NAS_BASE:
        raise HTTPException(status_code=503, detail="NAS_BASE 未配置")

    full_path = os.path.join(NAS_BASE, rest_path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    filename = os.path.basename(full_path)
    # 中文文件名需要正确编码
    return FileResponse(full_path, filename=filename, media_type="application/zip")


# ======== 启动入口 ========
if __name__ == "__main__":
    import uvicorn

    nas = os.environ.get("NAS_BASE", "")
    if not nas:
        nas = input("NAS路径 (如 \\\\192.168.1.203\\知末备份): ").strip().strip('"')
        os.environ["NAS_BASE"] = nas
        # 重新设置模块级变量
        globals()["NAS_BASE"] = nas

    print(f"NAS: {nas}")
    print("启动 Web 服务 http://0.0.0.0:8080 ...")
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

- [ ] **步骤 2：安装依赖**

```bash
pip install fastapi uvicorn pillow
```

- [ ] **步骤 3：验证服务器能启动（不连 NAS，只测导入和路由注册）**

```
python -c "from step06_web_server import app; print('FastAPI app OK, routes:', len(app.routes))"
```

预期：打印路由数量，无报错

- [ ] **步骤 4：Commit**

```bash
git add rag/260519_第二版/step06_web_server.py
git commit -m "feat: step06 FastAPI web server with search/browse/img/download APIs"
```

---

### 任务 3：创建前端 HTML

**文件：** 创建 `rag/260519_第二版/static/index.html`

- [ ] **步骤 1：编写单页 HTML**

设计要点：
- 顶部搜索栏
- 分组标签栏（横向滚动）
- 瀑布流卡片（CSS columns）
- 详情弹窗（modal）
- 响应式：桌面 4-5 列，平板 2-3 列
- 颜色方案：深色标题栏 + 浅灰背景

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>景观搜图 v2</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; background: #1a1a2e; color: #eee; min-height: 100vh; }

/* 顶部 */
.header { position: sticky; top: 0; z-index: 100; background: #16213e; padding: 12px 20px; box-shadow: 0 2px 10px rgba(0,0,0,.4); }
.header h1 { font-size: 20px; margin-bottom: 10px; color: #e94560; }
.search-bar { display: flex; gap: 8px; }
.search-bar input { flex: 1; padding: 10px 16px; border: none; border-radius: 6px; font-size: 15px; background: #0f3460; color: #eee; outline: none; }
.search-bar input:focus { box-shadow: 0 0 0 2px #e94560; }
.search-bar button { padding: 10px 20px; border: none; border-radius: 6px; background: #e94560; color: #fff; font-size: 15px; cursor: pointer; }
.search-bar button:hover { background: #d63850; }

/* 分组标签 */
.tabs { display: flex; gap: 6px; padding: 12px 20px; overflow-x: auto; background: #16213e; border-top: 1px solid #0f3460; }
.tab { padding: 6px 16px; border-radius: 20px; border: 1px solid #0f3460; background: transparent; color: #aaa; cursor: pointer; white-space: nowrap; font-size: 14px; }
.tab:hover { border-color: #e94560; color: #e94560; }
.tab.active { background: #e94560; color: #fff; border-color: #e94560; }

/* 结果信息 */
.info-bar { padding: 8px 20px; font-size: 13px; color: #888; }

/* 瀑布流 */
.gallery { columns: 5 220px; column-gap: 12px; padding: 0 20px 20px; }
.card { break-inside: avoid; margin-bottom: 12px; border-radius: 8px; overflow: hidden; background: #16213e; cursor: pointer; transition: transform .15s; }
.card:hover { transform: scale(1.02); }
.card img { width: 100%; display: block; }
.card-body { padding: 8px 10px; }
.card-body .fn { font-size: 13px; line-height: 1.3; word-break: break-all; }
.card-body .style-tag { display: inline-block; margin-top: 4px; padding: 2px 8px; border-radius: 4px; font-size: 11px; background: #0f3460; color: #aaa; }
.card-body .hit-tags { margin-top: 4px; display: flex; gap: 4px; flex-wrap: wrap; }
.hit-tag { padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: bold; }
.hit-tag.fn-hit { background: #e94560; color: #fff; }
.hit-tag.tag-hit { background: #e9a645; color: #1a1a2e; }

/* Modal */
.modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.8); z-index: 200; justify-content: center; align-items: center; }
.modal-overlay.active { display: flex; }
.modal { background: #16213e; border-radius: 12px; max-width: 900px; width: 95%; max-height: 90vh; overflow-y: auto; }
.modal img { width: 100%; border-radius: 12px 12px 0 0; }
.modal-body { padding: 20px; }
.modal-body h2 { font-size: 18px; margin-bottom: 12px; }
.modal-body .meta { font-size: 13px; color: #aaa; margin-bottom: 8px; }
.modal-body .tags-full { font-size: 13px; line-height: 1.6; white-space: pre-wrap; background: #0f3460; padding: 12px; border-radius: 6px; margin: 12px 0; }
.modal-close { float: right; background: none; border: none; color: #aaa; font-size: 24px; cursor: pointer; }
.modal-close:hover { color: #fff; }
.btn-download { display: inline-block; padding: 10px 24px; background: #e94560; color: #fff; border-radius: 6px; text-decoration: none; font-size: 14px; margin-top: 8px; }
.btn-download:hover { background: #d63850; }

/* loading */
.loading { text-align: center; padding: 40px; color: #888; }
.loading::after { content: "..."; animation: dots 1.5s steps(4) infinite; }
@keyframes dots { 0%,20% { content: "."; } 40% { content: ".."; } 60%,100% { content: "..."; } }

@media (max-width: 768px) { .gallery { columns: 2 160px; } }
</style>
</head>
<body>

<div class="header">
  <h1>景观搜图 v2</h1>
  <div class="search-bar">
    <input type="text" id="searchInput" placeholder="搜索景观模型，如：中式凉亭水景、现代商业广场..." autofocus>
    <button onclick="doSearch()">搜索</button>
  </div>
</div>

<div class="tabs" id="tabs"></div>
<div class="info-bar" id="infoBar"></div>
<div class="gallery" id="gallery"></div>

<div class="modal-overlay" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal" id="modal"></div>
</div>

<script>
// ===== 状态 =====
let currentGroup = null;
let currentMode = 'browse'; // 'browse' | 'search'
let currentQuery = '';

// ===== 初始化 =====
async function init() {
  const r = await fetch('/api/groups');
  const data = await r.json();
  const tabs = document.getElementById('tabs');

  data.groups.forEach(g => {
    const btn = document.createElement('button');
    btn.className = 'tab';
    btn.textContent = g;
    btn.onclick = () => browseGroup(g);
    tabs.appendChild(btn);
  });

  if (data.groups.length > 0) {
    browseGroup(data.groups[data.groups.length - 1]); // 默认最新
  }
}

// ===== 浏览分组 =====
async function browseGroup(group) {
  currentMode = 'browse';
  currentGroup = group;
  document.getElementById('searchInput').value = '';

  // tab active
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  Array.from(document.querySelectorAll('.tab')).find(t => t.textContent === group)?.classList.add('active');

  document.getElementById('gallery').innerHTML = '<div class="loading">加载中</div>';
  document.getElementById('infoBar').textContent = '';

  const r = await fetch(`/api/browse?group=${encodeURIComponent(group)}&page_size=200`);
  const data = await r.json();
  document.getElementById('infoBar').textContent = `${group} · ${data.total} 个模型`;
  renderCards(data.items);
}

// ===== 搜索 =====
async function doSearch() {
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;

  currentMode = 'search';
  currentQuery = q;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('gallery').innerHTML = '<div class="loading">搜索中</div>';

  const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
  const data = await r.json();
  document.getElementById('infoBar').textContent =
    `${data.n_hit} 命中 + ${data.n_semantic} 语义 · 风格: ${data.style || '未识别'} · 关键词: ${data.keywords.join(', ')}`;
  renderCards(data.results);
}

// ===== 渲染卡片 =====
function renderCards(items) {
  const gallery = document.getElementById('gallery');
  if (items.length === 0) {
    gallery.innerHTML = '<div class="loading">无结果</div>';
    return;
  }

  gallery.innerHTML = items.map(item => {
    const imgSrc = `/img/${encodeURIComponent(item.path)}/${encodeURIComponent(item.filename)}.jpg?w=400`;
    const styleMatch = item.tags?.match(/风格:([^|]*)/);
    const style = styleMatch ? styleMatch[1].trim() : '';

    let hitTags = '';
    if (item.fn_hit) hitTags += '<span class="hit-tag fn-hit">文件名</span>';
    if (item.tag_hit) hitTags += '<span class="hit-tag tag-hit">标签</span>';

    return `
    <div class="card" onclick="openDetail('${escAttr(item.path)}', '${escAttr(item.filename)}')">
      <img src="${imgSrc}" alt="${escAttr(item.filename)}" loading="lazy">
      <div class="card-body">
        <div class="fn">${escHtml(item.filename)}</div>
        ${style ? `<span class="style-tag">${escHtml(style)}</span>` : ''}
        ${hitTags ? `<div class="hit-tags">${hitTags}</div>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ===== 详情弹窗 =====
async function openDetail(path, filename) {
  const overlay = document.getElementById('modalOverlay');
  const modal = document.getElementById('modal');
  modal.innerHTML = '<div class="loading">加载中</div>';
  overlay.classList.add('active');

  const r = await fetch(`/api/detail?path=${encodeURIComponent(path)}&filename=${encodeURIComponent(filename)}`);
  const item = await r.json();
  const imgSrc = `/img/${encodeURIComponent(path)}/${encodeURIComponent(filename)}.jpg?w=800`;
  const dlHref = `/download/${encodeURIComponent(path)}/${encodeURIComponent(filename)}.zip`;

  modal.innerHTML = `
    <button class="modal-close" onclick="closeModal()">&times;</button>
    <img src="${imgSrc}" alt="${escAttr(filename)}">
    <div class="modal-body">
      <h2>${escHtml(filename)}</h2>
      <div class="meta">路径: ${escHtml(path)}</div>
      <div class="tags-full">${escHtml(item.tags || '无标签')}</div>
      ${item.zip_exists
        ? `<a class="btn-download" href="${dlHref}" download>下载 ZIP 模型</a>`
        : '<div class="meta" style="color:#e94560">ZIP 文件不存在</div>'}
    </div>
  `;
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('active');
}

// ===== 搜索框回车 =====
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('searchInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') doSearch();
  });
  init();
});

// ===== 工具函数 =====
function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
function escAttr(s) {
  return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
</script>

</body>
</html>
```

- [ ] **步骤 2：创建 static 目录**

```bash
mkdir rag\260519_第二版\static
```

然后将上述 HTML 写入 `rag/260519_第二版/static/index.html`

- [ ] **步骤 3：验证 HTML 可被 FastAPI 正确返回**

```bash
python -c "
import sys; sys.path.insert(0, 'rag/260519_第二版')
from step06_web_server import app
from fastapi.testclient import TestClient
client = TestClient(app)
# 仅测试静态文件返回（不依赖 NAS / Searcher 初始化）
"
```

注意：此步骤会因为 Searcher 初始化失败（无 RAGFlow/Xinference 连接），需要改为 lazy init 模式。改为：

```python
# step06_web_server.py 中，Searcher 改为懒初始化
searcher = None  # 延迟初始化

def get_searcher():
    global searcher
    if searcher is None:
        searcher = Searcher(...)
        searcher.initialize()
    return searcher
```

- [ ] **步骤 4：Commit**

```bash
git add rag/260519_第二版/step06_web_server.py rag/260519_第二版/static/
git commit -m "feat: add static frontend with waterfall gallery, search, and detail modal"
```

---

### 任务 4：端到端验证

- [ ] **步骤 1：更新 step06 为懒加载模式后验证启动流程**

修改 `step06_web_server.py`：Searcher 改为在第一个请求时才初始化，或保持在 `on_event("startup")` 但要求服务已启动。

最终启动命令：

```bash
cd rag/260519_第二版
set NAS_BASE=\\192.168.1.203\知末备份
python step06_web_server.py
```

预期输出：
```
加载 RAGFlow 文档...
  xxx: 340 chunks
  ...
总计 1360 条文档
embedding 1360 条文档 (bge-m3)...
就绪，矩阵 (1360, 1024)，耗时 X.Xs
NAS: \\192.168.1.203\知末备份
启动 Web 服务 http://0.0.0.0:8080 ...
```

- [ ] **步骤 2：浏览器测试清单**

| 测试项 | 操作 | 预期 |
|--------|------|------|
| 首页加载 | 打开 http://127.0.0.1:8080 | 分组标签 + 瀑布流卡片 |
| 分组切换 | 点击不同分组标签 | 瀑布流刷新，显示对应分组 |
| 图片加载 | 查看瀑布流 | 每张卡片显示缩略图 |
| 搜索 | 输入"中式凉亭"搜索 | 结果按打分排序，显示命中标记 |
| 详情弹窗 | 点击卡片 | 大图 + 完整标签 + 下载按钮 |
| 下载 | 点击下载按钮 | 触发 ZIP 下载 |
| 空搜索 | 不输入直接搜索 | 不应发送请求 |

- [ ] **步骤 3：Commit（如有修复）**

---

## 验收标准

1. 浏览器打开 `http://127.0.0.1:8080` 能看到分组瀑布流
2. 搜索结果能展示命中标记（文件名/标签）
3. 点击卡片能看到详情弹窗
4. 下载按钮能下载 ZIP
5. 微信 Bot 二期不做，但 `/api/search` 已就绪
