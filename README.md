# 在 Ubuntu 本机控制 Tasmota 智能插头

一句话结论：**Tasmota 插头就是局域网里的一个小 HTTP 服务器**，用浏览器、curl 或 MQTT 都能控制。
Ubuntu 完全够用，Windows 虚拟机没有优势 —— 而且 VM 如果用 NAT 网络，还发现不到设备，纯属添麻烦。

> 本文档里所有 `192.168.3.46` 都是**示例内网地址**，换成你自己设备的实际 IP 即可。
> 设备 MAC 已脱敏为 `<MAC>`。

---

## 0. 先确认前提

插头必须满足两条：**已刷 Tasmota 固件** + **已连上你家 Wi-Fi**。

- 品牌插头（Sonoff S26/S31、Gosund、Nous 等）大多要先拆开用 USB-TTL 刷一次机，这是唯一"看起来需要 Windows"的环节，其实 Ubuntu 直接能干：
  ```
  pip install esptool
  esptool.py --port /dev/ttyUSB0 write_flash 0x0 tasmota.bin
  ```
  不想碰命令行：用 Chrome/Edge 打开 <https://tasmota.github.io/install/>，WebSerial 直接网页刷，Linux 也支持。
- 已经刷好但没配网：手机会出现 `tasmota_XXXXXX-####` 热点（注意是下划线）→ 连上 → 浏览器打开 `http://192.168.4.1` → 填 SSID 和密码 → Save。**这个配网热点只在上电后 3 分钟内存在**，错过就拔电重插。

满足之后，机器上什么都不用装（脚本只用 Python 标准库）。

### 芯片决定一切：先看模组丝印

拆开外壳（插头一般是卡扣，撬棒沿缝隙走一圈，别用金属），看里面那颗 Wi-Fi 模组上的丝印：

| 丝印/型号 | 芯片 | 能不能刷 Tasmota | 怎么办 |
|---|---|---|---|
| `ESP-WROOM-02`、`ESP-12F`、`ESP8285`、`TYWE2S`、`TYWE3S` | ESP8266 | ✅ 官方主目标 | USB-TTL 直刷 |
| `WT32`、`ESP32-C3-MINI`、`ESP32-SOLO` | ESP32 系列 | ✅ 官方支持 | USB-TTL 直刷 |
| `CB2S`、`CBU`、`BK7231N`、`BK7231T` | 博通集成 BK7231 | ❌ 刷不了 | 改刷 **OpenBeken**（另一套固件，也能接 HA） |
| 只有一颗光板无字/`RTL8710` / `BL2028` | 非 ESP | ❌ | 放弃刷机，走原厂 App |

国内电商买的涂鸦系插座，九成是 BK7231——**这不是 Tasmota 的地盘**，别浪费时间。想省事就直接买出厂预刷 Tasmota 的型号（Athom、Nous A1T、CloudFree，或淘宝搜"Tasmota 插座"），插上电配个网就能用，下面所有步骤照走。

---

## 1. 找到插头的 IP

```bash
avahi-browse -alrt | grep -i tasmota      # 主机名一般叫 tasmota-1a2b3c
# 或者直接跑本目录脚本
./plug.py discover
./scan.py                                 # 不依赖 mDNS/D-Bus 的全网段扫描，最可靠
```

`scan.py` 是兜底方案：并发连 80 端口 + ICMP 扫全段，再对每台 `GET /cm?cmnd=Status%202` 看有没有 `StatusFWR`，命中就是 Tasmota；顺带打印 MAC 厂商，一眼能看出是不是乐鑫的芯片。`--arp` 只看存活设备，`--all` 连非 Tasmota 的 80 端口设备也列出来。

`plug.py discover` 会先扫 mDNS，再把局域网 ARP 表里的每一台都探一遍，找到后自动写进 `~/.config/tasmota/plug.conf`，以后不用再带 IP。

找不到的两个常见原因：手机连着 5G 而插头在 2.4G 的另一个 SSID 下，且**路由器把两段做了隔离**（默认情况下 2.4G 与 5G 桥接在同一 LAN，IP 层互通，控制端走 5G 一样能控 2.4G 设备；只有开了 AP 隔离 / 访客网络 / IoT 独立网段时才会互不可见）；路由器开了 AP 隔离。这两个都要去路由器后台关掉。若全段扫下来连一台 ESP 芯片的设备都没有，那就是插头**根本没上电/没连上网**——先解决这个，别在电脑这边折腾。

> **频段这件事要说准**：ESP8266/ESP8285 只有 2.4G 射频，所以**插头自己**只能连 2.4G；但**控制端**（电脑、手机）连 2.4G 还是 5G 都无所谓，判据只有一个 —— 双方 IP 是否在同一网段（如都在 `192.168.3.0/24`）。

### 扫不到设备时，按这个顺序判定

