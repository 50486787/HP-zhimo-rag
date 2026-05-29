"""将精标 JSON 合成为 RAGFlow 入库 CSV。默认 --split 按子目录分别生成。"""
import sys, os, json
from collections import defaultdict
from pathlib import Path

def build_rows(json_files, target_dir):
    rows = []
    for jp in json_files:
        with open(jp, encoding="utf-8") as f:
            data = json.load(f)

        json_basename = os.path.basename(jp)
        name_no_ext = os.path.splitext(json_basename)[0]
        stem = name_no_ext[:-3] if name_no_ext.endswith("_精标") else name_no_ext
        rel = os.path.relpath(os.path.dirname(jp), target_dir)
        if rel == ".":
            parent = os.path.basename(os.path.dirname(target_dir))
            rel_dir = f"P{parent}_{os.path.basename(target_dir)}"
        else:
            rel_dir = "P" + rel.replace("\\", "/")

        style = data.get("style", "")
        tags = data.get("tags", [])
        plants = data.get("plants", [])
        material = data.get("material", [])
        form = data.get("form", "")
        description = data.get("description", "")

        tag_parts = []
        if style:
            tag_parts.append(f"风格:{style}")
        if tags:
            tag_parts.append(f"构筑:{' '.join(tags)}")
        if plants:
            tag_parts.append(f"植物:{' '.join(plants)}")
        if material:
            tag_parts.append(f"材质:{' '.join(material)}")
        if form:
            tag_parts.append(f"造型:{form}")

        tag_text = " | ".join(tag_parts)
        content = f"{tag_text} | {description}"

        rows.append({
            "content": content,
            "filename": stem,
            "path": rel_dir,
        })
    return rows

def write_csv(rows, output_path):
    with open(output_path, "w", encoding="utf-8-sig") as f:
        f.write("path,filename,content\n")
        for r in rows:
            c = r["content"].replace('"', '""')
            f.write(f'"{r["path"]}","{r["filename"]}","{c}"\n')
    print(f"  -> {os.path.basename(output_path)} ({len(rows)} 条)")

def build_csv(target_dir, split=True):
    target_dir = os.path.abspath(target_dir)

    # 递归收集所有 json
    json_files = []
    for root, dirs, files in os.walk(target_dir):
        for f in files:
            if f.endswith("_精标.json"):
                json_files.append(os.path.join(root, f))
    json_files.sort()

    if not json_files:
        print("未找到 JSON 文件")
        return

    # CSV 输出到源目录的上级目录
    out_dir = os.path.dirname(target_dir)
    os.makedirs(out_dir, exist_ok=True)

    if split:
        groups = defaultdict(list)
        for jp in json_files:
            subdir = os.path.relpath(os.path.dirname(jp), target_dir)
            groups[subdir].append(jp)

        for subdir in sorted(groups.keys()):
            rows = build_rows(groups[subdir], target_dir)
            if subdir == ".":
                parent = os.path.basename(os.path.dirname(target_dir))
                name = f"{parent}_{os.path.basename(target_dir)}"
            else:
                name = subdir
            out = os.path.join(out_dir, f"ragflow_入库_{name}.csv")
            write_csv(rows, out)
        print(f"\n共 {len(groups)} 个 CSV | 总计 {len(json_files)} 条")
    else:
        rows = build_rows(json_files, target_dir)
        out = os.path.join(out_dir, "ragflow_入库.csv")
        write_csv(rows, out)

def run(source_dir, output_dir=None, split=True):
    """从精标 JSON 生成 RAGFlow 入库 CSV"""
    source_dir = os.path.abspath(source_dir)
    if not os.path.isdir(source_dir):
        return False, f"目录不存在: {source_dir}", None
    if output_dir is None:
        output_dir = os.path.dirname(source_dir)
    try:
        build_csv(source_dir, split=split)
        return True, f"CSV 已生成到: {output_dir}", output_dir
    except Exception as e:
        return False, f"生成 CSV 失败: {e}", None


def check_done(**paths):
    output_dir = paths.get("output_dir", "")
    if output_dir and os.path.isdir(output_dir):
        csvs = list(Path(output_dir).glob("ragflow_入库_*.csv"))
        if csvs:
            return True, f"已生成 {len(csvs)} 个 CSV"
    return False, "未找到入库 CSV"


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else input("请输入精标目录路径: ").strip().strip('"').strip("'")
    split = "--merged" not in sys.argv  # --merged 生成单个合并 CSV
    build_csv(target, split=split)
