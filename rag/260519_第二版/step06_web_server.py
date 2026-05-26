"""step06_web_server.py - FastAPI Web 服务，包装 Searcher 提供搜索 API

启动方式:
    python step06_web_server.py
    或
    uvicorn step06_web_server:app --host 0.0.0.0 --port 8080
"""
import os
import sys

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image

# 将本模块所在目录加入 sys.path，以便导入 search_core
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from search_core import Searcher

# ================= 配置常量 =================
VOCAB_PATH = os.path.join(os.path.dirname(__file__), "知末粗标", "规范词频_Top500.json")
RAGFLOW_KEY = "ragflow-GELaRwRQXrL4oNxlciCEtmXJUUhj_9Ma0UwT38Yn1xU"
RAGFLOW_DS = ["ae52896e580711f1ba3a0fbde202da50"]
RAGFLOW_BASE = "http://127.0.0.1:9900/api/v1"
KIMI_KEY = "sk-g9ys55fFGKkJTsuTkEYhjC5sqEY83HfUyxRmVe1pm8QJmnN3"
KIMI_BASE = "https://api.moonshot.cn/v1"
BGE_BASE = "http://127.0.0.1:9997/v1"
THUMB_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".thumb_cache")

# ================= FastAPI 应用 =================
app = FastAPI(title="景观AI搜图 v2")

# 模块级 searcher 引用，由 lifespan 事件赋值
_searcher = None


def _real_path(ragflow_path):
    """RAGFlow 存 P01/P02，实际目录是 01/02，去掉 P 前缀"""
    return ragflow_path[1:] if ragflow_path.startswith("P") else ragflow_path


def _safe_path(nas_base, rest_path):
    """防路径遍历：确保 rest_path 解析后仍在 nas_base 内。"""
    src = os.path.realpath(os.path.join(nas_base, _real_path(rest_path)))
    nas_real = os.path.realpath(nas_base)
    if os.path.commonpath([nas_real, src]) != nas_real:
        raise HTTPException(403, "禁止访问")
    return src


def _ensure_ready():
    """确保 searcher 已初始化，否则抛 503"""
    if _searcher is None or not _searcher.ready:
        raise HTTPException(503, "搜索引擎尚未初始化完成，请稍后重试")
    return _searcher


# ================= 路由: 前端页面 =================

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """返回前端首页"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index_path):
        with open(index_path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    raise HTTPException(404, "前端尚未部署")


# ================= 路由: API =================

@app.get("/api/groups")
async def api_groups():
    """返回所有分组 (path) 列表"""
    searcher = _ensure_ready()
    groups = searcher.get_groups()
    return {"groups": groups}


@app.get("/api/browse")
async def api_browse(
    group: str = Query(..., description="分组名 (path)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """浏览某个分组下的文档"""
    searcher = _ensure_ready()
    indices = searcher.get_group_items(group)
    total = len(indices)

    start = (page - 1) * page_size
    end = start + page_size
    page_indices = indices[start:end]

    items = []
    for i in page_indices:
        items.append({
            "filename": searcher.doc_fns[i],
            "path": searcher.doc_paths[i],
            "tags": searcher.doc_tags_list[i],
        })

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/api/search")
async def api_search(q: str = Query(..., description="搜索关键词")):
    """AI 搜索"""
    searcher = _ensure_ready()
    return searcher.search(q)


@app.get("/api/detail")
async def api_detail(
    path: str = Query(..., description="分组路径"),
    filename: str = Query(..., description="文件名"),
):
    """返回单条文档详情，含 jpg/zip 是否存在的信息"""
    searcher = _ensure_ready()

    # 查找匹配文档
    match_idx = None
    for i in range(len(searcher.doc_fns)):
        if searcher.doc_paths[i] == path and searcher.doc_fns[i] == filename:
            match_idx = i
            break

    if match_idx is None:
        raise HTTPException(404, "未找到文档")

    nas = os.environ.get("NAS_BASE", "")
    real_dir = _real_path(path)
    jpg_path = os.path.join(nas, real_dir, f"{filename}.jpg")
    zip_path = os.path.join(nas, real_dir, f"{filename}.zip")

    return {
        "filename": searcher.doc_fns[match_idx],
        "path": searcher.doc_paths[match_idx],
        "tags": searcher.doc_tags_list[match_idx],
        "jpg_exists": os.path.isfile(jpg_path),
        "zip_exists": os.path.isfile(zip_path),
    }


# ================= 路由: 图片代理 =================

@app.get("/img/{rest_path:path}")
async def img_proxy(
    rest_path: str,
    w: int = Query(400, ge=1, le=4000, description="缩略图宽度"),
):
    """代理 NAS 图片，自动缩略 + 缓存到 THUMB_CACHE_DIR"""
    nas = os.environ.get("NAS_BASE", "")
    if not nas:
        raise HTTPException(503, "NAS_BASE 未配置")

    src_path = _safe_path(nas, rest_path)
    if not os.path.isfile(src_path):
        raise HTTPException(404, "图片不存在")

    # 缓存文件名: 路径中的分隔符替换为 _，避免子目录
    safe_name = rest_path.replace("\\", "_").replace("/", "_")
    cache_name = f"{safe_name}_{w}.jpg"
    cache_path = os.path.join(THUMB_CACHE_DIR, cache_name)

    # 缓存命中
    if os.path.isfile(cache_path):
        return FileResponse(cache_path, media_type="image/jpeg")

    # 生成缩略图（原子写入防止并发损坏）
    try:
        os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
        img = Image.open(src_path)
        img = img.convert("RGB")

        # 等比缩放
        orig_w, orig_h = img.size
        if orig_w > w:
            ratio = w / orig_w
            new_h = int(orig_h * ratio)
            img = img.resize((w, new_h), Image.LANCZOS)

        # 先写临时文件再原子重命名
        cache_tmp = cache_path + ".tmp"
        img.save(cache_tmp, "JPEG", quality=80)
        os.replace(cache_tmp, cache_path)

        return FileResponse(cache_path, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "图片处理失败")


# ================= 路由: 下载 =================

@app.get("/download/{rest_path:path}")
async def download_zip(rest_path: str):
    """下载 ZIP 文件"""
    nas = os.environ.get("NAS_BASE", "")
    if not nas:
        raise HTTPException(503, "NAS_BASE 未配置")

    file_path = _safe_path(nas, rest_path)
    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件不存在")

    return FileResponse(file_path, media_type="application/zip", filename=os.path.basename(rest_path))


# ================= 启动事件 =================

@app.on_event("startup")
async def startup():
    """初始化 Searcher 并创建缓存目录"""
    global _searcher

    os.makedirs(THUMB_CACHE_DIR, exist_ok=True)

    _searcher = Searcher(
        vocab_path=VOCAB_PATH,
        ragflow_key=RAGFLOW_KEY,
        ragflow_ds_ids=RAGFLOW_DS,
        ragflow_base=RAGFLOW_BASE,
        kimi_api_key=KIMI_KEY,
        kimi_base_url=KIMI_BASE,
        bge_base_url=BGE_BASE,
    )

    print("正在初始化搜索引擎...")
    _searcher.initialize()
    print(f"搜索引擎就绪，文档数: {len(_searcher.doc_fns)}")


# ================= 启动入口 =================

if __name__ == "__main__":
    import uvicorn

    nas = os.environ.get("NAS_BASE", "")
    if not nas:
        nas = input('NAS路径 (如 \\\\192.168.1.203\\知末备份): ').strip().strip('"')
        os.environ["NAS_BASE"] = nas
    print(f"NAS: {nas}")
    uvicorn.run(app, host="0.0.0.0", port=8080)