再用手机（电脑没有无线网卡，看不到热点）。打开 Wi-Fi 列表，看插头有没有自己开热点。

**注意命名：配网热点的 SSID 是 `tasmota_XXXXXX-####`（前缀后面是下划线），不是连上网络后的主机名 `tasmota-xxxxxx`（连字符）。** 官方原文："Search for a Wi-Fi AP named tasmota_XXXXXX-####"，示例 `tasmota_3D5E26-7718`。

| 手机看到的热点名 | 说明 | 下一步 |
|---|---|---|
| `tasmota_XXXXXX-####` | **已刷 Tasmota，未配网**，在 AP 模式 | 连它 → 浏览器开 `http://192.168.4.1` → 填 SSID 和密码 → Save |
| `SmartLife-XXXX` / `Tuya_XXXX` | 原厂涂鸦固件 | 拆机看芯片；ESP 就刷 Tasmota，BK7231 刷 OpenBeken |
| `ITEAD-XXXX` / `Sonoff-XXXX` | 原厂易微联固件 | 芯片必是 ESP8266，拆机 UART 刷 |
| 什么都没有 | 已连上某个 Wi-Fi，或压根没通电，或**错过了配网窗口** | 见下方「3 分钟窗口」 |

**3 分钟窗口（最容易踩的坑）**：官方文档明确写 "Wi-Fi manager server is active for only 3 minutes. If you miss the window you might have to disconnect your device from power and reconnect." 也就是说配网热点只在**上电后 3 分钟内**存在，过了就消失。所以查热点必须在刚通电的头几分钟内做，错过了就拔电重插再来一次。

**关于指示灯，别信"常亮=已联网"这种说法。** Tasmota 的灯是可配置的，官方 `LedState` 默认值是 `1`：

> `1` = show power state on LED (LED on when power on) **(default)**

也就是说**默认状态下 LED 只是跟着继电器的通断亮灭**，跟你有没有连上 Wi-Fi 没关系——插头通电、LED 就亮。只有在 LED 被配置成 link status LED（`LedLink`/`LedLinki`，或多 LED 设备上的第一个 LED）时，官方文档才说它会在未连上 Wi-Fi/AP 和 MQTT 时闪烁：

> "It blinks if the device is not connected to your Wi-Fi AP and MQTT broker (if MQTT is enabled)."

而且 `LedState 0` 可以整个关掉灯，`SetOption31 1` 还能让它断网时也不闪。**所以指示灯只能当弱参考，判定必须靠热点和路由器。**

判定顺序：**看热点名定固件 → 看路由器 DHCP 列表定网段 → 灯只作参考。**


多端口扫描可以区分"原厂还没刷"和"已刷 Tasmota"：

```bash
./scan.py --ports    # 6668=涂鸦原厂  8081=Sonoff 原厂  80=Tasmota/路由器
```


**推荐做法：在路由器后台做 DHCP 地址保留（绑定 MAC）。** 设备端保持 DHCP 不动。

设备端也能写死静态 IP（`IPAddress1 192.168.3.46` + `IPAddress2` 网关 / `IPAddress3` 掩码 / `IPAddress4` DNS），但**对这颗插头不要这么干**——见下方「关于 IP 会不会变」。如果确实要设，四条必须成套写，只改 `IPAddress1` 会导致没有网关。

---

### 关于 IP 会不会变（2026-11-02 实测）

先纠正一句说过头的话：**DHCP 不等于每次重启都换地址。** DHCP 是按租约续期的，同一 MAC 在租约到期前会一直续到同一个地址，所以日常用起来是稳定的。会变的情况只有：路由器恢复出厂 / 换路由器；设备长时间离线期间地址被别的设备占走；设备连到了另一个网络。

实测这台插头的当前配置：

```
IPAddress1 -> 0.0.0.0 (192.168.3.46)   ← 0.0.0.0 = 走 DHCP，括号内是实际地址
IPAddress2 -> 192.168.3.1              ← 网关（已填）
IPAddress3 -> 255.255.255.0            ← 掩码（已填）
IPAddress4 -> 192.168.3.1              ← DNS（已填）
```

也就是说静态配置的架子其实已经就位（网关/掩码/DNS 都有值），只差 `IPAddress1` 一项。

**但仍然不建议补上这一项。** 这颗插头要拿去别的网络联调：一旦写死 `192.168.3.46`，接到 `192.168.1.x` 的网段就彻底失联，且没有任何网页入口能改回来（`/wi` 打不开），只能拆机接串口——死锁。保持 DHCP，换网络它会自适应。

**唯一既固定地址又不会变砖的方案，是路由器侧绑定 MAC**：MAC `<MAC>` → 保留 `192.168.3.46`。

---

## 2. 三条控制路线，按需选

### 路线 A：浏览器（最快上手，日常够用）

