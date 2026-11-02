#!/usr/bin/env python3
"""
scan.py —— 不依赖 avahi/D-Bus 的局域网设备扫描，专门找 Tasmota 智能插头。

原理：并发 TCP 连 80 端口 -> 拿到活的主机 -> 逐个 GET / 看指纹 -> 再问一句
      /cm?cmnd=Status%202，返回里有 StatusFWR 就是 Tasmota。
用法:
  ./scan.py                     # 自动取本机所在 /24 网段
  ./scan.py 192.168.3.0/24
  ./scan.py 192.168.3.0/24 --all   # 连非 80 端口的主机也列出来
"""

import concurrent.futures as cf
import ipaddress
import json
import re
import subprocess
import sys
import urllib.request

TIMEOUT = 0.6
ESP_OUI = {
    "24:0A:C4", "84:F3:EB", "5C:CF:7F", "A0:20:A6", "60:01:94", "84:CC:A8",
    "DC:4F:22", "3C:71:BF", "8C:AA:B5", "48:3F:DA", "7C:DF:A1", "E8:68:E7",
    "C8:C9:A3", "94:B9:7E", "30:AE:A4", "08:3A:F2", "10:52:1C", "3C:61:05",
    "44:17:93", "24:6F:28", "CC:50:E3", "80:64:6F", "B4:E6:2D", "4C:11:AE",
    "84:0D:8E", "E0:98:06", "A4:CF:12", "50:02:91", "68:C6:3A", "98:F4:AB",
    "F4:CF:A2", "08:B6:1F", "34:94:54", "9C:9C:1F", "D8:BF:C0", "DC:54:75",
    "24:62:AB", "7C:9E:BD", "48:27:E2", "1C:BF:C0", "AC:67:B2", "C4:DD:57",
    "D4:8A:FC", "EC:FA:BC", "F0:08:D1", "A8:03:2A", "40:91:51", "BC:DD:C2",
    "E8:DB:84", "68:C6:3A", "98:CD:AC", "F4:12:FA", "30:83:98", "C4:4F:33",
}


def local_subnet() -> str:
    out = subprocess.run(["ip", "-4", "route", "show", "default"],
                         capture_output=True, text=True).stdout
    dev = re.search(r"dev (\S+)", out)
    if not dev:
        sys.exit("取不到默认网卡，请手动指定网段，如 ./scan.py 192.168.1.0/24")
    addr = subprocess.run(["ip", "-4", "-brief", "addr", "show", dev.group(1)],
                          capture_output=True, text=True).stdout
    m = re.search(r"(\d+\.\d+\.\d+\.\d+/\d+)", addr)
    if not m:
        sys.exit("取不到本机 IP，请手动指定网段")
    return str(ipaddress.ip_network(m.group(1), strict=False))


def ping(ip: str) -> bool:
    return subprocess.run(["ping", "-c1", "-W1", "-n", ip],
                          capture_output=True).returncode == 0


def sweep(hosts: list) -> None:
    """ICMP 扫一遍把 ARP 表填满，然后列出所有看得见的设备 —— 不需要它们开 80 端口。"""
    print(f"ICMP 扫 {len(hosts)} 个地址…")
    with cf.ThreadPoolExecutor(max_workers=128) as ex:
        alive = [h for h, ok in zip(hosts, ex.map(ping, hosts)) if ok]
    print(f"存活 {len(alive)} 台\n")
    print(f"{'IP':<16}{'MAC':<20}{'芯片/厂商':<14}{'80 端口'}")
    print("-" * 62)
    for ip in alive:
        mac = mac_of(ip)
        mark = "开" if port_open(ip) else "-"
        print(f"{ip:<16}{mac or '?':<20}{vendor(mac) or '未知':<14}{mark}")


def port_open(ip: str, port: int = 80) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(TIMEOUT)
        return s.connect_ex((ip, port)) == 0


PORTS = {
    80: "HTTP（Tasmota/路由器/打印机）",
    443: "HTTPS",
    6668: "涂鸦 Tuya 协议（原厂固件）",
    8081: "Sonoff DIY / eWeLink",
    1883: "MQTT broker",
    8080: "HTTP 备用",
    81: "HTTP 备用",
}


