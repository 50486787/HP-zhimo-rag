"""step05: v2 搜索 — bge-m3 全量打分 + 分层加权"""
import sys, os, json, re, time
import numpy as np
import requests
from openai import OpenAI

# ================= 配置 =================
KIMI = None
MODEL = "kimi-k2.5"
TRANS_TEMP = 0.6
TRANS_THINKING = False
BGE = OpenAI(
    api_key="not-needed",
    base_url="http://127.0.0.1:9997/v1",
)

RAGFLOW_KEY = ""
RAGFLOW_DS = []
RAGFLOW_BASE = "http://127.0.0.1:9900/api/v1"

STYLES = ["现代", "新中式", "中式", "轻奢", "极简", "古风", "意式", "欧式", "日式", "热带度假风", "禅意", "赛博朋克", "宋式", "田园", "卡通风", "工业风"]


# ================= 词表 & 翻译 =================
def load_vocab(path):
    with open(path, encoding="utf-8-sig") as f:
        return [item["词"] for item in json.load(f)]


def make_translate_prompt(vocab):
    words = ", ".join(vocab)
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


def translate(query):
    completion = KIMI.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": TRANSLATE_PROMPT},
            {"role": "user", "content": f"用户输入：{query}"}
        ],
        temperature=TRANS_TEMP,
        extra_body={"thinking": {"type": "enabled"}} if TRANS_THINKING else {"thinking": {"type": "disabled"}},
    )
    content = completion.choices[0].message.content
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return {"style": "", "keywords": [query], "vector_query": query}


# ================= RAGFlow 加载 =================
def parse_chunk(raw):
    """解析 RAGFlow Table 模式的 path / filename / content 三列"""
    m_path = re.search(r'- .?path:\s*(.+)', raw)
    m_fn = re.search(r'- filename:\s*(.+)', raw)
    m_ct = re.search(r'- content:\s*(.+)', raw, re.DOTALL)
    return (
        m_path.group(1).strip() if m_path else "",
        m_fn.group(1).strip() if m_fn else "",
        m_ct.group(1).strip() if m_ct else raw,
    )


