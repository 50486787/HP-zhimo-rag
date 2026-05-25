"""step05: 测试 v2 搜索 — bge-m3 基础分 + 分层加权"""
import sys, os, json, re, time
import numpy as np
import requests
from openai import OpenAI

# ================= 配置 =================
kimi_client = OpenAI(
    api_key="sk-g9ys55fFGKkJTsuTkEYhjC5sqEY83HfUyxRmVe1pm8QJmnN3",
    base_url="https://api.moonshot.cn/v1",
)
bge_client = OpenAI(
    api_key="not-needed",
    base_url="http://127.0.0.1:9997/v1",
)

RAGFLOW_API_KEY = "ragflow-GELaRwRQXrL4oNxlciCEtmXJUUhj_9Ma0UwT38Yn1xU"
RAGFLOW_DATASET_IDS = ["ae52896e580711f1ba3a0fbde202da50"]
RAGFLOW_URL = "http://127.0.0.1:9900/api/v1/retrieval"

STYLES = ["现代", "新中式", "中式", "轻奢", "极简", "古风", "意式", "欧式", "日式", "热带度假风", "禅意", "赛博朋克", "宋式", "田园", "卡通风", "工业风"]


# ================= 词表 & 翻译 =================
def load_top500(path):
    with open(path, encoding="utf-8-sig") as f:
        return [item["词"] for item in json.load(f)]


def build_translate_prompt(top_words):
    word_list = ", ".join(top_words)
    return f"""# Role
你是景观图库检索翻译官。将用户口语转化为精确搜索关键词。

# 受控词表（优先使用）
{word_list}

# 任务
1. 识别用户意图中的风格（限：{", ".join(STYLES)}），未提及则留空，不要猜测
2. 提取核心构筑元素——优先从受控词表中选词，但必须保留用户原话中的实体词
3. 提取植物类型，保留品种名
4. 提取材质类型
5. 保留用户原话的视觉特征词

# 重要规则
- keywords 必须包含用户原话的全部核心实体词，不能丢失或替换
- vector_query 用 keywords 拼接生成，不要添加用户未提及的风格/材质/植物等无关维度
- 复合词必须拆分：如"奶油风格"→拆成"奶油"+"风格"，"现代简约"→"现代"+"简约"，修饰词和名词分开

# Output JSON
{{{{
  "style": "风格 或 空",
  "keywords": ["核心词1", "核心词2", ...],
  "vector_query": "keywords用空格拼接"
}}}}
"""


def query_translate(user_input):
    completion = kimi_client.chat.completions.create(
        model="kimi-k2.5",
        messages=[
            {"role": "system", "content": TRANSLATE_PROMPT},
            {"role": "user", "content": f"用户输入：{user_input}"}
        ],
        temperature=0.6,
        extra_body={"thinking": {"type": "disabled"}},
    )
    content = completion.choices[0].message.content
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return {"style": "", "keywords": [user_input], "vector_query": user_input}


# ================= RAGFlow 加载 =================
def parse_ragflow_content(raw):
    """解析 RAGFlow Table 模式的 path / filename / content 三列"""
    path = ""
    filename = ""
    content = raw
    m_path = re.search(r'- .?path:\s*(.+)', raw)
    m_fn = re.search(r'- filename:\s*(.+)', raw)
    m_ct = re.search(r'- content:\s*(.+)', raw, re.DOTALL)
    if m_path:
        path = m_path.group(1).strip()
    if m_fn:
        filename = m_fn.group(1).strip()
    if m_ct:
        content = m_ct.group(1).strip()
    return path, filename, content


def load_docs_from_ragflow():
    """从 RAGFlow 拉全量文档（chunks 列表 API，不走检索过滤）"""
    headers = {"Authorization": f"Bearer {RAGFLOW_API_KEY}"}
    all_chunks = []

    for ds_id in RAGFLOW_DATASET_IDS:
        # 1. 列出 dataset 下的所有文档
        r = requests.get(
            f"http://127.0.0.1:9900/api/v1/datasets/{ds_id}/documents",
            headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"  获取 dataset {ds_id} 文档列表失败: {r.status_code}")
            continue
        docs = r.json().get("data", {}).get("docs", [])

        # 2. 遍历每个文档，拉全部 chunks
        for doc in docs:
            doc_id = doc["id"]
            doc_name = doc["name"]
            chunk_count = doc["chunk_count"]
            r2 = requests.get(
                f"http://127.0.0.1:9900/api/v1/datasets/{ds_id}/documents/{doc_id}/chunks",
                params={"page": 1, "page_size": chunk_count + 100},
                headers=headers, timeout=30)
            if r2.status_code != 200:
                print(f"  获取 {doc_name} chunks 失败: {r2.status_code}")
                continue
            chunks = r2.json().get("data", {}).get("chunks", [])
            all_chunks.extend(chunks)
            print(f"  {doc_name}: {len(chunks)} chunks")

    print(f"RAGFlow 总计 {len(all_chunks)} 条文档")
    return all_chunks


# ================= Embedding =================
def embed_batch(texts):
    """bge-m3 批量 embedding"""
    resp = bge_client.embeddings.create(model="bge-m3", input=texts)
    return np.array([d.embedding for d in resp.data], dtype=np.float32)


def normalize(vecs):
    """L2 归一化，cosine = dot(norm_q, norm_d)"""
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1e-10
    return vecs / norms


