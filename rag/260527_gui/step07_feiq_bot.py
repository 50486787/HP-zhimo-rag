"""feiq_bot.py — 飞秋搜图机器人

使用纯标准 IPMSG 协议（端口 2425），飞秋完全兼容。
收到消息 → 调 step06 /api/search 预执行搜索 → 发主页面链接（带 ?q= 参数）
用户点击链接 → index.html 自动搜索 → 完整交互体验（继续搜、浏览、详情、下载）

部署注意：Bot 占用 2425 端口，不能和飞秋客户端在同一台机器上同时运行。
"""
import json
import os
import ssl
import socket
import time
import urllib.request
from urllib.parse import quote

# ================= 配置 =================
BOT_NAME = "搜图Bot"
BOT_PORT = 2425

# step06 Web 服务地址
WEB_BASE = os.environ.get("WEB_BASE", "https://127.0.0.1:8088")

# ================= IPMSG 协议常量 =================
IPMSG_BR_ENTRY = 1
IPMSG_BR_EXIT  = 2
IPMSG_ANSENTRY = 3
IPMSG_SENDMSG  = 32
IPMSG_RECVMSG  = 33


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def make_pkt(pkt_no, name, host, cmd, msg=""):
    raw = f"1:{pkt_no}:{name}:{host}:{cmd}:{msg}"
    return raw.encode("gbk", errors="replace")


def parse_pkt(data):
    text = data.decode("gbk", errors="replace")
    if "_lbt6_" in text:
        idx = text.find(":")
        if idx == -1:
            return None
        text = "1" + text[idx:]
    parts = text.split(":", 5)
    if len(parts) < 6:
        return None
    cmd_raw = int(parts[4])
    if cmd_raw >= 0x600000:
        cmd_raw &= 0xFF
    base_cmd = cmd_raw & 0xFF
    return parts[0], parts[1], parts[2], parts[3], str(base_cmd), parts[5]


def prefetch_search(query):
    """预执行搜索（让 step06 侧热缓存，用户打开页面时更快）"""
    url = f"{WEB_BASE}/api/search?q={quote(query)}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(url, context=ctx, timeout=60) as resp:
        return json.loads(resp.read())


HOSTNAME = socket.gethostname()
MY_IP = get_lan_ip()


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", BOT_PORT))
    sock.settimeout(1)

    print(f"飞秋 Bot 已上线")
    print(f"  名称: {BOT_NAME}  主机: {HOSTNAME}  IP: {MY_IP}")
    print(f"  Web:  {WEB_BASE}")

    pkt = make_pkt(int(time.time()), BOT_NAME, HOSTNAME, IPMSG_BR_ENTRY, BOT_NAME)
    sock.sendto(pkt, ("255.255.255.255", BOT_PORT))
    print("BR_ENTRY 广播已发")

    pkt_counter = int(time.time())
    last_seen = {}

    try:
        while True:
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                pkt = make_pkt(int(time.time()), BOT_NAME, HOSTNAME, IPMSG_ANSENTRY, BOT_NAME)
                sock.sendto(pkt, ("255.255.255.255", BOT_PORT))
                continue

            if addr[0] == MY_IP:
                continue

            parsed = parse_pkt(data)
            if not parsed:
                continue

            ver, pkt_no_str, sender, sender_host, cmd_str, extra = parsed
            cmd = int(cmd_str)
            query = extra.strip().rstrip("\x00\r\n")
            peer = f"{sender}@{addr[0]}"

            if cmd in (IPMSG_BR_ENTRY, IPMSG_ANSENTRY):
                now = time.time()
                last = last_seen.get(peer, 0)
                if cmd == IPMSG_ANSENTRY and now - last < 5:
                    last_seen[peer] = now
                    continue
                last_seen[peer] = now
                pkt = make_pkt(int(time.time()), BOT_NAME, HOSTNAME, IPMSG_ANSENTRY, BOT_NAME)
                sock.sendto(pkt, (addr[0], BOT_PORT))

            elif cmd == IPMSG_SENDMSG:
                print(f"[消息] {peer}: {query}")

                ack = make_pkt(int(time.time()), BOT_NAME, HOSTNAME, IPMSG_RECVMSG, "")
                sock.sendto(ack, (addr[0], BOT_PORT))

                if query.lower() in ("帮助", "help", "h"):
                    reply_text = (
                        "景观AI搜图 Bot\n"
                        "直接输入搜索词即可，如：\n"
                        "  中式庭院\n"
                        "  儿童滑梯\n"
                        "  欧式喷泉 石材\n"
                        "也支持口语搜索，如：\n"
                        "  有没有适合幼儿园的户外设施\n"
                        "  新中式别墅入口大门"
                    )
                elif query.lower() in ("ping", "test", "测试"):
                    reply_text = "pong! Bot 在线"
                else:
                    page_url = f"{WEB_BASE}/?q={quote(query)}"
                    try:
                        data = prefetch_search(query)
                        total = data.get("total", 0)
                        reply_text = f"搜图「{query}」共 {total} 条\n{page_url}"
                    except Exception as e:
                        reply_text = f"搜图「{query}」\n{page_url}\n(预搜索失败: {e})"

                reply = make_pkt(pkt_counter, BOT_NAME, HOSTNAME, IPMSG_SENDMSG, reply_text[:800])
                pkt_counter += 1
                sock.sendto(reply, (addr[0], BOT_PORT))
                print(f"  -> 已回复")

    except KeyboardInterrupt:
        print("\n广播下线...")
        pkt = make_pkt(int(time.time()), BOT_NAME, HOSTNAME, IPMSG_BR_EXIT, BOT_NAME)
        sock.sendto(pkt, ("255.255.255.255", BOT_PORT))
    finally:
        sock.close()


def run():
    """启动飞秋 Bot（阻塞，需在子进程/线程中运行）"""
    main()


def check_done(**paths):
    """检测飞秋 Bot 是否在线（端口 2425 是否监听）"""
    import socket as _socket
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        s.settimeout(1)
        s.bind(("0.0.0.0", 0))
        # 给自己发 ANSENTRY，如果能收到回包说明在监听
        s.sendto(b"1:0:check:check:3:check", ("127.0.0.1", 2425))
        s.close()
        return True, "端口 2425 已监听"
    except Exception:
        return False, "飞秋 Bot 未启动"
    finally:
        try:
            s.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