打开 `http://<插头IP>/` 就是 Tasmota 自带的 Web UI，主界面那个大按钮直接开关。
点 **Console** 可以敲所有 Tasmota 命令，比如：

```
Power toggle          翻转
Power1 off            关（Power 等同 Power1，Power0 控全部输出）
Status 0              一行看全部状态
Backlog Power1 on; Delay 50; Power1 off     组合命令，最多 30 条
Timer1 {"Enable":1,"Time":"07:30","Days":"1111111","Action":1}    每天 7:30 开
PowerOnState 1        上电自动通电（0=保持断电 1=通电 2=恢复断电前 3=翻转）
```

### 路线 B：命令行 / 脚本（本目录的 plug.py）

```bash
alias plug="$PWD/plug.py"        # 写进 ~/.bashrc 省事

plug discover                    # 找设备并记住 IP
plug on                          # 通电   -> 192.168.1.50  ->  ON
plug off
plug toggle
plug status                      # 开关 + IP/信号 + 功率（带计量的型号才有）
plug energy
plug raw "Backlog Power1 On; Delay 100; Power1 Off"   # 任意命令
plug --host 192.168.1.51 on      # 临时指定另一台
```

底层就一条 HTTP，愿意手敲也行：

```bash
curl "http://192.168.1.50/cm?cmnd=Power%20On"          # {"POWER":"ON"}
curl -u admin:密码 "http://192.168.1.50/cm?cmnd=Status%200"
```

注意空格要编码成 `%20`，分号 `%3B`。脚本里已经帮你处理了。

**桌面快捷方式**：在 `~/.local/share/applications/` 放一个 `.desktop` 文件，`Exec=/path/to/plug.py toggle`，就能丢到 Dock 上点。

### 路线 C：MQTT + Home Assistant（要做自动化/联动才上）

Tasmota 是 MQTT 客户端，你需要一个 broker。Ubuntu 上：

```bash
sudo apt install mosquitto mosquitto-clients
# Tasmota 控制台里配：MqttHost 192.168.1.x  MqttTopic  plug1
mosquitto_sub -t 'stat/plug1/#' -v          # 看它发的所有状态
mosquitto_pub -t 'cmnd/plug1/Power' -m 'TOGGLE'
```

要做"定时/离家自动关/接语音助手"，装 Home Assistant（Docker 一条命令）：

```bash
docker run -d --name ha --network host --restart unless-stopped \
  -v ~/ha:/config ghcr.io/home-assistant/home-assistant:stable
```

HA 里加 **Tasmota** 集成，填 broker 地址；设备会自动通过 `tasmota/discovery` 出现（老教程里的 `SetOption19 1` 已经淘汰了，新版默认自带发现）。

---

## 3. 顺手值得设的几项

| 目的 | 命令（Console 里敲） |
|---|---|
| 改个能认出的名字/主机名 | `Hostname plug-kitchen` / `DeviceName 咖啡机` |
| 上电恢复状态 | `PowerOnState 2`（恢复断电前状态，冰箱/路由器场景有用） |
| Web UI 加密码 | 配置页 → 其它配置 → Web 密码设 `admin` / 你的密码 |
| 断网自动重启 | `WifiConfig 4`（连不上就开 AP 让人救） |
| 关掉没用的话题上报 | `TelePeriod 300`（默认 300 秒，调大省流量） |

---

## 4. 关于 Windows 虚拟机（如果你还想用）

可以，但注意两点：

1. **网卡设成桥接（Bridged），不要 NAT。** NAT 下 VM 只能主动出网，mDNS 广播收不到，`tasmota-xxxx.local` 也解析不了，只能靠 IP 硬连。
2. 装个 GUI 工具图省事：**TasmoManager**（Windows/macOS/Linux 三平台，GitHub `tom-23/TasmoManager`）能发现设备、点按开关、改 MQTT/WiFi 配置。它 2021 年后就没更新了，日常开关够用，别指望新版特性。Windows 那边还有个 Tasmota Device Manager，只能批量刷机/改配置，不如直接用浏览器。

结论还是那句话：插头在局域网里，谁在同一网段谁就能控。Ubuntu 原生的 `curl` + 这个脚本已经是最短路径。

---

## 参考文档（结论的出处）

