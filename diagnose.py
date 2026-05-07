"""诊断 v6：穷举 fileUrl 参数 + 发现其他下载端点"""
import json
import requests
from config import API_BASE, SITE_BASE
from db import init_db, get_cookies


def diagnose(sku_id="1135455615"):
    db_path = "downloads.db"
    init_db(db_path)
    cookies_json = get_cookies(db_path)
    if not cookies_json:
        print("没有 cookie")
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": SITE_BASE,
    })
    cookies = json.loads(cookies_json)
    for c in cookies:
        session.cookies.set(c["name"], c["value"],
                           domain=c.get("domain", ""),
                           path=c.get("path", "/"))

    # 先 qualify
    print("=== qualify ===")
    r = session.get(f"{API_BASE}/download/qualify", params={"skuId": sku_id})
    print(f"  {r.text}")

    # 获取 commodity detail
    detail_data = None
    r = session.get(f"{API_BASE}/commodity/detail", params={"skuId": sku_id})
    if r.status_code == 200:
        detail_data = r.json().get("data", {})
        print(f"  commodityId: {detail_data.get('commodityId')}")
        print(f"  physicalModelId: {detail_data.get('physicalModelId')}")
        print(f"  type: {detail_data.get('type')}")

    # 穷举 fileUrl POST body 参数
    print("\n=== fileUrl POST body 穷举 ===")
    commodity_id = detail_data.get("commodityId") if detail_data else None
    physical_id = detail_data.get("physicalModelId") if detail_data else None

    bodies = [
        {"skuId": sku_id},
        {"skuId": int(sku_id)},
        {"skuId": sku_id, "commodityId": commodity_id},
        {"skuId": sku_id, "type": 10},
        {"skuId": sku_id, "type": 4},
        {"commodityId": commodity_id},
        {"commodityId": str(commodity_id)},
        {"physicalModelId": physical_id},
        {"modelId": sku_id},
        {"id": sku_id},
        {"sku_id": sku_id},
        {"productId": sku_id},
        {"goodsId": sku_id},
        {"fileId": sku_id},
        {"skuId": sku_id, "enterpriseId": "1123909600639193088"},
    ]

    model_url = f"https://su.znzmo.com/sumoxing/{sku_id}.html"
    for body in bodies:
        r = session.post(f"{API_BASE}/download/fileUrl",
                        json=body,
                        headers={
                            "Referer": model_url,
                            "Content-Type": "application/json;charset=UTF-8",
                        })
        result = r.text[:120]
        if r.status_code != 400:
            print(f"  >>> {body}: {r.status_code} -> {result}")

    # 尝试不同的请求路径
    print("\n=== 其他可能的下载端点 ===")
    alt_endpoints = [
        ("GET", f"{API_BASE}/download/getFileUrl"),
        ("GET", f"{API_BASE}/download/getDownloadUrl"),
        ("GET", f"{API_BASE}/download/downloadFile"),
        ("POST", f"{API_BASE}/download/doDownload"),
        ("POST", f"{API_BASE}/download/createDownload"),
        ("GET", f"{API_BASE}/download/generateUrl"),
        ("GET", f"{API_BASE}/file/download"),
        ("GET", f"{API_BASE}/commodity/download"),
        ("GET", f"{API_BASE}/commodity/file"),
        ("PUT", f"{API_BASE}/download/fileUrl"),
    ]

    for method, url in alt_endpoints:
        try:
            if method == "GET":
                r = session.get(url, params={"skuId": sku_id})
            else:
                r = session.post(url, json={"skuId": sku_id})
            if r.status_code not in [404]:
                print(f"  {method} {url}: {r.status_code} -> {r.text[:150]}")
        except:
            pass

    # 尝试带 commodityId 的 qualify+fileUrl
    print("\n=== qualify with commodityId then fileUrl ===")
    r = session.get(f"{API_BASE}/download/qualify", params={"skuId": commodity_id})
    print(f"  qualify commodityId={commodity_id}: {r.text[:150]}")

    # 尝试用不同的 HTTP 方法 fileUrl
    print("\n=== fileUrl HTTP 方法 ===")
    for method in ["GET", "POST", "PUT", "PATCH"]:
        try:
            if method == "GET":
                r = session.get(f"{API_BASE}/download/fileUrl", params={"skuId": sku_id})
            elif method == "POST":
                r = session.post(f"{API_BASE}/download/fileUrl", json={"skuId": sku_id})
            elif method == "PUT":
                r = session.put(f"{API_BASE}/download/fileUrl", json={"skuId": sku_id})
            elif method == "PATCH":
                r = session.patch(f"{API_BASE}/download/fileUrl", json={"skuId": sku_id})
            print(f"  {method}: {r.status_code} -> {r.text[:120]}")
        except Exception as e:
            print(f"  {method}: {e}")


if __name__ == "__main__":
    import sys
    sku = sys.argv[1] if len(sys.argv) > 1 else "1135455615"
    diagnose(sku)