# ================= 打分 =================
def keyword_match_score(keywords, text, weight):
    """每个 ≥2 字的关键词命中 text 则 +weight。
    若 text 含 _ 则按 _ 分词后逐段匹配，提高精确度。"""
    score = 0
    segments = text.split("_") if "_" in text else [text]
    for kw in keywords:
        if len(kw) < 2:
            continue
        for seg in segments:
            if kw in seg:
                score += weight
                break  # 每关键词最多命中一次
    return score


def search(query_vec, doc_matrix, doc_paths, doc_filenames, doc_tags_list, keywords, nas_base, max_semantic=5):
    """分层打分搜索。
    有文件名/标签命中的全部显示，纯语义最多显示 max_semantic 个。"""
    q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    base_scores = (doc_matrix @ q_norm) * 100

    hits = []     # 有命中（文件名或标签）
    semantic = []  # 纯语义（无命中）

    for i in range(len(doc_paths)):
        fn_score = keyword_match_score(keywords, doc_filenames[i], 300)
        tag_score = keyword_match_score(keywords, doc_tags_list[i], 100)
        score = float(base_scores[i]) + fn_score + tag_score

        item = {
            "idx": i,
            "path": doc_paths[i],
            "filename": doc_filenames[i],
            "tags": doc_tags_list[i],
            "base_score": round(float(base_scores[i]), 1),
            "fn_hit": fn_score > 0,
            "tag_hit": tag_score > 0,
            "final_score": round(score, 1),
        }
        if fn_score > 0 or tag_score > 0:
            hits.append(item)
        else:
            semantic.append(item)

    hits.sort(key=lambda x: x["final_score"], reverse=True)
    semantic.sort(key=lambda x: x["final_score"], reverse=True)

    return hits + semantic[:max_semantic]


def show_results(results, nas_base):
    n_hit = sum(1 for r in results if r.get("fn_hit") or r.get("tag_hit"))
    n_semantic = len(results) - n_hit
    print(f"\n{'='*60}")
    print(f"结果: {n_hit} 命中 + {n_semantic} 语义 | 共 {len(results)} 条")
    print(f"{'='*60}")
    for rank, r in enumerate(results, 1):
        fn = r["filename"]
        path = r["path"]
        style = "?"
        m = re.search(r'风格:([^|]*)', r["tags"])
        if m:
            style = m.group(1).strip()
        desc = r["tags"].split("|")[-1].strip()[:80]

        # 命中标记
        hit_mark = ""
        if r.get("fn_hit"):
            hit_mark += "[文件名命中]"
        if r.get("tag_hit"):
            hit_mark += "[标签命中]"

        print(f"[{rank}] {hit_mark} {fn}")
        print(f"    路径={path} | 风格={style} | 基础={r['base_score']} 最终={r['final_score']}")
        print(f"    {desc}")
        if fn != "未知":
            print(f"    预览: {os.path.join(nas_base, path, fn + '.jpg')}")
            print(f"    模型: {os.path.join(nas_base, path, fn + '.zip')}")
        print()


# ================= Main =================
if __name__ == "__main__":
    # 加载词表
    word_list_path = os.path.join(os.path.dirname(__file__), "知末粗标", "规范词频_Top500.json")
    if not os.path.exists(word_list_path):
        word_list_path = input("Top500词表路径: ").strip().strip('"').strip("'")
    top_words = load_top500(word_list_path)
    TRANSLATE_PROMPT = build_translate_prompt(top_words)
    print(f"词表: {len(top_words)} 词")

    # 启动加载
    print("加载 RAGFlow 文档...")
    t0 = time.time()
    chunks = load_docs_from_ragflow()
    if not chunks:
        print("无文档，退出")
        sys.exit(1)

    doc_paths = []
    doc_filenames = []
    doc_tags_list = []
    doc_texts = []

    for c in chunks:
        raw = c.get("content_with_weight", c.get("content", ""))
        path, filename, tags = parse_ragflow_content(raw)
        doc_paths.append(path)
        doc_filenames.append(filename)
        doc_tags_list.append(tags)
        doc_texts.append(f"{filename} {tags}")

    print(f"embedding {len(doc_texts)} 条文档 (bge-m3)...")
    doc_embeddings = embed_batch(doc_texts)
    doc_embeddings = normalize(doc_embeddings)
    print(f"加载完成，矩阵 {doc_embeddings.shape}，耗时 {time.time() - t0:.1f}s")

    nas_base = input("NAS路径 (如 \\\\192.168.1.203\\知末备份): ").strip().strip('"').strip("'")
    print(f"NAS: {nas_base}")
    print("输入 'q' 退出\n")

    while True:
        query = input("搜索: ").strip()
        if query.lower() == 'q':
            break
        if not query:
            continue

        # 1. Kimi 翻译
        trans = query_translate(query)
        keywords = trans.get("keywords", [])
        vq = trans.get("vector_query", query)
        print(f"  翻译: style={trans.get('style','')} keywords={keywords}")
        print(f"  vector_query: {vq}")

        # 2. bge-m3 embed + 打分
        q_vec = embed_batch([vq])
        q_vec = normalize(q_vec)
        results = search(q_vec[0], doc_embeddings, doc_paths, doc_filenames, doc_tags_list, keywords, nas_base)
        show_results(results, nas_base)
