"""search_core.py - v2 景观搜图系统共享搜索模块
从 step05_search.py 提取核心逻辑，供 CLI (step05) 和 Web (step06) 共用
"""
import sys, json, re, time
import numpy as np
import requests
from openai import OpenAI

# ================= 配置常量 =================
STYLES = ["现代", "新中式", "中式", "轻奢", "极简", "古风", "意式", "欧式", "日式", "热带度假风", "禅意", "赛博朋克", "宋式", "田园", "卡通风", "工业风"]


class Searcher:
    """v2 景观搜图系统核心搜索引擎

    启动流程:
      searcher = Searcher(vocab_path, ragflow_key, ragflow_ds_ids, ragflow_base,
                          kimi_api_key, kimi_base_url, bge_base_url)
      searcher.initialize()  # 加载文档 + embedding
      results = searcher.search("中式庭院")  # 查询
    """

    def __init__(self, vocab_path, ragflow_key, ragflow_ds_ids, ragflow_base,
                 kimi_api_key, kimi_base_url, bge_base_url):
        # 加载词表
        self.vocab = self._load_vocab(vocab_path)
        # 构建翻译 prompt
        self.translate_prompt = self._make_translate_prompt(self.vocab)
        # 初始化客户端
        self.kimi_client = OpenAI(api_key=kimi_api_key, base_url=kimi_base_url)
        self.bge_client = OpenAI(api_key="not-needed", base_url=bge_base_url)
        # RAGFlow 配置
        self.ragflow_key = ragflow_key
        self.ragflow_ds_ids = ragflow_ds_ids
        self.ragflow_base = ragflow_base
        # 文档数据 (initialize() 后填充)
        self.ready = False
        self.doc_matrix = None
        self.doc_paths = []
        self.doc_fns = []
        self.doc_tags_list = []

    # ================= 词表 & 翻译 =================

    def _load_vocab(self, path):
        with open(path, encoding="utf-8-sig") as f:
            return [item["词"] for item in json.load(f)]

    def _make_translate_prompt(self, vocab):
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

    def translate(self, query):
        """调用 Kimi 翻译口语→keywords + vector_query"""
        completion = self.kimi_client.chat.completions.create(
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

    # ================= RAGFlow 加载 =================

    @staticmethod
    def _parse_chunk(raw):
        """解析 RAGFlow Table 模式的 path / filename / content 三列"""
        m_path = re.search(r'- .?path:\s*(.+)', raw)
        m_fn = re.search(r'- filename:\s*(.+)', raw)
        m_ct = re.search(r'- content:\s*(.+)', raw, re.DOTALL)
        return (
            m_path.group(1).strip() if m_path else "",
            m_fn.group(1).strip() if m_fn else "",
            m_ct.group(1).strip() if m_ct else raw,
        )

    def load_docs(self):
        """从 RAGFlow chunks 列表 API 拉全量文档（不走检索，无需 question）"""
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

    # ================= Embedding =================

    def _embed(self, texts, batch_size=200):
        """bge-m3 embedding，分批次避免超时（httpx 与 Xinference 代理不兼容）"""
        all_embeddings = []
        base_url = str(self.bge_client.base_url).rstrip("/")
        url = f"{base_url}/embeddings"
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            resp = requests.post(url, json={"model": "bge-m3", "input": batch}, timeout=120)
            if resp.status_code != 200:
                raise RuntimeError(f"Embedding 失败: {resp.status_code} {resp.text[:200]}")
            all_embeddings.extend(d["embedding"] for d in resp.json()["data"])
        return np.array(all_embeddings, dtype=np.float32)

    @staticmethod
    def _normalize(vecs):
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1e-10
        return vecs / norms

    # ================= 初始化 =================

    def initialize(self):
        """加载文档 + bge-m3 embed + normalize → 存内存"""
        t0 = time.time()
        print("加载 RAGFlow 文档...")
        chunks = self.load_docs()
        if not chunks:
            raise RuntimeError("无文档，无法初始化搜索引擎")

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

    # ================= 分组 =================

    @staticmethod
    def _clean_group(path):
        """去掉用于显示的 P 前缀"""
        return path[1:] if path.startswith("P") else path

    def get_groups(self):
        """返回 path 去重排序列表（去掉 P 前缀）"""
        return sorted(set(self._clean_group(p) for p in self.doc_paths))

    def get_group_items(self, group):
        """返回某分组下的索引列表，兼容 P 前缀和无前缀"""
        return [i for i, p in enumerate(self.doc_paths)
                if p == group or self._clean_group(p) == group]

    # ================= 打分 & 搜索 =================

    @staticmethod
    def _kw_score(keywords, text, weight):
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

    def search(self, query):
        """Kimi翻译 → embed query → 矩阵点积 → 分层加权 → 返回结果

        Returns:
            dict: {style, keywords, vector_query, results, total, n_hit, n_semantic}
            其中 results 每项含 filename, path, tags, base_score, final_score, fn_hit, tag_hit
        """
        if not self.ready:
            raise RuntimeError("Searcher 未初始化，请先调用 initialize()")

        # 1. 翻译
        trans = self.translate(query)
        keywords = trans.get("keywords", [])
        vq = trans.get("vector_query", query)
        style = trans.get("style", "")

        # 2. embed & normalize query
        q_vec = self._embed([vq])
        q_vec = self._normalize(q_vec)

        # 3. 矩阵点积 + 分层加权
        q_norm = q_vec[0] / (np.linalg.norm(q_vec[0]) + 1e-10)
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
        results = hits + semantic[:5]

        n_hit = len(hits)
        n_semantic = min(len(semantic), 5)

        return {
            "style": style,
            "keywords": keywords,
            "vector_query": vq,
            "results": results,
            "total": len(results),
            "n_hit": n_hit,
            "n_semantic": n_semantic,
        }
