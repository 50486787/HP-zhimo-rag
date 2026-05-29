"""
阶段3：二次精标
用 Top 500 规范词表 + Few-shot 范例，按 4 维度精确标注 SU 模型。
"""
import os, sys, json, time, base64, re, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

client = None
MODEL = None
LABEL_TEMP = 0.6
LABEL_THINKING = False


def load_word_list(path):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    return [item["词"] for item in data]


def build_system_prompt(top_words):
    word_list_str = ", ".join(top_words[:500])
    return f"""# Role
你是一名顶级的景观建筑 3D 资产标注专家。你的任务是仔细观察 SU 模型的预览图，结合文件名，严格按以下维度提取模型属性，输出结构化 JSON 和自然语言描述。

# Core Rules
- 文件名优先：先解析文件名中的下划线分词，提取关键词映射到各维度，LLM 只负责补全和归一化，不凭空编造
- 预览图中的水印内容（如"知末"、"zhiMo"等）绝对不标注
- 文件名末尾的 ID_数字 不标注
- 看不清或不存在的元素，宁可留空，绝不硬编
- 禁止在输出中添加主观评价词（如"优美的"、"震撼的"）
- 禁止在描述末尾添加总结句或使用建议（如"适合..."、"展现..."、"呈现..."、"营造..."），白描画面即可，不要升华

## 维度1：设计风格 style (单选)
从文件名提取 + 看图确认。限词库：现代、新中式、中式、轻奢、极简、古风、意式、欧式、日式、热带度假风、禅意、赛博朋克、宋式、田园、卡通风、工业风。如无法判断填"现代"。

## 维度2：资产标签 tags (数组，多选)
从受控词表选取合适的词，涵盖模型中的硬景构筑元素（如亭子、水景、铺装、景墙、雕塑、灯具、标识、游乐设施、建筑等），场景模型可多选。优先用受控词表内的词，文件名关键词不要丢失。

## 维度3：植物 plants (数组，多选)
模型中包含的植物类型。限词库：乔木、灌木、草坪、花卉、观赏草、水生植物、棕榈、竹子、造型树、花境、绿篱、苔藓、攀援植物。无植物则留空 []。重要：如果文件名中明确写了植物品种（如红枫、罗汉松、银杏、樱花、鸡爪槭等），必须保留原词写入 plants。

## 维度4：材质 material (数组，多选)
限词库：木材、石材、金属、竹材、玻璃、混凝土、砖、穿孔板、亚克力、涂料、塑胶、藤编、张拉膜。如画面无法判断则留空 []。

## 维度5：造型特征 form (单选)
仅当模型具有鲜明的、非功能性的几何造型特征时才填写——即该特征是其视觉辨识度的核心。限词库：曲线、直线、拱形、波浪、圆球、异形、长条形、错缝。
重要：绝大多数资产没有突出的造型特征，应留空 ""。判断标准：如果去掉这个造型描述，别人还能准确想象出这个物体的样子，那就是没有突出特征。比如普通亭子、普通景墙、普通灯笼、普通招牌、普通石头——都不填 form。只有像"拱形廊桥""圆球雕塑""波浪铺装""异形艺术装置"这种造型本身就是卖点的才填。

## 受控词表（优先使用，用于 sub_category 和描述）
{word_list_str}

---

# Output Format
严格按顺序输出两部分：JSON + 自然语言描述。

JSON 格式：
{{
  "style": "风格",
  "tags": ["标签1", "标签2", "标签3"],
  "plants": ["植物1"],
  "material": ["材质1"],
  "form": "造型特征",
  "description": "50-80字综合描述，融合各维度关键词"
}}

---

# Few-Shot Examples

## 示例1：单一构筑
文件名：新中式亭子_凉亭_古建亭子_休闲亭子餐桌椅组合_四角亭子_古建长廊ID_1111557098
[输出]:
{{
  "style": "新中式",
  "tags": ["亭子", "凉亭", "四角亭", "古建长廊", "户外桌椅"],
  "plants": [],
  "material": ["木材", "石材"],
  "form": "",
  "description": "新中式四角凉亭，木结构梁柱配深色石材基座，檐口平直方正，内置休闲桌椅组合，侧接古建长廊。"
}}

## 示例2：雕塑
文件名：现代不锈钢雕塑_抽象艺术装置_景观小品_镜面雕塑ID_1195881237
[输出]:
{{
  "style": "现代",
  "tags": ["雕塑", "艺术装置", "景观小品"],
  "plants": [],
  "material": ["金属"],
  "form": "异形",
  "description": "现代抽象不锈钢雕塑，表面镜面抛光处理，轮廓呈不规则流线体块穿插组合，周围以碎石铺装衬托。"
}}

## 示例3：水景
文件名：现代镜面水景_跌水_涌泉_庭院水景_不锈钢收边ID_1193253930
[输出]:
{{
  "style": "现代",
  "tags": ["水景", "镜面水景", "跌水", "涌泉"],
  "plants": [],
  "material": ["石材", "金属"],
  "form": "",
  "description": "现代风格矩形镜面水景，中央设抽象不锈钢雕塑，两侧对称布置阶梯式跌水与涌泉阵列，深色石材池壁配不锈钢收边。"
}}

## 示例4：复合场景带植物
文件名：现代庭院景观_水景_景墙_户外座椅_植物组团_铺装ID_1198888888
[输出]:
{{
  "style": "现代",
  "tags": ["庭院景观", "水景", "景墙", "户外座椅", "铺装"],
  "plants": ["乔木", "灌木", "花卉", "草坪"],
  "material": ["石材", "木材", "金属"],
  "form": "",
  "description": "现代庭院综合场景，矩形水景为中心，石材景墙作背景，木质户外座椅沿铺装布置，搭配乔木灌木与花卉组成的多层植物组团。"
}}"""


