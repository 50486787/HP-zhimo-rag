"""step00 — 扫描 NAS 目录结构，生成目录骨架与数量 JSON"""
import os
import json


def run(target_dir, output_dir=None):
    """扫描 NAS 目录结构 → 生成 目录骨架与数量.json

    Returns:
        (ok: bool, msg: str, output_path: str or None)
    """
    target_dir = os.path.abspath(target_dir)
    if not os.path.isdir(target_dir):
        return False, f"目录不存在: {target_dir}", None

    if output_dir is None:
        output_dir = os.path.dirname(target_dir)

    project_name = os.path.basename(target_dir)
    output_path = os.path.join(output_dir, f"{project_name}_目录骨架与数量.json")

    try:
        skeleton = _scan_directory(target_dir, project_name)
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(skeleton, f, indent=2, ensure_ascii=False)
        count = len(skeleton)
        return True, f"扫描完成: {count} 个子目录 → {os.path.basename(output_path)}", output_path
    except Exception as e:
        return False, f"扫描失败: {e}", None


def _scan_directory(dir_path, project_name):
    skeleton = {}
    dir_path = os.path.abspath(dir_path)
    base_len = len(dir_path)
    junk_ext = {'.bak', '.dwl', '.dwl2', '.tmp', '.err', '.log', '.recover'}

    for root, dirs, files in os.walk(dir_path):
        valid_files = [
            f for f in files
            if not f.startswith('~$')
            and f.lower() not in ('thumbs.db', 'ehthumbs.db', '.ds_store')
            and os.path.splitext(f)[1].lower() not in junk_ext
        ]
        rel_path = root[base_len:].lstrip(os.sep).replace("\\", "/")
        if not rel_path:
            rel_path = project_name
        else:
            rel_path = f"{project_name}/{rel_path}"
        if valid_files:
            skeleton[rel_path] = len(valid_files)
        elif not any(os.path.isdir(os.path.join(root, d)) for d in dirs):
            skeleton[rel_path] = 0
    return skeleton


def check_done(**paths):
    output_dir = paths.get("output_dir", "")
    if output_dir and os.path.isdir(output_dir):
        for f in os.listdir(output_dir):
            if "目录骨架与数量" in f and f.endswith(".json"):
                return True, f"已生成: {f}"
    return False, "未找到目录骨架 JSON"
