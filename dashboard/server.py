#!/usr/bin/env python3
"""Tasmota 插头本地面板 —— 只依赖 Python 标准库。

为什么要这一层：Tasmota 的 HTTP 响应不带 Access-Control-Allow-Origin，
浏览器直接 fetch 设备 IP 会被 CORS 拦掉（命令其实发出去了，但读不到 JSON）。
所以这里在 127.0.0.1 起一个小服务，转发设备的 /cm?cmnd=... 接口。

用法：
    ./server.py                     # 读 ~/.config/tasmota/plug.conf 里的 host
    ./server.py --host 192.168.3.46 --port 8765
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF_PATH = os.path.expanduser("~/.config/tasmota/plug.conf")
HERE = os.path.dirname(os.path.abspath(__file__))
DEVICE = {"host": "", "timeout": 4}


def conf_host() -> str:
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip().lower() == "host":
                        return v.strip()
    return ""


def cmnd(cmd: str) -> dict:
    url = f"http://{DEVICE['host']}/cm?cmnd={urllib.parse.quote(cmd)}"
    with urllib.request.urlopen(url, timeout=DEVICE["timeout"]) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw)


def snapshot() -> dict:
    """一次 Status 0 拿全：开关、电能、网络、固件、运行时长都在这四组里。"""
    s = cmnd("Status 0")
    sts = s.get("StatusSTS", {})
    net = s.get("StatusNET", {})
    fw = s.get("StatusFWR", {})
    prm = s.get("StatusPRM", {})
    dev = s.get("Status", {})
    e = s.get("StatusSNS", {}).get("ENERGY", {})
    wifi = sts.get("Wifi", {})
    return {
        "online": True,
        "name": dev.get("DeviceName", ""),
        "topic": dev.get("Topic", ""),
        "power": sts.get("POWER", ""),
        "uptime": sts.get("Uptime", ""),
        "heap": sts.get("Heap"),
        "rssi": wifi.get("Signal"),
        "ssid": wifi.get("SSId", ""),
        "ip": net.get("IPAddress", ""),
        "mac": net.get("Mac", ""),
        "version": fw.get("Version", ""),
        "hardware": fw.get("Hardware", ""),
        "bootcount": prm.get("BootCount"),
        "time": sts.get("Time", ""),
        "energy": {
            "voltage": e.get("Voltage"),
            "power": e.get("Power"),
            "current": e.get("Current"),
            "factor": e.get("Factor"),
            "today": e.get("Today"),
            "total": e.get("Total"),
        } if e else None,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "TasmotaPanel/1.0"

    def log_message(self, fmt, *args):  # 静音访问日志，出错才看
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, b"index.html missing", "text/plain")
        elif path == "/api/state":
            try:
                self._json(snapshot())
            except Exception as e:
                self._json({"online": False, "error": str(e)}, 200)
        elif path == "/api/power":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self._power((q.get("cmd") or ["toggle"])[0])
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/power":
            self._send(404, b"not found", "text/plain")
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            body = {}
        self._power(body.get("cmd", "toggle"))

    def _power(self, cmd: str) -> None:
        cmd = cmd.lower()
        word = {"on": "On", "off": "Off", "toggle": "Toggle"}.get(cmd)
        if not word:
            self._json({"ok": False, "error": f"不认识的指令 {cmd}"}, 400)
            return
        try:
            r = cmnd("Power " + word)
            self._json({"ok": True, "power": r.get("POWER", "?")})
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 200)


def main() -> None:
    p = argparse.ArgumentParser(description="Tasmota 插头本地面板")
    p.add_argument("--host", default=conf_host(), help="插头 IP，默认读 plug.conf")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    if not a.host:
        sys.exit("没有设备 IP。用 --host 指定，或先跑 plug.py discover 写配置。")
    DEVICE["host"] = a.host

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"面板已启动： http://127.0.0.1:{a.port}/   设备 {a.host}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
