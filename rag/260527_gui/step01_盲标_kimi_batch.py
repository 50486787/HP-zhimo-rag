import os
import sys
import json
import time
import base64
import re
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

client = None
MODEL = None
LABEL_TEMP = 0.6
LABEL_THINKING = False

SYSTEM_PROMPT = """# Role
你是景观建筑设计院的 3D 资产标注专家。

# 任务
根据模型预览图和文件名，为 SU 模型打上一组标签和一段自然语言描述。

# 规则
- 参考文件名中的描述信息
- 标签用逗号分隔，自由发挥，你觉得有检索价值的词都写上
- 描述 50-80 字，综合概括模型特征
- 禁止标注水印内容
- 禁止标注文件名末尾的 ID_数字

# 输出格式
严格只输出 JSON，不要额外文字：
{
  "tags": "词1, 词2, 词3, ...",
  "description": "50-80字综合描述"
}"""



def encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def clean_json(content):
    content_no_think = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    match = re.search(r'\{.*\}', content_no_think, re.DOTALL)
    if not match:
        return None
    json_str = match.group(0)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # 容错：模型偶尔在标签/描述内输出未转义双引号，用关键字边界提取
        result = {}
        # tags: 从 "tags": " 到 ", "description"
        m = re.search(r'"tags"\s*:\s*"(.*?)",\s*"description"', json_str, re.DOTALL)
        if m:
            result["tags"] = m.group(1)
        # description: 从 "description": " 到末尾 "}
        m = re.search(r'"description"\s*:\s*"(.*?)"\s*\}\s*$', json_str, re.DOTALL)
        if m:
            result["description"] = m.group(1)
        return result if len(result) == 2 else None


def label_one(image_path, max_retries=3):
    filename = os.path.basename(image_path)
    name_without_ext = os.path.splitext(filename)[0]
    b64 = encode_image(image_path)

    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": f"文件名：{name_without_ext}"}
                    ]}
                ],
                temperature=LABEL_TEMP,
                extra_body={"thinking": {"type": "enabled"}} if LABEL_THINKING else {"thinking": {"type": "disabled"}}
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
            elif "invalid or unsupported image" in error_msg.lower() or "failed to decode image" in error_msg.lower():
                safe_print(f"  [BAD_IMG] 图片无法解码，跳过")
                return None  # 不重试，直接放弃
            else:
                safe_print(f"  [ERR] 错误: {e}")
                time.sleep(1)
    return None


_print_lock = threading.Lock()

def safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)


def process_one(jpg_path, source_dir, output_dir, idx, total):
    rel = jpg_path.relative_to(source_dir)
    json_path = output_dir / rel.with_suffix(".json")
    json_path.parent.mkdir(parents=True, exist_ok=True)

    if json_path.exists():
        safe_print(f"[{idx}/{total}] [SKIP] 已存在，跳过: {rel}")
        return "skip"

    safe_print(f"[{idx}/{total}] [ANNOTATE] 标注: {rel}")

    result = label_one(jpg_path)
    if result:
        result["_source_filename"] = os.path.basename(jpg_path)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        tags = result.get("tags", "")
        safe_print(f"[{idx}/{total}] [OK] tags: {tags[:60]}...")
        return "done"
    else:
        safe_print(f"[{idx}/{total}] [FAIL] 标注失败: {rel}")
        return "fail"


def main(source_dir, output_dir=None, workers=3):
    source_dir = Path(source_dir)
    if output_dir is None:
        output_dir = source_dir
    else:
        output_dir = Path(output_dir)
    if not source_dir.is_dir():
        print(f"[ERR] 目录不存在: {source_dir}")
        return

    jpg_files = sorted(source_dir.rglob("*.jpg"))
    total = len(jpg_files)
    print(f"共找到 {total} 张预览图")
    print(f"输出目录: {output_dir}")
    print(f"并发数: {workers}")
    print("-" * 50)

    pending = []
    skipped = 0
    for jpg_path in jpg_files:
        rel = jpg_path.relative_to(source_dir)
        if (output_dir / rel.with_suffix(".json")).exists():
            skipped += 1
        else:
            pending.append(jpg_path)

    if skipped:
        print(f"已跳过 {skipped} 张（已有标注结果）")
    if not pending:
        print("没有待标注的图片，退出")
        return

    print(f"待标注: {len(pending)} 张")
    print("-" * 50)

    done = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for jpg_path in pending:
            idx = jpg_files.index(jpg_path) + 1
            futures[executor.submit(process_one, jpg_path, source_dir, output_dir, idx, total)] = jpg_path
            time.sleep(0.15)  # 提交间隔，避免瞬间涌入

        for future in as_completed(futures):
            status = future.result()
            if status == "done":
                done += 1
            elif status == "fail":
                failed += 1

    print("-" * 50)
    print(f"完成: 成功 {done}, 失败 {failed}, 跳过 {skipped}")


def run(image_dir, output_dir=None, workers=3, prompt=None, model_cfg=None):
    """调 Kimi 视觉 API 批量盲标

    Args:
        image_dir: 图片所在目录
        output_dir: 标注 JSON 输出目录（默认同 image_dir）
        prompt: 自定义系统提示词，None 则用默认
        model_cfg: dict with api_key, base_url, model (用于覆盖默认client)
    """
    global client, MODEL, SYSTEM_PROMPT, LABEL_TEMP, LABEL_THINKING
    if model_cfg:
        client = OpenAI(
            api_key=model_cfg.get("api_key", ""),
            base_url=model_cfg.get("base_url", ""),
            timeout=120.0,
        )
        MODEL = model_cfg.get("model", "")
        LABEL_TEMP = model_cfg.get("temperature", 0.6)
        LABEL_THINKING = model_cfg.get("thinking", False)
    if prompt:
        SYSTEM_PROMPT = prompt
    if not os.path.isdir(image_dir):
        return False, f"目录不存在: {image_dir}", None
    if output_dir is None:
        output_dir = image_dir
    try:
        os.makedirs(output_dir, exist_ok=True)
        main(image_dir, output_dir=output_dir, workers=workers)
        return True, f"盲标完成: {output_dir}", output_dir
    except Exception as e:
        return False, f"盲标失败: {e}", None


def check_done(**paths):
    raw_dir = paths.get("raw_label_dir", "")
    if raw_dir and os.path.isdir(raw_dir):
        jsons = list(Path(raw_dir).rglob("*.json"))
        if jsons:
            return True, f"已标注 {len(jsons)} 个 JSON"
    return False, "未找到粗标结果"


if __name__ == "__main__":
    workers = 3
    folder = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] in ("-w", "--workers"):
            workers = int(args[i + 1])
            i += 2
        else:
            folder = args[i]
            i += 1

    if not folder:
        folder = input("请输入图片文件夹路径: ").strip().strip('"').strip("'")
    main(folder, workers)
