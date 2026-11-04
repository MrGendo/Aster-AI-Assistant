# 网页抓取 worker：为摘要富化提供原文（只读 GET）
# 安全边界（抓任意 URL 的前提）：
#   1. SSRF 防护：解析后的 IP 命中私网/环回/链路本地/保留段一律拒绝（含重定向后的每一跳）
#   2. 体积上限 512KB、超时 10s、仅 text/html、正文截断 3000 字（够摘要用）
#   3. 无副作用：不执行 JS、不写文件、不带回 Cookie
import ipaddress
import json
import re
import socket
import sys
import urllib.request
from html.parser import HTMLParser

MAX_BYTES = 512 * 1024
MAX_TEXT = 3000
UA = "aster-capability/0.1 (+readonly)"


def _host_ip_ok(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


class _NoUnsafeRedirect(urllib.request.HTTPRedirectHandler):
    """重定向逐跳校验目标主机，防止跳进内网"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.request.urlparse(newurl).hostname or ""
        if not _host_ip_ok(host):
            raise RuntimeError(f"重定向目标不安全: {host}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


opener = urllib.request.build_opener(_NoUnsafeRedirect)


class _TextExtract(HTMLParser):
    # 极简正文抽取：去 script/style，取文本与标题（够摘要，不追求精准正文）
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.title = ""
        self._in_title = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self.skip:
            return
        if self._in_title:
            self.title += data
        else:
            self.parts.append(data)


def fetch(url: str) -> dict:
    host = urllib.request.urlparse(url).hostname or ""
    if not _host_ip_ok(host):
        return {"url": url, "ok": False, "error": f"目标不安全（内网/保留地址）：{host}"}
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener.open(req, timeout=10) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" not in ctype and "text/plain" not in ctype:
            return {"url": url, "ok": False, "error": f"非文本内容（{ctype.split(';')[0]}）"}
        raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return {"url": url, "ok": False, "error": "内容超过 512KB 上限"}
    p = _TextExtract()
    p.feed(raw.decode("utf-8", errors="ignore"))
    text = re.sub(r"\s+", " ", " ".join(p.parts)).strip()
    return {"url": url, "ok": True, "title": p.title.strip()[:200],
            "text": text[:MAX_TEXT]}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("输入不是合法 JSON", file=sys.stderr)
        return 4
    urls = payload.get("urls") or ([payload["url"]] if payload.get("url") else [])
    pages = []
    for u in urls[:5]:  # 单次最多 5 页，控制耗时
        if not str(u).startswith(("http://", "https://")):
            pages.append({"url": u, "ok": False, "error": "仅支持 http(s)"})
            continue
        try:
            pages.append(fetch(str(u)))
        except Exception as e:
            pages.append({"url": u, "ok": False, "error": str(e)[:120]})
    print(json.dumps({"pages": pages}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
