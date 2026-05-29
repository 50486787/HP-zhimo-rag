"""step06_web_server.py - FastAPI Web 服务，包装 Searcher 提供搜索 API

启动方式:
    python step06_web_server.py
    或
    uvicorn step06_web_server:app --host 0.0.0.0 --port 8080 --ssl-keyfile .cert/key.pem --ssl-certfile .cert/cert.pem
"""
import datetime
import os
import socket
import subprocess
import sys

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image

# 将本模块所在目录加入 sys.path，以便导入 search_core
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from search_core import Searcher

# ================= 配置常量 =================
VOCAB_PATH = os.environ.get("VOCAB_PATH", os.path.join(os.path.dirname(__file__), "知末粗标", "规范词频_Top500.json"))
RAGFLOW_KEY = os.environ.get("RAGFLOW_KEY", "")
RAGFLOW_DS = os.environ.get("RAGFLOW_DS", "").split(",") if os.environ.get("RAGFLOW_DS") else []
RAGFLOW_BASE = os.environ.get("RAGFLOW_BASE", "http://127.0.0.1:9900/api/v1")
KIMI_KEY = os.environ.get("KIMI_KEY", "")
KIMI_BASE = os.environ.get("KIMI_BASE", "https://api.moonshot.cn/v1")
BGE_BASE = os.environ.get("BGE_BASE", "http://127.0.0.1:9997/v1")
THUMB_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".thumb_cache")

# ================= FastAPI 应用 =================
app = FastAPI(title="景观AI搜图 v2")

# 模块级 searcher 引用，由 lifespan 事件赋值
_searcher = None


def _real_path(ragflow_path):
    """RAGFlow 存 P01/P02 或 P260527_gui_gui测试，还原为真实文件系统路径。
    只把最后一个 _ 转为子目录分隔符。"""
    path = ragflow_path[1:] if ragflow_path.startswith("P") else ragflow_path
    idx = path.rfind("_")
    if idx != -1:
        path = path[:idx] + os.sep + path[idx+1:]
    return path


def _safe_path(nas_base, rest_path):
    """防路径遍历：确保 rest_path 解析后仍在 nas_base 内。
    rest_path 格式: 目录/文件名.jpg，只对目录部分做 P前缀+下划线 转换"""
    parts = rest_path.replace("\\", "/").split("/", 1)
    real_dir = _real_path(parts[0])
    src = os.path.realpath(os.path.join(nas_base, real_dir, parts[1] if len(parts) > 1 else ""))
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
    rar_path = os.path.join(nas, real_dir, f"{filename}.rar")

    return {
        "filename": searcher.doc_fns[match_idx],
        "path": searcher.doc_paths[match_idx],
        "tags": searcher.doc_tags_list[match_idx],
        "jpg_exists": os.path.isfile(jpg_path),
        "zip_exists": os.path.isfile(zip_path),
        "rar_exists": os.path.isfile(rar_path),
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
    """下载 ZIP/RAR 文件"""
    nas = os.environ.get("NAS_BASE", "")
    if not nas:
        raise HTTPException(503, "NAS_BASE 未配置")

    file_path = _safe_path(nas, rest_path)
    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件不存在")

    # 根据扩展名设置 Content-Type
    ext = os.path.splitext(file_path)[1].lower()
    media_map = {".zip": "application/zip", ".rar": "application/vnd.rar"}
    media_type = media_map.get(ext, "application/octet-stream")

    # RFC 5987 编码中文文件名，避免 latin-1 编码错误
    from urllib.parse import quote
    raw_fn = os.path.basename(rest_path)
    encoded_fn = quote(raw_fn)
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_fn}"},
    )


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

CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cert")
CERT_PATH = os.path.join(CERT_DIR, "cert.pem")
KEY_PATH = os.path.join(CERT_DIR, "key.pem")


def _ensure_cert():
    """生成 HTTPS 证书（优先使用 mkcert 系统信任 CA，否则自签名）"""
    if os.path.isfile(CERT_PATH) and os.path.isfile(KEY_PATH):
        return

    print("首次启动，生成 HTTPS 证书...")
    os.makedirs(CERT_DIR, exist_ok=True)

    hostname = socket.gethostname()
    try:
        lan_ip = socket.gethostbyname(hostname)
    except Exception:
        lan_ip = "127.0.0.1"

    # 优先尝试 mkcert（系统信任 CA，需先执行 mkcert -install）
    try:
        result = subprocess.run(
            ["mkcert", "-key-file", KEY_PATH, "-cert-file", CERT_PATH,
             "localhost", "127.0.0.1", hostname, lan_ip],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"  证书已生成 (mkcert): {CERT_PATH}")
            return
    except FileNotFoundError:
        pass

    # 回退：cryptography 自签名
    print("mkcert 不可用，使用自签名证书...")
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        raise RuntimeError(
            "需要 cryptography 库来生成 HTTPS 证书，请运行: pip install cryptography"
        )

    key = rsa.generate_private_key(65537, 2048, default_backend())
    with open(KEY_PATH, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    san_names = [x509.DNSName("localhost"), x509.DNSName(hostname)]
    try:
        import ipaddress
        san_names.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
        san_names.append(x509.IPAddress(ipaddress.IPv4Address(lan_ip)))
    except Exception:
        pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
        .sign(key, hashes.SHA256(), default_backend())
    )
    with open(CERT_PATH, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f"  自签名证书已生成: {CERT_PATH}")


if __name__ == "__main__":
    import asyncio
    import uvicorn

    # Windows HTTPS 下 ProactorEventLoop 会因客户端断开抛 ConnectionResetError，
    # 切换 SelectorEventLoop 彻底消除
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    nas = os.environ.get("NAS_BASE", "")
    if not nas:
        nas = input('NAS路径 (如 \\\\192.168.1.203\\知末备份): ').strip().strip('"')
        os.environ["NAS_BASE"] = nas
    print(f"NAS: {nas}")

    _ensure_cert()
    print(f"HTTPS: https://{socket.gethostname()}:8088")
    uvicorn.run(app, host="0.0.0.0", port=8088,
                ssl_keyfile=KEY_PATH, ssl_certfile=CERT_PATH)