_print_lock = threading.Lock()

def safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)


def encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def clean_json(content):
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if not match:
        return None
    json_str = match.group(0)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        result = {}
        for key in ["style", "form", "description"]:
            m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', json_str)
            if m:
                result[key] = m.group(1)
        for key in ["tags", "plants", "material"]:
            m = re.search(rf'"{key}"\s*:\s*\[(.*?)\]', json_str, re.DOTALL)
            if m:
                items = re.findall(r'"([^"]*)"', m.group(1))
                result[key] = items
        if "style" in result and "tags" in result:
            return result
    return None


def label_one(image_path, system_prompt, max_retries=3):
    filename = os.path.basename(image_path)
    name_without_ext = os.path.splitext(filename)[0]
    b64 = encode_image(image_path)

    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": f"文件名：{name_without_ext}"}
                    ]}
                ],
                temperature=LABEL_TEMP,
                extra_body={"thinking": {"type": "enabled"}} if LABEL_THINKING else {"thinking": {"type": "disabled"}},
            )
            result = clean_json(completion.choices[0].message.content)
            if result:
                return result
            safe_print(f"  [WARN] JSON解析失败，重试 {attempt+1}/{max_retries}")
            time.sleep(0.5)
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "overloaded" in error_msg.lower():
                wait = 3 * (attempt + 1)
                safe_print(f"  [WAIT] 429限流，等待{wait}秒...")
                time.sleep(wait)
            elif "invalid or unsupported image" in error_msg.lower():
                safe_print(f"  [BAD_IMG] 图片无法解码，跳过")
                return None
            else:
                safe_print(f"  [ERR] 错误: {e}")
                time.sleep(1)
    return None


def process_one(jpg_path, source_dir, system_prompt, idx, total):
    rel = jpg_path.relative_to(source_dir)
    out_path = jpg_path.parent / f"{jpg_path.stem}_精标.json"

    if out_path.exists():
        safe_print(f"[{idx}/{total}] [SKIP] {rel}")
        return "skip"

    safe_print(f"[{idx}/{total}] [ANNOTATE] {rel}")
    result = label_one(jpg_path, system_prompt)

    if result:
        result["filename"] = jpg_path.name
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        safe_print(f"[{idx}/{total}] [OK] style={result.get('style','?')} tags={len(result.get('tags',[]))}个")
        return "done"
    else:
        safe_print(f"[{idx}/{total}] [FAIL] {rel}")
        return "fail"


