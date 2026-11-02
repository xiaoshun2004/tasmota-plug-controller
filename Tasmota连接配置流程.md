# Tasmota 连接与配置流程（依据官方文档整理）

> 来源：
> - <https://tasmota.github.io/docs/Getting-Started/>（"Initial Configuration" 与 "Common Mistakes" 两节）
> - <https://github.com/arendst/tasmota>（README：固件定位与支持芯片）
>
> 说明：下面每条都标了原文出处。**官方流程的前提是设备已经是 Tasmota 固件**（自己刷的，或出厂预刷的）。手上如果是原厂涂鸦固件（比如 CozyLife 热点那种），走不到第一步，得先刷机——见 README 的芯片判定表。

---

## 阶段一：首次配网（Web UI 方式，官方主推）

### 1. 连它的热点

原文：

> "Tasmota provides a wireless access point for easy Wi-Fi configuration."
> "Search for a Wi-Fi AP named tasmota_XXXXXX-#### (where XXXXXX is a string derived from the device's MAC address and #### is a number) and connect to it."

- 热点名格式 **`tasmota_XXXXXX-####`**，前六位来自 MAC，后四位是数字，例如 `tasmota_3D5E26-7718`。
- 手机连上后可能弹"此网络无互联网连接，是否切换"——**原文明确要求不要切**：

> "Do not allow the mobile device to select a different network."

### 2. 时间窗口（最容易被坑的一条）

> "Wi-Fi manager server is active for only 3 minutes. If you miss the window you might have to disconnect your device from power and reconnect."

**配网热点只在上电后 3 分钟内有效。** 过了就拔电重插，重新计时。

### 3. 打开 192.168.4.1 填 Wi-Fi

> "After you have connected to the Tasmota Wi-Fi AP, open http://192.168.4.1 in a web browser"

页面顶部会列出扫到的 Wi-Fi，选一个会自动填进 SSID 栏。填表要点（原文）：

| 字段 | 要求 |
|---|---|
| WiFi Network | "SSID's are case sensitive" —— **大小写敏感**，别手打错 |
| WiFi Password | "has to be under 64 characters and without special characters (e.g. asterisks) or white spaces" —— 少于 64 字符，**不能有特殊符号和空格** |

勾选显示密码的复选框，确认无误后点 **Save**。

### 4. 保存之后

> "If it was successful, you will see this message... Some phones will redirect you to the new IP immediately, on others you need to click the link to open it in a browser."
> "The tasmota_XXXXXX-#### network will no longer be present."

失败的话：

> "In case the network name or password were entered incorrectly, or it didn't manage to connect for some other reason, Tasmota will return to the 'Wi-Fi parameters' screen with an error message."

### 5. 找它的新 IP

官方给的办法是查路由器、或用 IP 扫描器（Fing / Angry IP Scanner / Super Scan）。**本仓库给了替代品**，不用装 Java：

```bash
./plug.py discover    # mDNS + ARP 探测，找到就记住 IP
./scan.py             # 不依赖 mDNS 的全网段扫描，最可靠
```

---

## 阶段二：Web UI 里的配置（三处，按顺序做）

### 1. 告诉它自己是什么硬件 —— Configure Module / Configure Template

原文：

> "Set up your device's feature using a Template in **Configuration - Configure Template** or Module in **Configuration - Configure Module**."

二选一：

- **Configuration → Configure Module**：从下拉里选对应模组（插头一般是 `Sonoff Basic`、`Generic` 之类），保存后自动重启。
- **Configuration → Configure Template**：粘一段 JSON 模板，适合模组库里没有的杂牌设备。模板库：<https://templates.blakadder.com/>

选错的现象：能开网页但按开关没反应——那是 GPIO 没映射对，回去换模组或改模板。

### 2. 起个名字 + 设网页密码 —— Configure Other

原文：

> "Configure your device name which is displayed in webUI and used for Home Assistant discovery."
> "Configure web admin password for the webUI. Default username is `admin`. This type of security is rudimentary since Tasmota doesn't use HTTPS, do not expose your device outside of your local network."

要点：

- **Device Name** 会同时用于 Home Assistant 自动发现，起个有意义的（`plug-kitchen`）。
- 网页密码的用户名固定是 `admin`，密码自己设。**原文明确说这是弱保护（没有 HTTPS），别把设备暴露到公网。**

