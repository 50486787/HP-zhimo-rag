"""step02 — 标签同义词归并 + 词频统计，输出 Top500 规范词表"""
import os
import json
import re
import time
from collections import Counter
from pathlib import Path

MERGE_PROMPT = """# Role
你是景观建筑设计院的 3D 资产标注专家，正在整理 SU 模型的标签词表。

# 任务
下面的标签需要做层级归并：把过于具体的下层标签，用更通用的上层范畴词来替代。

# 归并原则
- 层级方向：具体词 → 范畴词（如"蕨类"→"蕨类植物"，"望柱"→"栏杆柱"）
- 只在同一语义维度内归并，跨维度不合并
- 如果原词本身已经是合适的层级，不需要归并，就不要列出来
- 拿不准的宁可不合并

# 输出格式
严格只输出 JSON，key 是原始词，value 是归并后的上层词。只列出需要归并的词：
{
  "蕨类": "蕨类植物",
  "望柱": "栏杆柱"
}
"""


def run(source_dir, kimi_config=None, merge_prompt=None):
    """从粗标结果提取标签，调 Kimi 做同义词归并，输出 Top500 词表

    Args:
        merge_prompt: 自定义归并提示词，None 则用默认
    """
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        return False, f"目录不存在: {source_dir}", None

    try:
        from openai import OpenAI
    except ImportError:
        return False, "缺少 openai 库: pip install openai", None

    # 1. 提取所有标签
    tag_freq = Counter()
    json_count = 0
    for json_path in source_dir.rglob("*.json"):
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            tags = data.get("tags", "")
            if isinstance(tags, list):
                tags = ", ".join(tags)
            for t in tags.split(","):
                t = t.strip()
                if t:
                    tag_freq[t] += 1
            json_count += 1
        except Exception:
            pass

    if not tag_freq:
        return False, f"未在 {source_dir} 中找到标签数据", None

    # 2. 调 Kimi 归并高频词（>=2次）
    high_freq = [(t, f) for t, f in tag_freq.most_common() if f >= 2]
    if not high_freq:
        return False, "没有频次>=2的标签需要归并", None

    client = OpenAI(
        api_key=kimi_config.get("api_key", ""),
        base_url=kimi_config.get("base_url", ""),
        timeout=120.0,
    )
    model = kimi_config.get("model", "kimi-k2.5")

    all_mappings = {}
    batch_size = 500
    batches = [high_freq[i:i + batch_size] for i in range(0, len(high_freq), batch_size)]

    for idx, batch in enumerate(batches):
        tag_text = "\n".join(f"- {t}: {f}" for t, f in batch)
        mapping = _call_kimi_merge(client, model, tag_text, merge_prompt,
                                   temperature=kimi_config.get("temperature", 0.6),
                                   thinking=kimi_config.get("thinking", False))
        if mapping:
            all_mappings.update(mapping)
        time.sleep(0.5)

    # 3. 统计规范词频
    canonical_freq = Counter()
    for tag, freq in tag_freq.items():
        canonical = all_mappings.get(tag, tag)
        canonical_freq[canonical] += freq

    # 4. 保存
    mapping_path = source_dir / "标签同义词映射.json"
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(all_mappings, f, ensure_ascii=False, indent=2)

    freq_data = [{"词": w, "频次": f} for w, f in canonical_freq.most_common()]
    top500 = freq_data[:500]
    top500_path = source_dir / "规范词频_Top500.json"
    with open(top500_path, "w", encoding="utf-8") as f:
        json.dump(top500, f, ensure_ascii=False, indent=2)

    msg = (f"归并完成: {json_count} 个JSON, 去重标签 {len(tag_freq)}, "
           f"归并映射 {len(all_mappings)} 条, 规范词 {len(canonical_freq)}")
    return True, msg, str(top500_path)


def _call_kimi_merge(client, model, tag_text, custom_prompt=None, temperature=0.6, thinking=False):
    prompt = custom_prompt or MERGE_PROMPT
    max_retries = 3
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"请处理以下标签：\n{tag_text}"},
                ],
                temperature=temperature,
                extra_body={"thinking": {"type": "enabled"}} if thinking else {"thinking": {"type": "disabled"}},
            )
            content = completion.choices[0].message.content.strip()
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            time.sleep(1)
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "overloaded" in error_msg.lower():
                time.sleep(5 * (attempt + 1))
            elif "content_filter" in error_msg.lower():
                return {}
            elif "timed out" in error_msg.lower():
                time.sleep(3)
            else:
                time.sleep(2)
    return {}


def check_done(**paths):
    raw_dir = paths.get("raw_label_dir", "")
    if raw_dir:
        top500 = os.path.join(raw_dir, "规范词频_Top500.json")
        if os.path.isfile(top500) and os.path.getsize(top500) > 10:
            return True, f"已生成: 规范词频_Top500.json"
    return False, "未找到规范词表"
