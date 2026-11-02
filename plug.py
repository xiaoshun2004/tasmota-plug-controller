#!/usr/bin/env python3
"""
plug.py —— 在 Ubuntu 本机控制 Tasmota 智能插头。只用标准库，不装任何依赖。

用法:
  ./plug.py discover            # 局域网里找 Tasmota 设备（mDNS + ARP 探测）
  ./plug.py on                  # 通电
  ./plug.py off                 # 断电
  ./plug.py toggle              # 翻转
  ./plug.py status              # 开关状态 + IP/信号 + 功率（若有计量）
  ./plug.py energy              # 电量/功率原始数据
  ./plug.py raw "Backlog Power1 ON"   # 直接下发任意 Tasmota 命令
  ./plug.py --host 192.168.1.50 on    # 临时指定 IP

配置（任选其一，优先级从高到低）:
  1. 命令行 --host/--user/--password
  2. 环境变量 TASMOTA_HOST / TASMOTA_USER / TASMOTA_PASSWORD
  3. ~/.config/tasmota/plug.conf  内容形如:
        host=192.168.1.50
        user=admin          # 设了 Web 密码才需要
        password=xxxx
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CONF_PATH = os.path.expanduser("~/.config/tasmota/plug.conf")
TIMEOUT = 3


# ---------- 配置 ----------

def load_conf() -> dict:
    conf = {}
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                conf[k.strip().lower()] = v.strip()
    for k in ("host", "user", "password"):
        env = os.environ.get("TASMOTA_" + k.upper())
        if env:
            conf[k] = env
    return conf


def save_conf(host: str, user: str = "", password: str = "") -> None:
    os.makedirs(os.path.dirname(CONF_PATH), exist_ok=True)
    with open(CONF_PATH, "w", encoding="utf-8") as f:
        f.write(f"host={host}\n")
        if user:
            f.write(f"user={user}\n")
        if password:
            f.write(f"password={password}\n")
        f.write("# 换 IP 后重新跑一次: plug.py discover  然后改这里的 host\n")


# ---------- HTTP ----------

def build_url(host: str, cmnd: str, conf: dict) -> str:
    if not re.match(r"^https?://", host):
        host = "http://" + host
    host = host.rstrip("/")
    q = {"cmnd": cmnd}
    if conf.get("user"):
        q["user"] = conf["user"]
    if conf.get("password"):
        q["password"] = conf["password"]
    return f"{host}/cm?" + urllib.parse.urlencode(q, safe="", quote_via=urllib.parse.quote)


def call(host: str, cmnd: str, conf: dict) -> dict:
    url = build_url(host, cmnd, conf)
    req = urllib.request.Request(url, headers={"User-Agent": "plug.py"})
    if conf.get("user"):
        token = base64.b64encode(
            f"{conf['user']}:{conf.get('password', '')}".encode()
        ).decode()
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace").strip()
    except urllib.error.URLError as e:
        sys.exit(f"[x] 连不上 {host} —— {e.reason}\n    插头断电了？IP 变了？跑一下: plug.py discover")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"_raw": body}


# ---------- 发现 ----------

def mdns_hosts() -> list:
    """用 avahi 找局域网里广播了 http 服务的 Tasmota（主机名通常是 tasmota-xxxx）。"""
    found = []
    try:
        out = subprocess.run(
            ["avahi-browse", "-alrt", "--no-db-lookup"],
            capture_output=True, text=True, timeout=8,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return found
    for line in out.splitlines():
        if line.startswith("=") and ("tasmota" in line.lower()):
            m = re.search(r"address = \[([^\]]+)\]", line)
            if m:
                found.append(m.group(1))
    return found


def probe(ip: str) -> tuple | None:
    """直接问一句 Status 2，能回 JSON 就是 Tasmota。"""
    try:
        url = f"http://{ip}/cm?cmnd=Status%202"
        with urllib.request.urlopen(url, timeout=1.0) as r:
            body = r.read(4096).decode("utf-8", "replace")
        if "StatusFWR" in body:
            j = json.loads(body)
            fw = j.get("StatusFWR", {})
            ver = fw.get("Version", "?")
            hard = fw.get("Hardware", "?")
            return (ip, ver, hard)
    except Exception:
        return None
    return None


def arp_ips() -> list:
    try:
        out = subprocess.run(["ip", "neigh", "show"], capture_output=True, text=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return sorted({m.group(1) for m in re.finditer(r"^(\d+\.\d+\.\d+\.\d+)\s", out, re.M)})


def discover(conf: dict) -> None:
    print("· mDNS 扫描…")
    ips = mdns_hosts()
    print(f"· ARP 表探测（{len(arp_ips())} 个邻居，约 3 秒）…")
    ips += arp_ips()
    ips = list(dict.fromkeys(ips))
    with ThreadPoolExecutor(max_workers=32) as ex:
        results = [r for r in ex.map(probe, ips) if r]
    if not results:
        print("[!] 没找到。依次检查：插头和电脑在同一 Wi-Fi/网段？插头真的刷了 Tasmota 且已连网？")
        print("    实在找不到：路由器后台看 DHCP 客户端列表，找 hostname 带 tasmota 的那台。")
        return
    for ip, ver, hard in results:
        print(f"  ✓ {ip:<15} {hard:<22} {ver}")
    if len(results) == 1:
        ip = results[0][0]
        save_conf(ip, conf.get("user", ""), conf.get("password", ""))
        print(f"\n已写入 {CONF_PATH}，之后直接 plug.py on/off 就行。")
    else:
        print(f"\n多台设备：用 plug.py --host <IP> on 指定，或把 IP 写进 {CONF_PATH}")


# ---------- 状态输出 ----------

def show_status(host: str, conf: dict) -> None:
    s = call(host, "Status 0", conf)
    if "_raw" in s:
        sys.exit(f"[x] 返回不是 JSON：{s['_raw'][:200]}")
    # Status 0 把信息拆成三组返回：继电器/运行态在 StatusSTS，网络在 StatusNET，
    # 设备名/PowerOnState 在 Status。以前的版本只读 Status 组，所以全是 None。
    st = s.get("Status", {})
    sts = s.get("StatusSTS", {})
    net = s.get("StatusNET", {})
    fw = s.get("StatusFWR", {})
    power = sts.get("POWER") or st.get("Power") or st.get("POWER")
    print(f"开关    : {power}")
    name = st.get("DeviceName") or "?"
    print(f"设备    : {name}   固件 {fw.get('Version', '?')} ({fw.get('Hardware', '?')})")
    if net:
        w = sts.get("Wifi", {})
        # Wifi.Signal 才是 dBm（负数），Wifi.RSSI 是 0-100 的信号质量百分比，两者别混
        print(f"IP      : {net.get('IPAddress')}  ({w.get('SSId', '?')}, {w.get('Signal', '?')} dBm)")
    print(f"运行时长: {sts.get('Uptime')}")
    e = call(host, "Status 10", conf).get("StatusSNS", {}).get("ENERGY")
    if e:
        print(f"当前功率: {e.get('Power')} W   电压 {e.get('Voltage')} V   电流 {e.get('Current')} A")
        print(f"累计电量: {e.get('Total')} kWh   今日 {e.get('Today')} kWh")


# ---------- 连续采样 ----------

def monitor(host: str, conf: dict, interval: float = 2.0, count: int = 0) -> None:
    """反复读 Status 10，打印成表格。count=0 表示一直跑，Ctrl-C 停。"""
    print(f"{'时刻':<10}{'电压V':>7}{'功率W':>8}{'电流A':>8}{'PF':>6}{'累计kWh':>10}")
    print("-" * 49)
    i = 0
    try:
        while True:
            j = call(host, "Status 10", conf).get("StatusSNS", {})
            e = j.get("ENERGY")
            if not e:
                print("这台设备没有计量数据（StatusSNS 里没有 ENERGY 字段）。")
                return
            t = (j.get("Time") or "")[11:] or "--:--:--"
            print(f"{t:<10}{e.get('Voltage', 0):>7}{e.get('Power', 0):>8}"
                  f"{e.get('Current', 0):>8.3f}{e.get('Factor', 0):>6.2f}{e.get('Total', 0):>10.3f}")
            i += 1
            if count and i >= count:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止采样。")


# ---------- main ----------

def main() -> None:
    p = argparse.ArgumentParser(
        description="控制局域网里的 Tasmota 智能插头",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例: plug.py discover | plug.py on | plug.py toggle | plug.py status",
    )
    p.add_argument("--host", help="设备 IP 或主机名，如 192.168.1.50 / tasmota-1a2b3c.local")
    p.add_argument("--user", help="Web 密码的用户名（默认 admin）")
    p.add_argument("--password", help="Web 密码")
    p.add_argument("--json", action="store_true", help="输出原始 JSON")
    p.add_argument("--interval", type=float, default=2.0, help="monitor 的采样间隔秒数（默认 2）")
    p.add_argument("--count", type=int, default=0, help="monitor 采样次数，0 = 一直采")
    p.add_argument("action", nargs="?", default="status",
                   help="discover | on | off | toggle | status | energy | monitor | raw <命令>")
    p.add_argument("rest", nargs=argparse.REMAINDER, help="raw 的附加参数")
    a = p.parse_args()

    conf = load_conf()
    if a.user is not None:
        conf["user"] = a.user
    if a.password is not None:
        conf["password"] = a.password

    if a.action == "discover":
        discover(conf)
        return

    host = a.host or conf.get("host")
    if not host:
        sys.exit("还没有目标设备。先跑: ./plug.py discover   或  ./plug.py --host 192.168.1.50 status")

    act = a.action.lower()
    if act in ("on", "off", "toggle"):
        cmnd = "Power " + {"on": "On", "off": "Off", "toggle": "Toggle"}[act]
        r = call(host, cmnd, conf)
        print(json.dumps(r) if a.json else f"{host}  ->  {r.get('POWER', r)}")
    elif act == "monitor":
        monitor(host, conf, a.interval, a.count)
    elif act == "energy":
        print(json.dumps(call(host, "Status 10", conf), indent=2, ensure_ascii=False))
    elif act == "status":
        if a.json:
            print(json.dumps(call(host, "Status 0", conf), indent=2, ensure_ascii=False))
        else:
            show_status(host, conf)
    elif act == "raw":
        cmnd = " ".join(a.rest).strip() or "Status"
        print(json.dumps(call(host, cmnd, conf), indent=2, ensure_ascii=False))
    else:
        sys.exit(f"不认识的动作: {act}（可用: discover/on/off/toggle/status/energy/monitor/raw）")


if __name__ == "__main__":
    main()
