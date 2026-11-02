#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tasmota 插头控制台 —— 桌面版
================================
只用 Python 标准库（tkinter + urllib），无任何第三方依赖。

功能
----
1. 连接检测：测试连通性、显示往返延迟、自动搜索局域网内的 Tasmota 设备
2. 控制开关：开 / 关 / 切换（大圆钮 + 按钮）
3. 状态显示：电压、功率、电流、功率因数、今日、累计 + 电压曲线
4. 运行日志：请求结果、错误原因

用法
----
    python3 app.py                      # 读 ~/.config/tasmota/plug.conf
    python3 app.py --host 192.168.3.46
    python3 app.py --selftest           # 无窗口自检（开发用）

选项 --interval N 设置轮询间隔秒数（默认 2）。
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import ipaddress
import json
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from tkinter import font as tkfont

# ---------------------------------------------------------------- 配置

CONF_PATH = os.path.expanduser("~/.config/tasmota/plug.conf")

# 配色（深色）
BG = "#0f131a"
CARD = "#171d27"
CARD_HI = "#1e2633"
LINE = "#28313f"
TXT = "#e8edf7"
TXT_DIM = "#8b97ad"
TXT_FAINT = "#5b6779"
COL_ON = "#34d399"
COL_OFF = "#5b6779"
COL_ALERT = "#f87171"
COL_ACCENT = "#38bdf8"
COL_WARN = "#fbbf24"
COL_VOLT = "#fbbf24"
COL_WATT = "#38bdf8"

# 不走系统代理：局域网地址被代理拦掉是最常见的"连不上"原因
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def mix(c1: str, c2: str, t: float) -> str:
    """把颜色 c1 按比例 t 混向 c2，模拟半透明效果。"""
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    m = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return "#%02x%02x%02x" % m


def load_host() -> str:
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("host="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def save_host(host: str) -> None:
    try:
        os.makedirs(os.path.dirname(CONF_PATH), exist_ok=True)
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            f.write(f"host={host}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 设备通信


class Device:
    """Tasmota HTTP API 客户端。"""

    def __init__(self, host: str, timeout: float = 4.0):
        self.host = host.strip().rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> str:
        url = f"http://{self.host}{path}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "TasmotaDesk/1.0", "Connection": "close"}
        )
        with _OPENER.open(req, timeout=self.timeout) as r:
            return r.read().decode("utf-8", "replace")

    def cmd(self, command: str) -> dict:
        raw = self._get("/cm?cmnd=" + urllib.parse.quote(command))
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}

    def snapshot(self) -> dict:
        """一次完整读取：状态 + 计量。失败时返回 online=False。"""
        t0 = time.perf_counter()
        try:
            st = self.cmd("Status 0")
            en = self.cmd("Status 8")
            rtt = (time.perf_counter() - t0) * 1000
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            reason = getattr(e, "reason", e)
            return {"online": False, "error": str(reason)}
        except Exception as e:  # noqa: BLE001
            return {"online": False, "error": str(e)}

        if "Status" not in st:
            return {"online": False, "error": "返回不是 Tasmota 格式"}

        s = st.get("Status", {})
        fwr = st.get("StatusFWR", {})
        net = st.get("StatusNET", {})
        sts = st.get("StatusSTS", {})
        wifi = sts.get("Wifi", {})
        energy = (en.get("StatusSNS", {}) or {}).get("ENERGY", {}) or {}

        return {
            "online": True,
            "rtt_ms": rtt,
            "power": sts.get("POWER") or s.get("Power") or "?",
            "poweronstate": s.get("PowerOnState"),
            "version": fwr.get("Version", "?"),
            "hardware": fwr.get("Hardware", "?"),
            "hostname": net.get("Hostname", "?"),
            "mac": net.get("Mac", "?"),
            "ip": net.get("IPAddress", "?"),
            "ssid": wifi.get("SSId", "?"),
            "rssi": wifi.get("Signal", None),
            "quality": wifi.get("RSSI", None),
            "uptime": sts.get("Uptime", "?"),
            "heap": sts.get("Heap", None),
            "voltage": energy.get("Voltage"),
            "watt": energy.get("Power"),
            "current": energy.get("Current"),
            "factor": energy.get("Factor"),
            "today": energy.get("Today"),
            "total": energy.get("Total"),
            "ts": time.time(),
        }