### 3. 接 MQTT（要用 HA / 联动才需要）—— Configure MQTT

原文给的命令行等价写法：

```
Backlog mqtthost <yourhost>; mqttuser <user>; mqttpassword <password>; topic <unique_topic>; otaurl http://ota.tasmota.com/tasmota/release/tasmota.bin
```

也就是五个字段：

| 字段 | 含义 | 本机示例 |
|---|---|---|
| `MqttHost` | broker 地址 | `192.168.3.28`（装 mosquitto 的这台） |
| `MqttUser` / `MqttPassword` | broker 凭据 | 匿名可留空 |
| `Topic` | 设备唯一主题 | `plug1` |
| `FullTopic` | 默认 `%prefix%/%topic%/` | 保持默认即可 |
| `OtaUrl` | OTA 固件地址 | 保持默认 |

配好后的主题规律（来自 Commands 文档）：命令发到 `cmnd/plug1/Power`，状态回在 `stat/plug1/RESULT`、`stat/plug1/STATUS`。

---

## 阶段三（可选）：串口方式配置，跳过热点那套

如果你是用串口线刷的机，官方建议顺手在串口里把 Wi-Fi 配完，省掉 AP + 3 分钟窗口的麻烦：

> "If you flashed the device using serial programmer (or it is a NodeMCU/D1 mini) you can take advantage of the existing connection and configure your device over the serial connection"

```
Backlog ssid1 <yourssid>; password1 <your_password>; ssid2 <your_ssid2>; password2 <your_password>
```

终端设置（原文要点）：

> "Enable local echo so that you can see what is typed... Enable **Append CR+LF** since every request needs to end with `<CR><LF>`."

Linux 上直接用 `minicom` 或 `picocom`：

```bash
picocom -b 115200 /dev/ttyUSB0 --imap lfcrlf
```

设备重启后会打印新分配的 IP，浏览器打开即可。

---

## 阶段四：收尾（官方警告）

> "If you experience power fluctuations in your power grid it's best to immediately disable Power Cycle Recovery feature with command `SetOption65 1` immediately or you might end up with firmware defaults on your device."

电网不稳（或你经常拔插头）时，建议先执行 `SetOption65 1`，否则反复断电可能把配置打回默认。

---

## 附：Common Mistakes —— 官方列的 7 条（全是刷机阶段，不是网络配置）

| # | 原文 | 中文 |
|---|---|---|
| 1 | "Double check all connections and also check for solder overflow." | 复查接线和焊点，注意焊锡连锡短路 |
| 2 | "Some USB cables are for charging only and do not connect the data lines" | **只能用数据线**，充电线没有数据芯，烧不进去 |
| 3 | "Insufficient power delivered over the serial programmer... leads to flashing failures or corrupted flash" | 编程器供电不足会刷失败甚至刷坏，可外接 3.3V，**必须共地** |
| 4 | "ensure that it supplies 3.3V voltage and **NOT 5V**. 5V will damage the ESP chip!" | 电平必须是 3.3V，**5V 直接烧芯片** |
| 5 | "It is safe to leave GPIO0 connected to GND during the entire programming process... remove before booting" | 整个刷写过程 GPIO0 接地是安全的，正常启动前要断开 |
| 6 | "Make sure that the RX pin is connected to the TX pin... some devices can have the TX and RX pins labelled in reverse" | RX/TX 交叉接；有些板子丝印是反的，那就同名对接 |
| 7 | "Erase the flash first and power cycle before uploading... Not erasing can leave behind remnants of the previous flash contents" | 先擦除、断电重启、再写固件；不擦除会残留旧固件干扰运行 |

---

## 一句话对照：你这颗插头现在走到哪一步

| 阶段 | 你的 Xerhom QSTASPG10 |
|---|---|
| 已是 Tasmota 固件 | ❌ 不是。热点是 `cozylife-936340-0832`，涂鸦 OEM 方案 |
| 因此"阶段一"能否走 | ❌ 走不了，没有 `tasmota_` 热点可连 |
| 要先做的 | 判定芯片（`./scan.py --arp` 看 MAC 厂商）。Espressif → 刷 Tasmota，之后照本文从头走；Beken → 只能 OpenBeken，本文流程不适用 |