def main(source_dir, word_list_path, workers=3):
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        print(f"[ERR] 目录不存在: {source_dir}")
        return

    print("加载受控词表...")
    top_words = load_word_list(word_list_path)
    system_prompt = build_system_prompt(top_words)
    print(f"词表: {len(top_words)} 词, Prompt 长度: {len(system_prompt)} 字符")

    jpg_files = sorted(source_dir.rglob("*.jpg"))
    total = len(jpg_files)
    print(f"共找到 {total} 张预览图, 并发: {workers}")

    pending = []
    skipped = 0
    for jp in jpg_files:
        out_path = jp.parent / f"{jp.stem}_精标.json"
        if out_path.exists():
            skipped += 1
        else:
            pending.append(jp)

    if skipped:
        print(f"已跳过 {skipped} 张（已有精标结果）")
    if not pending:
        print("没有待标注的图片")
        return
    print(f"待标注: {len(pending)} 张")
    print("-" * 50)

    done = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for jpg_path in pending:
            idx = jpg_files.index(jpg_path) + 1
            futures[executor.submit(process_one, jpg_path, source_dir, system_prompt, idx, total)] = jpg_path
            time.sleep(0.15)

        for future in as_completed(futures):
            status = future.result()
            if status == "done":
                done += 1
            elif status == "fail":
                failed += 1

    print("-" * 50)
    print(f"完成: 成功 {done}, 失败 {failed}, 跳过 {skipped}")


def run(source_dir, word_list_path, workers=3, prompt_template=None, model_cfg=None):
    """用受控词表约束二轮标注

    Args:
        prompt_template: 自定义提示词模板，用 {word_list_str} 占位词表位置
        model_cfg: dict with api_key, base_url, model
    """
    global client, MODEL, build_system_prompt, LABEL_TEMP, LABEL_THINKING
    if model_cfg:
        client = OpenAI(
            api_key=model_cfg.get("api_key", ""),
            base_url=model_cfg.get("base_url", ""),
            timeout=120.0,
        )
        MODEL = model_cfg.get("model", "")
        LABEL_TEMP = model_cfg.get("temperature", 0.6)
        LABEL_THINKING = model_cfg.get("thinking", False)
    if prompt_template:
        def _custom_build(top_words):
            return prompt_template.replace("{word_list_str}", ", ".join(top_words[:500]))
        build_system_prompt = _custom_build
    if not os.path.isdir(source_dir):
        return False, f"目录不存在: {source_dir}", None
    if not word_list_path or not os.path.isfile(word_list_path):
        return False, f"词表不存在: {word_list_path}", None
    try:
        main(source_dir, word_list_path, workers=workers)
        return True, f"精标完成: {source_dir}", source_dir
    except Exception as e:
        return False, f"精标失败: {e}", None


def check_done(**paths):
    refined_dir = paths.get("refined_label_dir", "")
    if refined_dir and os.path.isdir(refined_dir):
        jsons = list(Path(refined_dir).rglob("*.json"))
        if jsons:
            return True, f"已精标 {len(jsons)} 个 JSON"
    return False, "未找到精标结果"


if __name__ == "__main__":
    workers = 3
    folder = None
    word_file = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] in ("-w", "--workers"):
            workers = int(args[i + 1])
            i += 2
        elif args[i] in ("--words"):
            word_file = args[i + 1]
            i += 2
        else:
            folder = args[i]
            i += 1

    if not folder:
        folder = input("请输入知末文件夹路径: ").strip().strip('"').strip("'")
    if not word_file:
        default_words = Path(folder) / "规范词频_Top500.json"
        if default_words.exists():
            word_file = str(default_words)
        else:
            word_file = input("请输入规范词表路径: ").strip().strip('"').strip("'")

    main(folder, word_file, workers)