def probe(ip: str, timeout: float = 1.5):
    """判断某个 IP 上是否跑着 Tasmota，是则返回 (ip, 版本, 设备名)。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.35)
            if s.connect_ex((ip, 80)) != 0:
                return None
    except OSError:
        return None
    try:
        r = Device(ip, timeout=timeout).cmd("Status 2")
        if "StatusFWR" in r:
            st = r.get("Status", {})
            return (ip, r["StatusFWR"].get("Version", "?"), st.get("DeviceName", "Tasmota"))
    except Exception:  # noqa: BLE001
        return None
    return None


def local_cidr() -> str | None:
    """推断本机所在 /24 网段。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # 不真的发包，只为拿到出口网卡地址
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    if not ip or ip.startswith("127."):
        return None
    return ip.rsplit(".", 1)[0] + ".0/24"


def scan_lan(cidr: str, on_done, on_progress=None, workers: int = 128):
    """并发扫描网段，找 Tasmota。在调用线程里跑，请放到后台线程。"""
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        on_done([], f"网段格式不对：{e}")
        return
    hosts = [str(h) for h in net.hosts()]
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(probe, h): h for h in hosts}
        done = 0
        for f in concurrent.futures.as_completed(futs):
            done += 1
            r = f.result()
            if r:
                found.append(r)
                if on_progress:
                    on_progress(f"找到 {r[0]}")
            if on_progress and done % max(1, len(hosts) // 10) == 0:
                on_progress(f"已扫描 {done}/{len(hosts)}")
    on_done(found, None)


# ---------------------------------------------------------------- 界面


class App:
    def __init__(self, root: tk.Tk, host: str, interval: float = 2.0):
        self.root = root
        self.host = host
        self.device = Device(host)
        self.interval = interval
        self.q: queue.Queue = queue.Queue()
        self.wake = threading.Event()
        self.running = True
        self.busy = False
        self.last: dict = {}
        self.samples: collections.deque = collections.deque(maxlen=120)
        self.scanning = False
        # 窗口刚映射时 X 可能把焦点点击直接投给画布，防止一开窗就误切电源
        self._armed_at = time.time() + 1.0

        self._setup_window()
        self._setup_fonts()
        self._build()

        self._log("info", f"就绪，目标 {host or '（未设置）'}")
        if not self.font_ok:
            self._log("err", "找不到中文字体，界面会显示方块。请改用系统 python3 运行："
                             "/usr/bin/python3 app.py（该 Python 的 Tk 支持 Xft）")
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self.root.after(100, self._drain)

    # ---------------- 基础

    def _setup_window(self):
        self.root.title("Tasmota 插头控制台")
        self.root.configure(bg=BG)
        self.root.geometry("960x820")
        self.root.minsize(880, 720)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _pick(self, candidates: list[str], size: int, weight: str = "normal"):
        # Tk 在不同构建下列出的字体名大小写不一（9.0 全是小写），统一按小写比对
        fams = {f.lower(): f for f in tkfont.families(self.root)}
        for c in candidates:
            hit = fams.get(c.lower())
            if hit:
                return (hit, size, weight)
        return ("TkDefaultFont", size, weight)

    def _setup_fonts(self):
        zh = ["Noto Sans CJK SC", "Noto Sans CJK SC Regular", "Source Han Sans SC",
              "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Microsoft YaHei",
              "PingFang SC", "SimHei", "song ti", "fangsong ti", "DejaVu Sans"]
        mono = ["JetBrains Mono", "DejaVu Sans Mono", "Ubuntu Mono",
                "Liberation Mono", "Noto Mono", "Courier New"]
        self.f_title = self._pick(zh, 14, "bold")
        self.f_body = self._pick(zh, 10)
        self.f_small = self._pick(zh, 9)
        self.f_label = self._pick(zh, 9)
        # 数字优先用中文黑体：DejaVu Sans Mono 的 0 太像字母 O，读数容易误读
        self.f_num = self._pick(zh + mono, 21, "bold")
        self.f_state = self._pick(zh, 18, "bold")
        self.f_dial = self._pick(mono + zh, 17, "bold")
        self.f_btn = self._pick(zh, 10, "bold")
        self.font_ok = self.f_body[0] != "TkDefaultFont"

    def _card(self, parent, title: str | None = None, **kw):
        """带边框的卡片，返回内部容器。"""
        outer = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                         highlightthickness=1, **kw)
        if title:
            lbl = tk.Label(outer, text=title, font=self.f_label, bg=CARD,
                           fg=TXT_DIM, anchor="w")
            lbl.pack(fill="x", padx=14, pady=(10, 0))
            outer.title_label = lbl  # type: ignore[attr-defined]
        inner = tk.Frame(outer, bg=CARD)
        inner.pack(fill="both", expand=True, padx=14, pady=(6, 12))
        outer.inner = inner  # type: ignore[attr-defined]
        return outer

    def _button(self, parent, text, command, kind="normal", width=8):
        if kind == "primary":
            bg, fg, abg = COL_ACCENT, "#06131a", mix(COL_ACCENT, "#ffffff", 0.2)
        elif kind == "on":
            bg, fg, abg = COL_ON, "#04140e", mix(COL_ON, "#ffffff", 0.2)
        elif kind == "off":
            bg, fg, abg = CARD_HI, TXT, LINE
        else:
            bg, fg, abg = CARD_HI, TXT_DIM, LINE
        b = tk.Button(
            parent, text=text, command=command, font=self.f_btn,
            bg=bg, fg=fg, activebackground=abg, activeforeground=fg,
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            width=width, pady=7,
        )
        return b

    # ---------------- 布局

    def _build(self):
        root = self.root

        # --- 顶栏
        head = tk.Frame(root, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 6))

        tk.Label(head, text="Tasmota 插头控制台", font=self.f_title,
                 bg=BG, fg=TXT).pack(side="left")

        self.lamp = tk.Canvas(head, width=14, height=14, bg=BG,
                              highlightthickness=0)
        self.lamp.pack(side="right", padx=(8, 6))
        self.lamp_dot = self.lamp.create_oval(2, 2, 12, 12, fill=TXT_FAINT, outline="")

        self.lbl_link = tk.Label(head, text="未连接", font=self.f_body,
                                 bg=BG, fg=TXT_DIM)
        self.lbl_link.pack(side="right")

        # --- 工具栏
        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=18, pady=(0, 12))

        tk.Label(bar, text="设备地址", font=self.f_body,
                 bg=BG, fg=TXT_DIM).pack(side="left", padx=(0, 8))

        self.var_host = tk.StringVar(value=self.host)
        self.ent = tk.Entry(
            bar, textvariable=self.var_host, font=self.f_body, width=18,
            bg=CARD, fg=TXT, insertbackground=COL_ACCENT, relief="flat",
            highlightthickness=1, highlightbackground=LINE,
            highlightcolor=COL_ACCENT,
        )
        self.ent.pack(side="left", ipady=5, padx=(0, 8))
        self.ent.bind("<Return>", lambda _e: self._connect())

        self._button(bar, "连接", self._connect, "primary", 6).pack(side="left", padx=3)
        self.btn_scan = self._button(bar, "搜索设备", self._scan, "normal", 8)
        self.btn_scan.pack(side="left", padx=3)

        self.btn_iv = self._button(bar, f"刷新 {self.interval:g}s",
                                  self._cycle_interval, "normal", 8)
        self.btn_iv.pack(side="right")

        # --- 主体
        body = tk.Frame(root, bg=BG)
        body.pack(fill="x", padx=18)
        body.columnconfigure(0, weight=0, minsize=286)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=0)

        # 左：电源控制
        left = self._card(body, "电源控制")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        self.dial = tk.Canvas(left.inner, width=200, height=200, bg=CARD,
                              highlightthickness=0, cursor="hand2")
        self.dial.pack(pady=(2, 4))
        self.dial.bind("<Button-1>", self._dial_click)
        self.dial.bind("<Enter>", lambda _e: self._draw_dial(True))
        self.dial.bind("<Leave>", lambda _e: self._draw_dial(False))

        self.lbl_power = tk.Label(left.inner, text="—", font=self.f_state,
                                  bg=CARD, fg=TXT_FAINT)
        self.lbl_power.pack()

        brow = tk.Frame(left.inner, bg=CARD)
        brow.pack(pady=(12, 4))
        self._button(brow, "开", lambda: self._send("Power On"), "on", 4).pack(side="left", padx=3)
        self._button(brow, "关", lambda: self._send("Power Off"), "off", 4).pack(side="left", padx=3)
        self._button(brow, "切换", lambda: self._send("Power Toggle"), "off", 5).pack(side="left", padx=3)

        self.lbl_pos = tk.Label(left.inner, text="", font=self.f_small,
                                bg=CARD, fg=TXT_FAINT)
        self.lbl_pos.pack(pady=(8, 0))

        # 右：读数网格 + 图表
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=0)
        right.rowconfigure(1, weight=0)

        grid = tk.Frame(right, bg=BG)
        grid.grid(row=0, column=0, sticky="ew")
        for i in range(3):
            grid.columnconfigure(i, weight=1, uniform="m")

        self.readouts = {}
        specs = [
            ("voltage", "电压", "V", COL_VOLT),
            ("watt", "功率", "W", COL_WATT),
            ("current", "电流", "A", TXT),
            ("factor", "功率因数", "", TXT_DIM),
            ("today", "今日用电", "kWh", TXT_DIM),
            ("total", "累计电量", "kWh", TXT_DIM),
        ]
        for idx, (key, label, unit, color) in enumerate(specs):
            r, c = divmod(idx, 3)
            card = self._card(grid)
            card.grid(row=r, column=c, sticky="nsew",
                      padx=(0 if c == 0 else 6, 0 if c == 2 else 6),
                      pady=(0 if r == 0 else 6, 6 if r == 0 else 0))
            box = card.inner
            tk.Label(box, text=label, font=self.f_label, bg=CARD,
                     fg=TXT_DIM, anchor="w").pack(fill="x")
            line = tk.Frame(box, bg=CARD)
            line.pack(anchor="w", pady=(3, 0))
            val = tk.Label(line, text="—", font=self.f_num, bg=CARD, fg=color)
            val.pack(side="left")
            if unit:
                tk.Label(line, text=unit, font=self.f_small, bg=CARD,
                         fg=TXT_FAINT).pack(side="left", padx=(5, 0), pady=(8, 0))
            self.readouts[key] = val

        # 图表
        self.chart_card = self._card(right, self._chart_caption())
        self.chart_card.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.chart = tk.Canvas(self.chart_card.inner, height=150, bg=CARD,
                               highlightthickness=0)
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda _e: self._draw_chart())

        # --- 日志
        log_card = self._card(root, "运行日志")
        log_card.pack(fill="both", expand=True, padx=18, pady=(12, 0))

        wrap = tk.Frame(log_card.inner, bg=CARD)
        wrap.pack(fill="both", expand=True)

        self.log = tk.Text(wrap, height=6, bg=CARD, fg=TXT_DIM,
                           font=self.f_small, relief="flat", bd=0,
                           highlightthickness=0, wrap="word", state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(wrap, command=self.log.yview,
                          bg=CARD, troughcolor=CARD, bd=0, width=8)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)
        self.log.tag_configure("info", foreground=TXT_DIM)
        self.log.tag_configure("ok", foreground=COL_ON)
        self.log.tag_configure("warn", foreground=COL_WARN)
        self.log.tag_configure("err", foreground=COL_ALERT)

        self.lbl_dev = tk.Label(log_card.inner, text="", font=self.f_small,
                                bg=CARD, fg=TXT_FAINT, anchor="w")
        self.lbl_dev.pack(fill="x", pady=(8, 0))

        self._draw_dial()
        self._draw_chart()

    # ---------------- 绘制

    def _chart_caption(self) -> str:
        secs = int(self.samples.maxlen * self.interval)
        span = f"{secs} 秒" if secs < 120 else f"{secs / 60:.0f} 分钟"
        return f"电压 / 功率走势（最近 {span}）"

    def _draw_dial(self, hover: bool = False):
        c = self.dial
        c.delete("all")
        on = self.last.get("power") == "ON"
        known = bool(self.last.get("online"))
        col = COL_ON if on else COL_OFF
        cx = cy = 100
        r_out, r_ring, r_in = 92, 74, 60

        # 光晕
        halo = mix(CARD, col, 0.16 if on else 0.06)
        c.create_oval(cx - r_out, cy - r_out, cx + r_out, cy + r_out,
                      fill=halo, outline="")
        # 外环
        ring = mix(col, CARD, 0.15 if hover else 0.3)
        c.create_oval(cx - r_ring, cy - r_ring, cx + r_ring, cy + r_ring,
                      fill=ring, outline="")
        # 内圆
        inner = mix(CARD, col, 0.22 if on else 0.10)
        if hover:
            inner = mix(inner, "#ffffff", 0.06)
        c.create_oval(cx - r_in, cy - r_in, cx + r_in, cy + r_in,
                      fill=inner, outline=col if on else LINE, width=2)
        # 文字
        label = "ON" if on else ("OFF" if known else "--")
        c.create_text(cx, cy - 6, text=label, font=self.f_dial,
                      fill=col if (on or known) else TXT_FAINT)
        c.create_text(cx, cy + 22, text="点击切换", font=self.f_small,
                      fill=TXT_FAINT)

    def _draw_chart(self):
        c = self.chart
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 80 or h < 50:
            return

        pad_l, pad_r, pad_t, pad_b = 48, 24, 16, 22
        x0, x1 = pad_l, w - pad_r
        y0, y1 = pad_t, h - pad_b

        # 两条曲线各自独立缩放（电压几百伏、功率几十瓦，共用一轴会互相压平）
        data = {}
        for key, idx, color in (("volt", 1, COL_VOLT), ("watt", 2, COL_WATT)):
            vals = [p[idx] for p in self.samples if p[idx] is not None]
            # 全 0 视为无有效读数，不画成一条贴底的假线
            if len(vals) >= 2 and max(abs(v) for v in vals) > 0.5:
                lo, hi = min(vals), max(vals)
                if hi - lo < max(0.5, hi * 0.02):
                    pad = max(0.5, hi * 0.02)
                    lo, hi = lo - pad, hi + pad
                data[key] = (vals, lo, hi, color)

        # 网格 + 左侧电压刻度
        for i in range(5):
            y = y0 + (y1 - y0) * i / 4
            c.create_line(x0, y, x1, y, fill=LINE, dash=(2, 4))
            if "volt" in data:
                _, lo, hi, _ = data["volt"]
                v = hi - (hi - lo) * i / 4
                txt = f"{v:.1f}" if (hi - lo) < 30 else f"{v:.0f}"
                c.create_text(x0 - 7, y, text=txt, font=self.f_small,
                              fill=mix(CARD, COL_VOLT, 0.55), anchor="e")

        if not data:
            n = len(self.samples)
            msg = ("暂无有效读数（电压与功率都是 0）" if n >= 2
                   else "等待数据…")
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                          text=msg, font=self.f_small, fill=TXT_FAINT)
            return

        for vals, lo, hi, color in data.values():
            n = len(vals)
            coords = []
            for i, v in enumerate(vals):
                x = x0 + (x1 - x0) * i / max(1, n - 1)
                y = y1 - (y1 - y0) * (v - lo) / (hi - lo)
                coords.extend([x, y])
            c.create_line(*coords, fill=color, width=2, smooth=True,
                          capstyle="round", joinstyle="round")
            c.create_oval(coords[-2] - 3, coords[-1] - 3,
                          coords[-2] + 3, coords[-1] + 3,
                          fill=color, outline="")
            c.create_text(x1 - 4, coords[-1] - 11, text=f"{vals[-1]:g}",
                          font=self.f_small, fill=color, anchor="e")

        # 图例（带底色，避免压住曲线）
        lx = x0 + 2
        c.create_rectangle(lx, y0 - 4, lx + 156, y0 + 18, fill=CARD, outline="")
        for color, name in ((COL_VOLT, "电压 V"), (COL_WATT, "功率 W")):
            c.create_line(lx + 4, y0 + 7, lx + 18, y0 + 7, fill=color, width=2)
            c.create_text(lx + 23, y0 + 7, text=name, font=self.f_small,
                          fill=TXT_DIM, anchor="w")
            lx += 74

        c.create_text(x1, h - 6, text="两条曲线各自缩放，看趋势", font=self.f_small,
                      fill=TXT_FAINT, anchor="e")

    # ---------------- 行为

    def _log(self, kind: str, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{ts}  {msg}\n", kind)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _cycle_interval(self):
        opts = [1.0, 2.0, 5.0, 10.0]
        i = opts.index(self.interval) if self.interval in opts else 1
        self.interval = opts[(i + 1) % len(opts)]
        self.btn_iv.configure(text=f"刷新 {self.interval:g}s")
        if hasattr(self, "chart_card"):
            self.chart_card.title_label.configure(text=self._chart_caption())  # type: ignore[attr-defined]
        self._log("info", f"轮询间隔改为 {self.interval:g} 秒")

    def _connect(self):
        host = self.var_host.get().strip()
        if not host:
            self._log("warn", "请先填设备地址")
            return
        self.host = host
        self.device = Device(host)
        save_host(host)
        self.samples.clear()
        self._log("info", f"正在连接 {host} …")
        self.wake.set()

    def _scan(self):
        if self.scanning:
            return
        cidr = local_cidr()
        if not cidr:
            self._log("err", "推断不出本机网段，请手动在地址栏填 IP")
            return
        self.scanning = True
        self.btn_scan.configure(text="搜索中…", state="disabled")
        self._log("info", f"扫描 {cidr} …（约 10 秒）")

        def done(found, err):
            self.q.put(("scan_done", found, err))

        def prog(msg):
            self.q.put(("scan_prog", msg))

        threading.Thread(target=scan_lan, args=(cidr, done, prog), daemon=True).start()

    def _dial_click(self, _event=None):
        if time.time() < self._armed_at:
            self._log("warn", "窗口刚打开，忽略这次点击（防止误切电源）")
            return
        self._send("Power Toggle")

    def _send(self, command: str):
        if self.busy:
            self._log("warn", "上一条命令还在执行")
            return
        if not self.last.get("online"):
            self._log("warn", "设备未连接，命令未发送")
            self.wake.set()
            return
        self.busy = True
        dev = self.device

        def work():
            try:
                r = dev.cmd(command)
                self.q.put(("log", "ok", f"{command} → {json.dumps(r, ensure_ascii=False)}"))
            except Exception as e:  # noqa: BLE001
                self.q.put(("log", "err", f"{command} 失败：{e}"))
            finally:
                self.busy = False
                self.wake.set()

        threading.Thread(target=work, daemon=True).start()

    # ---------------- 轮询与消息泵

    def _poll_loop(self):
        while self.running:
            if not self.host:
                self.wake.wait(1.0)
                self.wake.clear()
                continue
            snap = self.device.snapshot()
            self.q.put(("state", snap))
            self.wake.wait(self.interval)
            self.wake.clear()

    def _drain(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "state":
                    self._apply_state(msg[1])
                elif kind == "log":
                    self._log(msg[1], msg[2])
                elif kind == "scan_prog":
                    self._log("info", msg[1])
                elif kind == "scan_done":
                    self._apply_scan(msg[1], msg[2])
        except queue.Empty:
            pass
        if self.running:
            self.root.after(120, self._drain)

    def _apply_state(self, s: dict):
        prev_online = self.last.get("online")
        self.last = s

        if not s.get("online"):
            self.lamp.itemconfigure(self.lamp_dot, fill=COL_ALERT)
            self.lbl_link.configure(text="离线", fg=COL_ALERT)
            self.lbl_power.configure(text="离线", fg=COL_ALERT)
            self.lbl_pos.configure(text=s.get("error", ""), fg=TXT_FAINT)
            for w in self.readouts.values():
                w.configure(text="—")
            self._draw_dial()
            if prev_online is not False:
                self._log("err", f"连接失败：{s.get('error', '未知')}")
            return

        was_offline = prev_online is False
        self.lamp.itemconfigure(self.lamp_dot, fill=COL_ON)
        self.lbl_link.configure(
            text=f"在线 · {s['rtt_ms']:.0f} ms", fg=TXT_DIM)
        if was_offline or prev_online is None:
            self._log("ok", f"已连接 {s['ip']} · {s['version']} · {s['hardware']}")

        on = s.get("power") == "ON"
        self.lbl_power.configure(text="开" if on else "关",
                                 fg=COL_ON if on else TXT_DIM)
        pos_map = {0: "上电后保持关闭", 1: "上电后自动开启", 2: "上电后恢复断电前状态",
                   3: "恢复断电前状态", 4: "上电后开启并延时", 5: "上电后先开再关"}
        pos = s.get("poweronstate")
        self.lbl_pos.configure(text=f"上电行为：{pos_map.get(pos, '未知')}", fg=TXT_FAINT)
        self._draw_dial()

        def fmt(v, unit="", nd=1):
            if v is None:
                return "—"
            try:
                return f"{float(v):.{nd}f}"
            except (TypeError, ValueError):
                return str(v)

        self.readouts["voltage"].configure(text=fmt(s.get("voltage"), nd=0))
        self.readouts["watt"].configure(text=fmt(s.get("watt"), nd=1))
        self.readouts["current"].configure(text=fmt(s.get("current"), nd=3))
        self.readouts["factor"].configure(text=fmt(s.get("factor"), nd=2))
        self.readouts["today"].configure(text=fmt(s.get("today"), nd=3))
        self.readouts["total"].configure(text=fmt(s.get("total"), nd=3))

        self.samples.append((s["ts"], s.get("voltage"), s.get("watt")))
        self._draw_chart()

        rssi = s.get("rssi")
        self.lbl_dev.configure(
            text=(f"{s['hostname']}  ·  MAC {s['mac']}  ·  Wi-Fi {s['ssid']} "
                  f"({rssi} dBm)  ·  运行 {s['uptime']}  ·  {s['version']} / {s['hardware']}")
        )

    def _apply_scan(self, found, err):
        self.scanning = False
        self.btn_scan.configure(text="搜索设备", state="normal")
        if err:
            self._log("err", err)
            return
        if not found:
            self._log("warn", "没找到 Tasmota 设备。检查插头是否通电、是否在同一个网段")
            return
        for ip, ver, name in found:
            self._log("ok", f"找到 {ip}  {name}  {ver}")
        ip, ver, name = found[0]
        self.var_host.set(ip)
        self._connect()

    def _on_close(self):
        self.running = False
        self.wake.set()
        self.root.destroy()


# ---------------------------------------------------------------- 入口


def main():
    ap = argparse.ArgumentParser(description="Tasmota 插头控制台（桌面版）")
    ap.add_argument("--host", default=None, help="设备 IP（默认读 ~/.config/tasmota/plug.conf）")
    ap.add_argument("--interval", type=float, default=2.0, help="轮询间隔秒数，默认 2")
    ap.add_argument("--selftest", action="store_true", help="不进入事件循环，仅自检并退出")
    a = ap.parse_args()

    host = a.host or load_host()
    root = tk.Tk()
    app = App(root, host, a.interval)

    if a.selftest:
        for _ in range(6):
            root.update_idletasks()
            root.update()
            time.sleep(0.15)
        print("selftest: 窗口", root.winfo_width(), "x", root.winfo_height())
        print("selftest: 在线 =", app.last.get("online"), "| 开关 =", app.last.get("power"))
        print("selftest: 采样点 =", len(app.samples))
        app.running = False
        root.destroy()
        return 0

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