def scan_ports(hosts: list) -> None:
    """多端口扫一遍：能区分 Tasmota(80) 和还没刷的原厂插头(Tuya 6668 / Sonoff 8081)。"""
    print(f"扫 {len(hosts)} 台主机 × {len(PORTS)} 个端口…\n")
    jobs = [(h, p) for h in hosts for p in PORTS]
    with cf.ThreadPoolExecutor(max_workers=256) as ex:
        hits = [r for r in ex.map(port_open_pair, jobs) if r]
    if not hits:
        print("全空。网段里没有任何设备开这些端口。")
        return
    print(f"{'IP':<16}{'端口':<7}{'含义'}")
    print("-" * 60)
    for ip, p in sorted(hits):
        print(f"{ip:<16}{p:<7}{PORTS[p]}")
    print(f"\n命中 {len(hits)} 条。涂鸦设备命中 6668 = 原厂固件，需刷机；"
          f"Tasmota 命中 80 且 /cm 接口可用。")


def port_open_pair(a: tuple) -> tuple | None:
    ip, port = a
    import socket
    with socket.socket() as s:
        s.settimeout(0.35)
        return a if s.connect_ex((ip, port)) == 0 else None


def mac_of(ip: str) -> str:
    out = subprocess.run(["ip", "neigh", "show", ip], capture_output=True, text=True).stdout
    m = re.search(r"lladdr ([0-9a-f:]{17})", out, re.I)
    return m.group(1).upper() if m else ""


def vendor(mac: str) -> str:
    if not mac:
        return ""
    return "乐鑫/ESP" if mac[:8] in ESP_OUI else ""


def fingerprint(ip: str) -> dict:
    info = {"ip": ip, "tasmota": False, "title": "", "server": "", "fw": "", "hw": ""}
    try:
        with urllib.request.urlopen(f"http://{ip}/", timeout=1.5) as r:
            info["server"] = r.headers.get("Server", "")
            body = r.read(2000).decode("utf-8", "replace")
        t = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
        info["title"] = (t.group(1).strip()[:40] if t else "") or body.strip()[:30].replace("\n", " ")
    except Exception as e:
        info["title"] = f"(非 HTTP: {type(e).__name__})"
    try:
        with urllib.request.urlopen(f"http://{ip}/cm?cmnd=Status%202", timeout=1.5) as r:
            raw = r.read(1024).decode("utf-8", "replace")
        if "StatusFWR" in raw:
            j = json.loads(raw).get("StatusFWR", {})
            info.update(tasmota=True, fw=j.get("Version", "?"), hw=j.get("Hardware", "?"))
    except Exception:
        pass
    return info


def main() -> None:
    subnet = sys.argv[1] if len(sys.argv) > 1 and "/" in sys.argv[1] else local_subnet()
    show_all = "--all" in sys.argv
    net = ipaddress.ip_network(subnet, strict=False)
    hosts = [str(h) for h in net.hosts()]

    if "--arp" in sys.argv or "--sweep" in sys.argv:
        sweep(hosts)
        return

    if "--ports" in sys.argv:
        scan_ports(hosts)
        return

    print(f"扫描 {net}（{len(hosts)} 个地址，约 5 秒）…\n")

    with cf.ThreadPoolExecutor(max_workers=128) as ex:
        alive = [h for h, ok in zip(hosts, ex.map(port_open, hosts)) if ok]

    if not alive:
        print("80 端口一个都没开 —— 网段选错了？或者插头不在这个网段。")
        print("先确认：路由器后台看 DHCP 客户端列表，找 hostname 带 tasmota / 厂商名的设备。")
        return

    print(f"{'IP':<16}{'型号/固件':<26}{'页面标题':<22}{'MAC 厂商'}")
    print("-" * 78)
    found = []
    for info in map(fingerprint, alive):
        mac = mac_of(info["ip"])
        if info["tasmota"]:
            found.append(info["ip"])
            left, mid = f"Tasmota {info['fw']}", info["hw"][:22]
            print(f"{info['ip']:<16}{mid:<26}{left:<22}{vendor(mac) or mac}")
        elif show_all:
            print(f"{info['ip']:<16}{'?':<26}{info['title'][:20]:<22}{vendor(mac) or mac}")

    print()
    if found:
        print(f"找到 Tasmota 设备：{', '.join(found)}")
        print(f"下一步：./plug.py --host {found[0]} on")
    else:
        print("这个网段里没有 Tasmota。可能原因：")
        print("  1. 插头还没刷 Tasmota（原厂固件，通常是涂鸦/易微联 App 配网）")
        print("  2. 插头连的是另一个 SSID / VLAN（比如 IoT 专用 Wi-Fi）")
        print("  3. 插头没上电或没连上网 —— 看它指示灯是否常亮")
        print("加 --all 可以列出所有开了 80 端口的设备，用来判断它在不在线。")


if __name__ == "__main__":
    main()