| 内容 | 出处 |
|---|---|
| AP 热点名 `tasmota_XXXXXX-####`、默认 IP `192.168.4.1`、**配网窗口 3 分钟** | [Getting Started](https://tasmota.github.io/docs/Getting-Started/) |
| `LedState` 默认 `1` = 跟随继电器电源、`LedPower`/`LedMask` 取值 | [Commands](https://tasmota.github.io/docs/Commands/#ledstate) |
| Link LED 断网时闪烁，仅在 LED 被配为 link status 时成立 | [Status LED](https://tasmota.github.io/docs/Status-LED/) |
| HTTP 控制接口 `cm?cmnd=`、`Power`/`Status`/`Timer`/`Backlog` | [Commands](https://tasmota.github.io/docs/Commands/) |
| 支持芯片范围（ESP8266/ESP32 全系）、MQTT/HTTP/KNX 控制 | [GitHub 仓库](https://github.com/arendst/tasmota) |
| Web 版刷机（需 Chrome/Edge 的 Web Serial） | [Tasmota Web Installer](https://tasmota.github.io/install/) |

注意：`https://tasmota.github.io/docs/LEDs/` 这个路径是 404，LED 的说明分散在 Commands 的 `LedState` 条目和 Status-LED 页面里。

---

## 附：Xerhom QSTASPG10 实测记录（2026-11-02）

**已知（从外壳丝印读出）**：品牌 Xerhom，型号 QSTASPG10，输入 AC 100–240V，最大 10A，CE / FCC / RoHS，美标三脚（带地），机身是常见的 50×50mm 白色方块。

**未知（查不到资料）**：这颗用的是什么芯片。搜 `QSTASPG10` 在网上没有任何固件/拆机记录，品牌也没有公开的方案说明。所以**目前无法确定能不能刷 Tasmota**。

**判定芯片的两条路**：

1. **不拆机**：先用手机 App（Smart Life / Tuya Smart）把它连上家里 2.4G Wi-Fi，然后跑 `./scan.py --arp` 看它的 MAC 厂商：
   - `Espressif` → ESP8266/ESP32，**能刷 Tasmota**
   - `Beken` / 厂商名含 Tuya → BK7231 系，**只能刷 OpenBeken**
   - 其他 → 拆机确认
2. **拆机**：撬开外壳看模组丝印（`TYWE2S`=ESP8266 / `CB2S`/`CBU`=BK7231）。

**已排除**：2026-11-02 10:09–10:16 三次全网段扫描（ICMP + 80/443/6668/8081/1883/8080/81），192.168.3.0/24 内没有它的任何痕迹 —— 说明当时它**还没有连上家里 Wi-Fi**，既不是已配置好的 Tasmota，也不是已联网的原厂固件。

**这类白牌插座的通病**：只支持 2.4GHz。华为路由器若开了"双频合一"（2.4G/5G 同名），涂鸦/白牌设备经常连不上。要么在路由器里把 2.4G 单独起个名字，要么开一个 IoT 专用网络。

---

## 附二：CozyLife 热点 → 曾判断是涂鸦方案（**结论已被推翻，见附三**）

手机看到的热点叫 `cozylife-936340-0832`。这说明：

- 设备处在 **AP 配网模式**，等你连上去填 Wi-Fi 账号密码（不是 Tasmota，Tasmota 的热点叫 `tasmota_XXXXXX-####`）。
- CozyLife 是**涂鸦生态的 OEM App**：模组、云端、配网流程都是涂鸦的，只换了 App 外观和热点名前缀。判断依据是配网方式与热点命名结构一致；**最终确认看它连上网后的 MAC 厂商**。

### 为什么 Tasmota 大概率无解

2026 年市面上的廉价涂鸦 Wi-Fi 插座，主控绝大多数是 **BK7231T / BK7231N**（模组丝印 `CB2S`、`CB3S`、`WB2S`、`WB3S`）。Tasmota 只支持 ESP8266/ESP32，**在 BK 芯片上无法执行**。对应的替代固件是 **OpenBeken**（开源，有 Web UI、MQTT、HTTP API，能接 Home Assistant）。

### 三条路，按代价排序

| 路线 | 做法 | 代价 / 门槛 |
|---|---|---|
| **A. 直接用原厂** | CozyLife App 配网，手机 App 控制 | 最省事，但依赖厂商云，Ubuntu 上无法直接控制 |
| **B. 本地协议控制** | 配网后用 `tinytuya` / LocalTuya 走局域网 TCP 6668 | 不用刷机。需要拿到 `device_id` + `local_key`，涂鸦 OEM 云不一定开放给你，可能要 root 手机抓 App 数据库 |
| **C. 刷 OpenBeken** | 免拆机：tuya-cloudcutter；或拆机 UART 焊线 | 需要**带 AP 模式的无线网卡**（这台机没有）；且原厂固件 ≥1.1.15 已被封堵，只能拆机焊线 |

### 两条必须先确认的信息

1. **芯片**：配网成功后跑 `./scan.py --arp`，看 MAC 厂商。`Espressif` = 有救（能刷 Tasmota）；`Beken`/`Tuya` = 只能 OpenBeken。
2. **原厂固件版本**：CozyLife App → 设备 → 设备信息里看。≤1.1.8 有机会 CloudCutter 免拆机；≥1.1.15 必须拆机。

### CloudCutter 的硬件门槛（这条最容易忽略）

tuya-cloudcutter 的工作方式是把电脑的无线网卡切成 AP，诱导设备回连，再借 OTA 通道刷入第三方固件。**必须要一块支持 AP 模式的无线网卡 + NetworkManager 管理的 Linux**。这台 HP 台式机没有任何无线网卡，跑不了。解决办法：插一个支持 AP 模式的 USB 无线网卡（RTL8812AU / RT5370 这类），或用树莓派、笔记本。

### 结论建议

如果目标只是「在 Ubuntu 上控制这个插头」而不是「一定要 Tasmota」，最省时间的顺序是：**先 A 配网 → 看芯片和固件版本 → 芯片是 Beken 且版本够老就试 C，否则退到 B 或直接换一个出厂预刷 Tasmota 的插头（Athom / Nous A1T，插电配网就能用 `plug.py` 控制）**。

> 官方的**连接与配置流程**（连 AP → 192.168.4.1 → 填 Wi-Fi → Configure Module/Other/MQTT → Common Mistakes）已单独整理成 **[Tasmota连接配置流程.md](Tasmota连接配置流程.md)**，每条附原文出处。

---

## 附三：更正 —— 实测这颗是 **Tasmota 8.5.0**，不是涂鸦方案（2026-11-02 10:27）

屏幕上 `http://192.168.4.1` 打开的是标准 Tasmota 配网页，页脚是 **"Tasmota 8.5.0 by Theo Arends"**。

**推翻附二的两条判断**：

1. 我说过 `cozylife-936340-0832` 这个热点名"是涂鸦 OEM 的特征"——错。Tasmota 允许改主机名，AP 名会跟着变，所以热点名不能用来判断固件类型。**当时我该等看完 192.168.4.1 的页面再下结论。**
2. 我说过"2026 年这类白牌插座九成是 BK7231、Tasmota 无解"——对这颗不成立。它是卖家预刷的 Tasmota，跑在 ESP 芯片上。

**修正后的结论**：这颗插头可以直接用 Tasmota 控制，`plug.py` / `scan.py` 全部适用，不需要拆机、不需要 CloudCutter。

**但有个版本问题**：8.5.0 是 2020 年前后的固件，之后经历过 8.5.1 → 9.1 → …多代。官方 [Upgrading](https://tasmota.github.io/docs/Upgrading/) 给的路径是 `v7.2.0 → v8.5.1 → v9.1 → Current release`，并警告：

> "Tasmota v9.1 introduced a major change in GPIO mapping. Downgrading is not recommended and will probably not work correctly."

也就是说升到 9.1+ 之后 **GPIO 映射会变，模组/模板要重新核对**。升级前务必先 `Configuration → Backup Config` 备份（原文："Any time you upgrade it is highly recommended to back up your device settings."）。升级时优先用 `.gz` 固件可跳过 minimal 中转；报 "Upload buffer miscompare" 才需要先刷 `tasmota-minimal.bin`，且原文强调 "Do not install only tasmota-minimal.bin but upgrade to full, working firmware."

**建议顺序**：先配网 → 确认能开关 → Configure Module 校准硬件 → 备份配置 → 再考虑升级固件。别一上来就升，8.5.0 对插头这种最简设备够用。

---

## 四、这台插头的实际控制方式（2026-11-02 实测）

设备：`192.168.3.46`，ESP8285N08，Tasmota 13.0.0，SSID `RobotStore`，信号 -34 dBm。
能力已逐项探测：**HTTP API 开**（`HTTP_API:1`）、**16 个定时器**、**Rule 引擎**（Rule1 空闲 511 字符）、`SetOption65` 默认 OFF。

### ① 即时控制（本机，秒级）

```bash
./plug.py on / off / toggle / status
curl "http://192.168.3.46/cm?cmnd=Power%20On"
curl "http://192.168.3.46/cm?cmnd=Backlog%20Power1%20On;%20Delay%20100;%20Power1%20Off"   # 脉冲 1 秒
```

### ② 设备内定时（推荐做主控，电脑关机也照跑）

Tasmota 自带 16 个定时器，存在设备 Flash 里，不依赖 PC。时区已改为 `+08:00`（原为 `+01:00`，差 7 小时，不改定时会错）。

```bash
# 每周一至周五 07:30 开
curl "http://192.168.3.46/cm?cmnd=Timer1%20{\"Enable\":1,\"Mode\":0,\"Time\":\"07:30\",\"Window\":0,\"Days\":\"1111100\",\"Repeat\":1,\"Output\":1,\"Action\":1}"
# 每天 23:00 关（Output:0 = OFF）
curl "http://192.168.3.46/cm?cmnd=Timer2%20{\"Enable\":1,\"Mode\":0,\"Time\":\"23:00\",\"Window\":0,\"Days\":\"1111111\",\"Repeat\":1,\"Output\":1,\"Action\":0}"
curl "http://192.168.3.46/cm?cmnd=Timers%201"      # 定时总开关，默认 OFF，必须打开
```

字段含义：`Days` 七位依次是 周日→周六；`Action` 1=ON、0=OFF、2=TOGGLE；`Window` 为随机延迟秒数（防止整点同时触发）。

### ③ 联动（Rule / MQTT）

本地规则示例——插头通电后 30 秒自动关，不需要任何服务器：

```
Rule1 ON Power1#State=1 DO RuleTimer1 30 ENDON
Rule1 ON Rules#Timer=1 DO Power1 OFF ENDON
Rule1 1
```

要接 Home Assistant 再上 MQTT（`Backlog mqtthost <IP>;mqttuser <u>;mqttpassword <p>;topic plug1`），HA 端用 MQTT 集成，不需要 `SetOption19`（该选项已废弃）。

### 已知短板

- **计量读数全 0**，连电压都是 0V。有市电时正常该读到 220V 左右，模板里映射了三个计量类 GPIO（2624/2656/2720，编号段像 HLW8012 的 CF/CF1/SEL）。推断是卖家拿别的型号模板照抄、实物没有这颗芯片。**不影响开关**，但按功率做自动化（如充满自停）不可行。
- `DeviceName` 仍是默认 `Tasmota`，Topic `cozylife_936340`。改名会重新触发 HA 发现，等定好用哪个名字再改。
- IP `192.168.3.46` 是 DHCP 分的，但不等于重启就换（按租约续期，同一 MAC 通常长期拿回同一地址）。要彻底固定，在华为路由器后台按 MAC `<MAC>` 做**地址保留**；别在设备里写死静态 IP，理由见「关于 IP 会不会变」。

### 计量芯片是 BL0937（10:47 更正）

上一条「可能没有计量芯片」的推断**错了**。用设备自带的 `Gpios` 命令解码模板，三根计量引脚全在：

| GPIO | 功能（设备自报） |
|---|---|
| GPIO4 | `BL0937 CF`（功率脉冲） |
| GPIO5 | `HLWBL CF1`（电压/电流脉冲） |
| GPIO12 | `HLWBL SEL_i`（通道选择，反相） |
| GPIO13 | `Led` |
| GPIO14 | `Relay` |

这是 Sonoff POW 那一系的经典接法，芯片是 **BL0937**（廉价国产计量 IC，Tasmota 用 HLW8012 驱动兼容它）。所以硬件没缺东西，问题在读数。

### 为什么插着运行中的真空泵，功率还是 0

实测 10 次采样（间隔 2 秒）：电压 **307–309 V** 在跳动，功率恒 0，电流恒 0.000，`Total` 一分不累加。

- **电压通道是活的**：CF1 有脉冲进来，说明芯片在测、SEL 切换正常。
- **标定常数全是出厂默认**：`VoltageSetCal=1950`、`PowerSetCal=12530`、`CurrentSetCal=3500`。
- 电压读 308 而市电约 220，偏高约 40% —— 就是默认常数不匹配这块板的硬件参数。

功率/电流恒 0 有两种解释，需要一次大负载才能分辨：
1. 真空泵功率太小（小泵 10–30W，220V 下电流 0.05–0.15A），低于 BL0937 的电流分辨率，脉冲太稀导致功率算成 0；
2. CF 通道没被驱动识别。

**验证办法**：插一个 800–1500W 的纯阻性负载（电水壶、吹风机、电暖器），再 `./plug.py monitor`。有读数 → 是分辨率问题；仍是 0 → CF 通道有问题。

电压标定（可选，需知道实际电压，万用表量最准，没有就用 220 当地参考）：

```bash
curl "http://192.168.3.46/cm?cmnd=VoltageSet%20220"    # 之后读数会被换算成 220 左右
```

### 连续采样

```bash
./plug.py --count 10 --interval 2 monitor    # 注意：选项要写在 monitor 前面
```

---

## 五、可视化面板（dashboard/）

一个本地网页面板：实时读数 + 开关控制，不依赖任何第三方库、不连云端。

```bash
cd dashboard
./server.py                      # 默认读 ~/.config/tasmota/plug.conf
./server.py --host 192.168.3.46 --port 8765
# 浏览器打开 http://127.0.0.1:8765/
```

**为什么要有这一层后端**：Tasmota 的 HTTP 响应**不带 `Access-Control-Allow-Origin`**，浏览器直接 `fetch("http://192.168.3.46/cm?...")` 会被 CORS 拦掉——命令其实发出去了，但读不到返回的 JSON，页面拿不到读数。所以 `server.py` 起在 `127.0.0.1`（只监听本机，局域网其它机器访问不到），前端只跟它说话：

| 端点 | 作用 |
|---|---|
| `GET /` | 面板页面 |
| `GET /api/state` | 代理设备 `Status 0`，整理成扁平 JSON |
| `GET /api/power?cmd=on\|off\|toggle` | 发 `Power` 命令并返回新状态 |

面板内容：圆形开关（点击即切换）+ 开/关/切换三个按钮、电压/功率/电流/功率因数/今日/累计六格读数、最近 3 分钟电压与功率折线（Canvas 手绘，无图表库）、固件/IP/信号/运行时长。数据每 2 秒轮询一次，设备离线时顶部指示灯转红。

**注意**：面板显示的是设备原始读数。目前电压 308V、功率 0W 是设备标定未校的问题（见上一节），面板如实显示，不做美化。

---

## 六、给别人用的说明（不含 server.py）

`dashboard/` 那套是给本机自己用的。如果要把控制权交给别人（同事、现场人员），**不需要把 `server.py` 给他们**——它只是个 CORS 中转，跟插头本身的控制能力无关。

直接转发这两个文件之一：

| 文件 | 用途 |
|---|---|
| [给同事的插头控制说明.md](给同事的插头控制说明.md) | 完整版，三种方式 + 注意事项，每条命令都已实测 |
| [给同事的插头控制说明-微信版.txt](给同事的插头控制说明-微信版.txt) | 纯文本，可直接粘贴到微信/邮件 |

对方需要知道的核心只有三件事：

1. 浏览器打开 `http://192.168.3.46/`，点 **Toggle**（设备固件自带页面，已确认首页有该按钮）
2. 或 `curl "http://192.168.3.46/cm?cmnd=Power%20On"`
3. 定时开关用设备自带 Timer，不依赖任何电脑

**传给外人之前必须先确认的两件事**：
- 插头**没有任何 Web 认证**（实测：无凭据请求 `/cm` 和首页均返回 200，`WebPassword` 查询返回的 `****` 只是固件掩码，不代表设了密码）。谁能访问这个 IP 谁就能控制。
- IP `192.168.3.46` 是 DHCP 分配。人多了之后 IP 一变，所有人的书签和脚本一起失效 → **先去华为路由器按 MAC `<MAC>` 做地址保留**。

---

## 七、改 Wi-Fi（换网络联调）

配置页是 **`http://<IP>/wi`**（不是 `/wificfg`、`/cfg`，那两个 404）。页面上有三个可改的地方：

| 字段 | 元素 id | 说明 |
|---|---|---|
| WiFi Network | `s1` | 主网络，当前 `RobotStore` |
| Alternative WiFi Network | `s2` | 备用网络，当前空 |
| Hostname | `h` | 值是 `%s-%04d`（自动命名模板），**含 `%` 会被重置为默认，别动** |

### 完整页面地图（2026-11-02 逐个探测确认）

`/cn` 是**配置菜单**（`<title>Tasmota - Configuration</title>`），不是总导航；总导航是首页 `/`（Main Menu，带开关按钮和计量读数）。

| 路径 | 标题 | 说明 |
|---|---|---|
| `/` | Tasmota - Main Menu | 总导航 + 开关 + 读数 |
| `/cn` | Tasmota - Configuration | 配置菜单，列出下面各子页 |
| `/cs` | Console | 命令行控制台 |
| `/in` | Information | 设备信息 |
| `/up` | Firmware Upgrade | 固件升级 |
| `/tm` | Configure Timer | **定时器图形界面**（勾选式，含 Sunrise/Sunset） |
| `/wi` | Configure WiFi | Wi-Fi 配置 |
| `/md` | Configure Module | 模组/GPIO 配置 |
| `/mq` | Configure MQTT | MQTT 配置 |
| `/co` | Configure Other | 设备名、Web 密码、经纬度 |
| `/tp` | Configure Template | 模板 |
| `/lg` | Configure Logging | 日志级别 |
| `/rs` | **Restore Configuration** | 恢复配置（⚠️ **不是 Restart**，此前记错） |
| `/dl` | （无标题，直接下载） | Backup Configuration 备份文件 |

未找到 Restart 的独立页面路径（`/rb` `/restart` `/rst` 均 404）；重启用官方命令 `Restart 1`（**未实测**）。`/dz`（Domoticz）404，该固件未编译此模块。

### 优先用双 SSID，不要覆盖主网络

```bash
curl "http://<IP>/cm?cmnd=SSId2%20新网络名"     # 会重启
curl "http://<IP>/cm?cmnd=Password2%20密码"     # 会重启
curl "http://<IP>/cm?cmnd=SSId2%200"            # 清空备用网络，会重启
```

`WifiConfig 4`（当前值）的行为是"先试 SSID1，失败再试 SSID2"，所以配好备用网络后两边都能连，主网络不受影响。

### ⚠️ 改主网络的风险（重要）

官方 Commands 页对 `WifiConfig 4` 的**全部**描述只有一句：

> `4` = "retry other AP without rebooting" _(default)_

**它没有写连不上时会自动开热点。** 开 3 分钟热点（`192.168.4.1`）是 `WifiConfig 2` 的行为。所以当前配置下，如果 SSID 或密码填错，设备只会无限重试，**不会自己开 AP 让人补救**，只能拆机接串口。

要改主网络时，更稳的顺序是先把 `WifiConfig` 设成 `2`（启动一次 3 分钟 Wi-Fi Manager 兜底），再改 SSID。但这条是我的推断——官方只写了"启动 3 分钟后重启并尝试连接"，**没写如果仍连不上会不会再开一次**，未经实测。

### 语法限制（官方原文）

- SSID ≤ 32 字符，密码 ≤ 64 字符
- 二者都**不能含特殊字符或空格**
- `SSId<x> 1` = 恢复出厂默认并重启；`SSId<x> 0` = 清空并重启

---

## 八、命令踩坑记录（2026-11-02 实测）

| 命令 | 结果 |
|---|---|
| `Backlog Power1 On; Delay 3000; Power1 Off` | **危险**：只执行到 `Power1 On`，`Delay` 之后的命令不执行 → **电源保持 ON 不关**。HTTP 请求结束时命令队列被清空所致 |
| `Power1 On 3`（带延时秒数） | 无效，返回 `{"POWER":"OFF"}` 且不动作 |
| `Delay 1000` 单独发 | 有效，返回 `{"Delay":1000}`，但在 Backlog 里不可靠 |
| `Uptime` | `{"Command":"Unknown"}` —— 这颗的固件裁剪了该命令 |
| `Backlog Power; Uptime` | 返回 `{}`，**查询类命令在 Backlog 里拿不到结果**，只能执行动作命令 |
| `Timer1 {...}` 直接拼 URL | `{"Timer1":"Invalid JSON"}`，必须 URL 编码；用 `curl -G --data-urlencode` |

### Rule 的覆盖语义（官方 Rules 页）

```bash
# 正确：多个 ON...ENDON 块写在同一行，空格分隔，一次提交
Rule1 ON Power1#State=1 DO RuleTimer1 30 ENDON ON Rules#Timer=1 DO Power1 OFF ENDON
Rule1 1     # 定义后必须显式启用
```

- 再次发 `Rule1 ...` 是**覆盖**，追加要用 `Rule1 + ON ... ENDON`
- 清空是 `Rule1 "`（双引号）；`Rule1 0` 只停用、不删内容
- 状态判断写 `=1` / `=0`，不要写 `=on` / `=off`
- `RuleTimer` 为 1..8，单位秒

---

## 九、桌面控制台（desktop/，不是网页）

`desktop/` 是一个原生窗口程序，取代 `dashboard/` 那种「起一个本地服务再开浏览器」的方式。

```bash
cd desktop
./run.sh                    # 读 ~/.config/tasmota/plug.conf
./run.sh --host 192.168.3.46
```

**纯标准库（tkinter + urllib），零第三方依赖**，不生成 HTML、不需要常驻后端。

| 区域 | 内容 |
|---|---|
| 顶栏 | 在线状态灯 + 往返延迟 |
| 工具栏 | 地址输入、连接、搜索设备（自动扫本机网段）、刷新间隔切换 |
| 左 | 大圆钮（点击切换）+ 开/关/切换 + 上电行为 |
| 右上 | 电压 / 功率 / 电流 / 功率因数 / 今日 / 累计 |
| 右下 | 电压与功率走势曲线 |
| 底 | 运行日志（绿成功、黄警告、红失败）+ 设备信息条 |

### ⚠️ 必须用系统自带的 python3（这条最容易踩）

```bash
/usr/bin/python3 app.py     # 对
python3 app.py              # 取决于 PATH 指向谁，可能整片方块
```

实测这台机器上两个 Python 的 Tk 能力差很多：

| Python | Tk 版本 | `font.families()` | 中文 |
|---|---|---|---|
| 托管版 3.13（`~/.workbuddy/binaries/python`） | 9.0 | **20 个**，全是 X 核心字体 | 全部显示为方块 |
| 系统 `/usr/bin/python3` 3.10 | 8.6 | **239 个**，含 Noto Sans CJK | 正常 |

托管版的 Tk 编译时没链接 Xft，只能拿到 `fixed`/`courier` 这类老式 X 字体。`run.sh` 会优先选 `/usr/bin/python3`，程序启动时也会自检并在日志里红字提示。

### 两个设计取舍

- **圆钮点击有 1 秒保护期**：窗口刚映射时，X 可能把焦点点击直接投给画布。实测过一次，截图时窗口弹到桌面上被点中，`Power Toggle` 真发了出去（设备当时接着气泵）。所以启动后 1 秒内的点击只记日志、不动作。
- **两条曲线各自缩放**：电压几百伏、功率几十瓦，共用一根 Y 轴会把功率压成直线。所以按各自的最大最小值铺满，只反映趋势相对变化，不能直接比较高低。