def load_all_docs():
    """从 RAGFlow chunks 列表 API 拉全量文档（不走检索，无需 question）"""
    headers = {"Authorization": f"Bearer {RAGFLOW_KEY}"}
    all_chunks = []

    for ds_id in RAGFLOW_DS:
        r = requests.get(f"{RAGFLOW_BASE}/datasets/{ds_id}/documents", headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"  [WARN] dataset {ds_id} 获取失败: {r.status_code}")
            continue
        for doc in r.json()["data"]["docs"]:
            doc_id = doc["id"]
            chunk_count = doc["chunk_count"]
            r2 = requests.get(
                f"{RAGFLOW_BASE}/datasets/{ds_id}/documents/{doc_id}/chunks",
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


# ================= Embedding =================
def embed(texts):
    resp = BGE.embeddings.create(model="bge-m3", input=texts)
    return np.array([d.embedding for d in resp.data], dtype=np.float32)


def normalize(vecs):
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1e-10
    return vecs / norms


# ================= 打分 & 搜索 =================
def kw_score(keywords, text, weight):
    """按 _ 分词后逐段匹配，每个 keyword 最多命中一次"""
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


def search(query_vec, doc_matrix, doc_fns, doc_tags_list, keywords, nas_base):
    q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    base = (doc_matrix @ q_norm) * 100

    hits, semantic = [], []
    for i in range(len(doc_fns)):
        fn_score = kw_score(keywords, doc_fns[i], 300)
        tag_score = kw_score(keywords, doc_tags_list[i], 100)
        score = float(base[i]) + fn_score + tag_score

        item = {
            "filename": doc_fns[i],
            "path": doc_paths[i],
            "tags": doc_tags_list[i],
            "base_score": round(float(base[i]), 1),
            "final_score": round(score, 1),
            "fn_hit": fn_score > 0,
            "tag_hit": tag_score > 0,
        }
        (hits if (fn_score + tag_score > 0) else semantic).append(item)

    hits.sort(key=lambda x: x["final_score"], reverse=True)
    semantic.sort(key=lambda x: x["final_score"], reverse=True)
    return hits + semantic[:5]


# ================= 展示 =================
def display(results, nas_base, page_size=30):
    n_hit = sum(1 for r in results if r["fn_hit"] or r["tag_hit"])
    n_sem = len(results) - n_hit
    total = len(results)

    for start in range(0, total, page_size):
        batch = results[start:start + page_size]
        end = min(start + page_size, total)
        print(f"\n{'='*60}")
        print(f"{n_hit} 命中 + {n_sem} 语义 | {start+1}-{end}/{total}")
        print(f"{'='*60}")

        for rank, r in enumerate(batch, start + 1):
            style_m = re.search(r'风格:([^|]*)', r["tags"])
            style = style_m.group(1).strip() if style_m else "?"
            desc = r["tags"].split("|")[-1].strip()[:80]
            marks = ""
            if r["fn_hit"]:
                marks += "[文件名]"
            if r["tag_hit"]:
                marks += "[标签]"

            print(f"[{rank}] {marks} {r['filename']}")
            print(f"    路径={r['path']} | 风格={style} | 基础={r['base_score']} 最终={r['final_score']}")
            print(f"    {desc}")
            if r["filename"] != "未知":
                real = r['path'][1:] if r['path'].startswith('P') else r['path']
                real = real.replace('_', 'os.sep')
                print(f"    预览: {os.path.join(nas_base, real, r['filename'] + '.jpg')}")
            print()

        if end < total:
            cmd = input(f"[{end}/{total}] 回车继续，q 退出: ").strip().lower()
            if cmd == 'q':
                break


# ================= Main =================
def run(vocab_path=None, nas_base=None, ragflow_base=None, ragflow_key=None,
        ragflow_ds=None, bge_base=None, kimi_cfg=None):
    """快速验证搜索管线：加载文档 → embedding → 返回状态"""
    try:
        if vocab_path is None:
            vocab_path = os.path.join(os.path.dirname(__file__), "知末粗标", "规范词频_Top500.json")
        if not os.path.exists(vocab_path):
            return False, f"词表不存在: {vocab_path}", None

        global RAGFLOW_BASE, RAGFLOW_KEY, RAGFLOW_DS, BGE, KIMI
        if ragflow_base:
            RAGFLOW_BASE = ragflow_base
        if ragflow_key:
            RAGFLOW_KEY = ragflow_key
        if ragflow_ds:
            RAGFLOW_DS = ragflow_ds if isinstance(ragflow_ds, list) else [ragflow_ds]
        if bge_base:
            BGE = OpenAI(api_key="not-needed", base_url=bge_base)
        if kimi_cfg:
            global MODEL, TRANS_TEMP, TRANS_THINKING
            KIMI = OpenAI(
                api_key=kimi_cfg.get("api_key", ""),
                base_url=kimi_cfg.get("base_url", ""),
                timeout=120.0,
            )
            MODEL = kimi_cfg.get("model", "kimi-k2.5")
            TRANS_TEMP = kimi_cfg.get("temperature", 0.6)
            TRANS_THINKING = kimi_cfg.get("thinking", False)

        vocab = load_vocab(vocab_path)
        chunks = load_all_docs()
        if not chunks:
            return False, "RAGFlow 无文档", None

        doc_texts = []
        for c in chunks:
            raw = c.get("content_with_weight", c.get("content", ""))
            path, fn, tags = parse_chunk(raw)
            doc_texts.append(f"{fn} {tags}")

        doc_mat = embed(doc_texts)
        doc_mat = normalize(doc_mat)
        return True, f"验证通过: {len(chunks)} 条文档, 矩阵 {doc_mat.shape}", None
    except Exception as e:
        return False, f"验证失败: {e}", None


def check_done(**paths):
    """检测搜索是否可用：词表 + RAGFlow 连通"""
    vocab_path = paths.get("vocab", "")
    if not vocab_path:
        vocab_path = os.path.join(os.path.dirname(__file__), "知末粗标", "规范词频_Top500.json")
    if not os.path.exists(vocab_path):
        return False, "词表不存在"
    try:
        r = requests.get(f"{RAGFLOW_BASE}/datasets", headers={"Authorization": f"Bearer {RAGFLOW_KEY}"}, timeout=5)
        if r.status_code == 200:
            return True, "RAGFlow 连通，词表就绪"
    except Exception:
        pass
    return False, "RAGFlow 不可达"


if __name__ == "__main__":
    vocab_path = os.path.join(os.path.dirname(__file__), "知末粗标", "规范词频_Top500.json")
    if not os.path.exists(vocab_path):
        vocab_path = input("Top500词表路径: ").strip().strip('"')
    vocab = load_vocab(vocab_path)
    TRANSLATE_PROMPT = make_translate_prompt(vocab)
    print(f"词表: {len(vocab)} 词")

    # 启动加载
    print("加载 RAGFlow 文档...")
    t0 = time.time()
    chunks = load_all_docs()
    if not chunks:
        print("无文档，退出")
        sys.exit(1)

    doc_paths, doc_fns, doc_tags_list, doc_texts = [], [], [], []
    for c in chunks:
        raw = c.get("content_with_weight", c.get("content", ""))
        path, fn, tags = parse_chunk(raw)
        doc_paths.append(path)
        doc_fns.append(fn)
        doc_tags_list.append(tags)
        doc_texts.append(f"{fn} {tags}")

    print(f"embedding {len(doc_texts)} 条文档 (bge-m3)...")
    doc_mat = embed(doc_texts)
    doc_mat = normalize(doc_mat)
    print(f"就绪，矩阵 {doc_mat.shape}，耗时 {time.time() - t0:.1f}s")

    nas_base = input("NAS路径 (如 \\\\192.168.1.203\\知末备份): ").strip().strip('"')
    print(f"NAS: {nas_base}\n输入 'q' 退出\n")

    while True:
        query = input("搜索: ").strip()
        if query.lower() == 'q':
            break
        if not query:
            continue

        trans = translate(query)
        keywords = trans.get("keywords", [])
        vq = trans.get("vector_query", query)
        print(f"  翻译: style={trans.get('style','')} keywords={keywords}")

        q_vec = embed([vq])
        q_vec = normalize(q_vec)
        results = search(q_vec[0], doc_mat, doc_fns, doc_tags_list, keywords, nas_base)
        display(results, nas_base)
