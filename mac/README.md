# Siri Remote → Mac

把初代 Apple Siri Remote 当作 Mac 的输入设备（方向键 / 回车 / Esc / 播放暂停 / 音量）。
协议解码和手势引擎来自 [a-funk/open-appleremote](https://github.com/a-funk/open-appleremote)（原版只支持 Linux），
这里补上了 macOS 缺失的两层。

## 支持哪个遥控器

| 型号 | macOS 能配 | 本项目 |
|---|---|---|
| A1513 / A1962（初代，玻璃触摸板） | ⚠️ 看系统版本，见下 | ✅ 解码器就是为它写的 |
| A2540 / A2854（2、3 代，圆形触控板） | ✅ macOS 11.3 / 13 起原生支持 | ❌ 协议不同，macOS 已把它当普通遥控器，方向键直接可用 |

**初代在 macOS 上的真实状态（26.5.2 实测，拔线验证过）**：**无线可用**。

这个遥控器的 HID 走的是 **BLE HID（HOGP）**，不是妙控键盘那种经典 `HID ACL`。
所以 `Services: 0x400000 < BLE >` 并不代表“没有输入通道”，它本来就是 BLE 设备。
拔掉 USB 后 8 个集合依然在（`path` 变成 `DevSrvsID:...`），按键和触摸都正常。

插 USB 只是多了**一条并行的通道**（Apple 的 DriverKit 用户态 HID 设备 + `usage 65280/11` 的
0xBB feature 诊断通道），不是必需。

触摸表面被系统注册成 `AppleMultitouchDevice`（Transport = Bluetooth Low Energy），
只能走私有 `MultitouchSupport` 读——**内置触控板会出现在同一个设备列表里，必须按 `MTDeviceIsBuiltIn` 过滤掉**，
否则你用笔记本触摸板也会被当成遥控器手势。

另外：按键集合（`usage 12/1`）**同一时间只能被一个进程打开**，跑第二个实例会 `open failed`。

## 一次性的准备

```bash
cd AppleTV-Remote-Mac
python3 -m venv .venv                                   # 已经建好了
.venv/bin/python -m pip install hidapi pyobjc-framework-Quartz
```

配对（无线必需；只用有线的话可跳过）：

1. **Apple TV 断电**（否则遥控器会连回 Apple TV 而不是 Mac）
2. 遥控器按住 **Menu + 音量+** 约 5 秒，指示灯闪
3. Mac：系统设置 → 蓝牙 → 列表最下面出现一串十六进制地址 → 连接
4. 连接后显示的名字是遥控器的序列号（`DLF2MFC94K17` 这种），不是 "Siri Remote"

## 三步用起来（无线 / 有线都行）

**第 1 步：给权限。** 合成按键需要「辅助功能」授权，否则 `CGEventPost` 会被系统静默丢弃
（实测：音量 18 → 18 纹丝不动）。

```bash
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
```

把运行它的终端（Terminal / iTerm / WorkBuddy）勾上；如果用 `run.command` 双击启动，勾 **Terminal** 即可。

**第 2 步：标定方向。**

```bash
.venv/bin/python mac/calibrate.py
```

跟着提示滑四次（右、左、上、下），它会告诉你方向对不对、一次滑动能出几步，
并按需要给出 `FLIP_Y` / `STEP` 的修改建议。不用给权限，它不发任何按键。

**第 3 步：验一下按键真的有效。**

```bash
.venv/bin/python mac/test_keys.py
```

方向键、回车这类一定有效，但**音量和播放/暂停是系统定义键（NX_SYSDEFINED），不认 CGEvent 的 keycode**。
这个脚本用可观测的系统音量判断音量键到底动没动，动不了就走 `osascript` 兜底。

**第 4 步：开跑。**

```bash
open mac/run.command                      # 双击也行，会带 --debug 显示
.venv/bin/python mac/siriremote.py keys   # 或者直接跑，加 --quiet 不打印每次按键
```

## 用法

```bash
.venv/bin/python mac/siriremote.py scan              # 认不认得出遥控器（pid 应为 0x026D）
.venv/bin/python mac/siriremote.py watch             # 只打印解码事件，先确认摸/按都有反应
.venv/bin/python mac/siriremote.py keys              # 真正开始发按键
.venv/bin/python mac/siriremote.py keys --debug      # 附带原始触摸坐标，用来校方向
.venv/bin/python mac/siriremote.py keys --no-touch   # 只开按键，不挂触控回调
```

诊断工具：

```bash
.venv/bin/python mac/diagnose.py             # 四步体检：USB/蓝牙/集合/20 秒实听
.venv/bin/python mac/probe.py --touch --seconds 6   # 逐个集合试读，看谁有输入
.venv/bin/python mac/capture.py              # 分阶段录制（每个动作单独一段）
.venv/bin/python mac/analyze.py mac/captures/cap1.jsonl   # 分析录制：哪一位对应哪个键
.venv/bin/python mac/touch_probe.py          # 单独验证私有触控框架
```

没有硬件也能验：

```bash
.venv/bin/python mac/test_backend.py                                   # 合成报文，11 项自测
.venv/bin/python mac/siriremote.py keys --replay open-appleremote/tests/sample.jsonl --fast --dry-run
```

`keys` 需要**辅助功能**权限，否则合成的按键会被系统静默丢掉：
系统设置 → 隐私与安全性 → 辅助功能 → 勾上你运行它的终端（或 WorkBuddy）。
程序启动时会自检 `AXIsProcessTrusted()`，没授权会打警告。

## 默认键位

| 遥控器 | Mac |
|---|---|
| 触摸板滑动 / 快滑 上·下·左·右 | ↑ ↓ ← → |
| 按下触摸板（click） | Return |
| Menu | Esc |
| TV | 在 ChatGPT / WorkBuddy / Claude 之间轮换 |
| Play/Pause | **Return**（回车） |
| 音量 + / − | **PageUp / PageDown**（翻页） |
| Siri | 不映射 |

**音量键现在是翻页**（PageUp / PageDown，keycode 116 / 121）。想换回系统音量：

```bash
echo '{"volup": "sys:volup", "voldown": "sys:voldown"}' > mykeys.json
.venv/bin/python mac/siriremote.py keys --map mykeys.json
```

顺带记一下音量走系统通道时的实测结论（macOS 26.5.2）：

| 尝试的通道 | 结果 |
|---|---|
| `CGEventCreateKeyboardEvent` + keycode 72/73 | 音量 18 → 18，**无效** |
| `NSEvent.otherEvent` 造 `NX_SYSDEFINED`，post 到 HID tap | 连试三次**无效**（Apple 收紧了这条通道） |
| `osascript -e "set volume output volume N"` | 18 → 22，**有效** |

所以走 `sys:` 时音量用 osascript 读-改-写（±6，带 120 ms 节流），程序启动时会自动测一遍。
播放/暂停的系统定义键同样无效，退回「对正在运行的媒体 App 发 AppleScript」
（Music、Spotify、VLC、IINA，只发正在运行的，不会把没开的拉起来）。

改键位：写个 JSON，值可以是 `CODES` 里的名字（`up`/`return`/`escape`/`space`/`F1`…）、CGKeyCode 数字，或 `null` 关掉。

```json
{"click": "space", "siri": "escape", "tv": null}
```

```bash
.venv/bin/python mac/siriremote.py keys --map my.json
```

## 鼠标模式（触摸板当鼠标）

```bash
.venv/bin/python mac/siriremote.py keys --mouse
open mac/run-mouse.command
```

| 操作 | 效果 |
|---|---|
| 在触摸板上滑动 | 移动鼠标指针（连续移动，不是走格） |
| 按下触摸板（click） | 左键单击；按住再滑 = 拖动 |
| Menu | 右键 |
| TV / 播放暂停 / 音量± | 同键盘模式 |

调参：

```bash
.venv/bin/python mac/siriremote.py keys --mouse --speed 1.2     # 更快，默认 0.85
.venv/bin/python mac/siriremote.py keys --mouse --accel         # 开启速度自适应加速
```

`--speed` 的含义是「1 个遥控器单位 = 多少像素」：满行程 1650 单位，默认 0.85 对应约 1400 px，
差不多一屏宽。实测校准：喂 1500 单位应在屏幕上移动 1275 px（线性模式精确成立）。

实现要点：CGEvent 的鼠标事件是**绝对坐标**，所以自己维护一个浮点位置累加位移再取整发送，
避免每帧各自取整的漂移；按住时改发 `LeftMouseDragged`，拖动才有效。


### 按下时指针不飘

手指按下触摸板会给触点一个位移（皮肤滚动），指针就跟着飘几像素，点击落点也偏。
真触摸板靠压力感应区分「按下」和「移动」，这里没有那个信号，所以用三招模拟：

| 机制 | 参数 | 作用 |
|---|---|---|
| 落点回正 | `SNAP_WINDOW=0.16s`、`SNAP_MAX=55px` | 按下瞬间把指针拉回 160 ms 前的落点。位移超过 55 px 就认定是「有意的移动」，不回正 |
| 按压冻结 | `CLICK_LOCK=0.14s` | 按下后 140 ms 内完全不动指针 |
| 拖动阈值 | `DRAG_START=14px` | 按住后要走超过 14 px 才开始拖动，轻碰不会误拖 |

都在 `mac/pointer.py` 顶部。觉得回正太"抢"，可以关掉：

```bash
.venv/bin/python mac/siriremote.py keys --mouse --no-snap
```

**为什么回正要看「窗口之前的样本」而不是「窗口内最老的样本」**：漂移往往正好从窗口起点开始，
用窗口内的样本会把漂移本身当成基准，回正后还差几像素（实测 9.9 px vs 5 px，真实使用中为 0）。

## 麦克风键（Siri 键）与听写

**遥控器的麦克风在 macOS 上不能用，已实测定论。**

两次独立测试，结论一致：

| 时间 | 条件 | 结果 |
|---|---|---|
| 2026-11-04 | 无线（BLE），8 个集合全监听，按住说话 20 秒 | `12/1` 668 包，**全部 2 字节** `fa10`；其余 7 个集合 0 包 |
| 2026-11-05 | **插着 USB**、`12/1` **独占**（先停主程序）、按住说话 10 秒 | 801 包，**全部 2 字节** `fa10` |

也就是说 HID 层只肯给「麦克风键被按住」这个状态位，**一个音频字节都没有**。
第二次是补测：第一次 `12/1` 被主程序占着（`open failed`），等于没测到关键通道，所以换了有线重做一遍。

**为什么 macOS 拿不到、Linux 能拿到 13 字节**：`PROTOCOL.md` 说遥控器把音频作为
**ATT handle `0x0023` 的 notification、每帧完整 101 字节**发出来；Linux 只拿到 13 字节是因为
BlueZ 用 23 字节 MTU 缓冲读（BlueZ 的 bug，不是遥控器的限制）。

而 macOS 这边，我们读到的报告 ID 是 **`0xFA`**，Linux 是 **`0xFC`** ——
说明 macOS 的 HOGP 驱动把厂商报告**重新映射成了标准 Consumer 报告**，顺手把 payload 全丢了。
**音频在驱动层就被扔掉**，应用层再读 HID 也没用。

其它依据：

- 8 个 HID 集合的 usage page 只有 `0x0C` / `0x0D` / `0x20` / `0xFF00`，**没有音频类（0x0B）**，
  系统音频设备列表里也没有它。
- 即便拿到音频，也要写虚拟音频驱动（HAL plugin）才能让系统当输入源用。

### 绕过 HID 直连 GATT？试过了，被系统过滤掉

理论上可以不走 HID，用 CoreBluetooth 直接订阅那个 ATT 特征。分两步试的。

**第一步：命令行 Python —— 被 TCC 直接 abort**

```
EXC_CRASH (SIGABRT)
  __TCC_CRASHING_DUE_TO_PRIVACY_VIOLATION__
  __TCCAccessRequest_block_invoke
```

`CBCentralManager` 能创建、能扫，但一进 runloop 就被系统杀掉——访问蓝牙要求进程的 Info.plist 里有
`NSBluetoothAlwaysUsageDescription`，无 bundle 的命令行 Python 没有。

**第二步：写进 `SiriRemote.app` —— 能连上，但只看得见 2 个服务**

给 App 加了 `CoreBluetooth` + Info.plist 声明，重新打包后确实通了（`蓝牙状态 = 5`）。
但扫描扫不到遥控器：**BLE 外设一旦连上就停止广播**，而它一直连着 Mac。
改用 `retrieveConnectedPeripherals(withServices:)` 向系统要「已连接的外设」，这才连上。

```
系统已连接的外设 2 个：
   <序列号>  state=0  id=<UUID>
→ 已连接，枚举服务
→ 服务 2 个：
   180A   (Device Information)   5 个特征，全 read
   180F   (Battery Service)      2 个特征，2A19 / 2A1A，read+notify
```

**只有设备信息和电池**。没有 `1812`（HID）、没有 Apple 私有服务、**连一个 write 特征都没有**。

也就是说：macOS 对「已被系统 HOGP 占用的设备」，只向应用层暴露非 HID 的安全服务。
这是系统级过滤，不是权限问题——命令行被 TCC 挡，Packaged App 被服务可见性挡。

顺带一个有用的观察：这次 CoreBluetooth 连接**没有影响系统 HID**（8 个集合全程还在），
说明它是独立的一条逻辑连接，不会抢走按键/触摸。

**三层阻挡汇总**：

| 层 | 现象 | 原因 |
|---|---|---|
| HID | 报告 ID `0xFA`，只有 2 字节 | HOGP 驱动把 `0xFC` 重映射成标准 Consumer 报告，丢 payload |
| GATT | 只有 180A/180F | 系统对 HOGP 设备只暴露非 HID 服务 |
| HCI | 抓不到 | macOS 没有 `btmon` 等价物，PacketLogger 要单独装 Xcode 附加工具 |

**唯一真正可行的路**：在一台 Linux 机器上跑原版 `open-appleremote`（BlueZ 不限制 GATT 可见性，
`btmon` 能抓完整 101 字节），把 PCM 通过 Wi-Fi 转发给 Mac。代价是要一台树莓派/旧笔记本，
而且遥控器得从 Mac 重新配对过去（Mac 上就不能用了）。

**没做，判断是投入产出比不划算。**

所以正确做法是：**用 Mac 自己的麦克风，遥控器的麦克风键只当"触发键"**。

```bash
.venv/bin/python mac/siriremote.py keys                    # 默认 hold-option
.venv/bin/python mac/siriremote.py keys --siri-action cmd-d   # 如果目标 App 用 ⌘D
```

| 参数 | 行为 | 适配 |
|---|---|---|
| `hold-option`（默认） | 按住→Option 按下，松开→Option 抬起 | 「长按 ⌥ 说话」的输入框/听写 |
| `cmd-d` | 发一次 ⌘D | ⌘D 式语音开关 |
| `hold-control` / `hold-fn` | 同上，换修饰键 | 系统听写设为「按 Control/Fn 两次」时 |
| `double-option` 等 | 双击修饰键 | 老式听写快捷键 |
| `key:space` / `key:F5` | 单键 | 第三方输入法的自定义语音键 |
| `off` | 不映射 | |

**要点：动作必须和目标 App 的触发方式一致。** 比如输入框提示「语音输入 ⌘D 或长按 ⌥」，
那 `double-option` 不会有任何反应（双击不是它的触发器），要用 `hold-option` 或 `cmd-d`。

实测确认：合成修饰键能真正改变系统修饰状态（`CGEventGetFlags` 从 `0x0` → alt 置位 → 清除），
不是只发了个空事件。

### 重要：从菜单栏启动需要单独授权

**TCC 认的是「负责进程」**。从 Terminal 跑 `siriremote.py` 能用，是因为 Terminal 在辅助功能列表里；
从 `SiriRemote.app` 里点「启动」拉起的主程序，负责进程变成 App 本身，**没有授权就全被丢弃**
（日志里会出现「警告：没有辅助功能权限」）。

处理：

1. App 启动时会自己调 `AXIsProcessTrustedWithOptions` 主动申请，`SiriRemote.app` 会出现在
   系统设置 → 隐私与安全性 → 辅助功能 的列表里
2. 勾上它（没出现就点 `+` 从 Finder 选 `SiriRemote.app`）
3. 回面板点一次「停止 → 启动」

面板会实时显示状态：`trusted: false` 时顶部会出现橙色横幅「缺少辅助功能权限，按键不会生效」并带「去授权」链接。

### 为什么「重启完还是不能用」：TCC 有几秒延迟

现象：点面板的「启动」，主程序起来了，**按键（TV 切换、音量）正常，但滑动、click、回车全死**。

日志里只有一行线索：

```
警告：没有辅助功能权限，合成的按键会被系统丢掉。
```

原因：**App 重启后 TCC 要过几秒才真正生效**。而 server 和主程序都在这几秒内启动，
`AXIsProcessTrusted()` 读到的是旧状态 `false` —— 但那时 App 其实已经有权限了
（`/tmp/siriremote-app.log` 里写得清清楚楚 `辅助功能已授权: true`）。

同一棵进程树里会出现三种结果，就是这个时间差造成的：App 说 true、采集子进程说 true、
主程序说 false。**光看这个标志会误判**。

现在的处理：

| 机制 | 作用 |
|---|---|
| `events_really_work()` | 真判据：把光标推 1px 再读回，看系统是否接受事件（然后还原） |
| `wait_for_access(10s)` | 每 0.5 秒重试，直到事件真的生效，最多等 10 秒 |
| `/tmp/siriremote-main.perm` | 主程序把自己的真实权限写出来，面板读它 |
| `/api/diag` | 用和启动主程序**完全相同的参数**跑一次自检，输出 `trusted` + `moved_px` |

`/api/diag` 是这次排查的关键工具——它证明了「同一条链上权限是好的」，
从而把问题锁定在时间窗而不是配置：

```json
{"trusted": true, "mouse_moved": true, "moved_px": 40.0, "from": [1777, 459]}
```

另外顺手去掉了 `start_new_session=True`：`setsid` 会让子进程脱离会话，
把 TCC 的「负责进程」链切断（虽然这次不是主因，但没有理由留着）。

### 为什么采集要放子进程（崩溃根因）

面板曾经「点停止一直显示处理中」。崩溃报告指得很清楚：

```
EXC_BREAKPOINT (SIGTRAP)
  hid_enumerate
  IOHIDManagerSetDeviceMatchingMultiple
  __CFCheckCFInfoPACSignature
```

每次刷新状态都调 `hid.enumerate()`，它会建一个 IOHIDManager 并挂到**当前线程**的 run loop；
而触摸回调那边开着另一个 CFRunLoop，并发时就会在这个签名校验里崩。服务一崩，前端 fetch 永远等不到响应，
按钮就卡在「处理中」。

三层修复：

1. **隔离**：探测（HID 轮询 + 触摸）全挪到 `mac/panel/probe.py` 子进程，父进程只做 HTTP；
   子进程崩了看门狗 1 秒后重拉，连续崩 3 次就放弃 HID 轮询只留触摸。
2. **缓存**：`/api/status` 只读子进程推上来的状态，父进程里不再出现任何 `hid.*` 调用。
3. **看门狗**：App 每 5 秒探一次端口，服务没了就重新拉起 —— 不用重启菜单栏程序。
4. 前端所有请求都带超时（状态 2.5s / 启停 6s），失败会弹提示而不是一直转。

另外记一笔：`pkill -f "panel/server.py"` 这类命令会匹配到**执行它的 shell 自己**（命令行里就含这个字符串），
把命令打断（exit 143）。要按 pid 精确杀，或校验 cmd 是否以解释器路径开头。

### TV 键：App 轮换

默认按 TV 键在 **ChatGPT → WorkBuddy → Claude** 之间循环（只在你已打开的那几个里切，
不会把没启动的 App 拉起来）。

```bash
.venv/bin/python mac/siriremote.py keys --tv-apps "ChatGPT,WorkBuddy,Claude"
.venv/bin/python mac/siriremote.py keys --tv-apps "Safari,Notes,Terminal"
echo '{"tv": "home"}' > mykeys.json && .venv/bin/python mac/siriremote.py keys --map mykeys.json   # 还原成 Home 键
```

两个实现坑（都实测踩过）：

- **`NSRunningApplication.activateWithOptions:` 从 macOS 14 起被系统忽略**——调用不报错，但前台不变。
  改用 `open -a <名字>`（走 LaunchServices），不需要「自动化」授权。
- **`NSWorkspace.frontmostApplication()` 在本进程里会给出过期值**：`open -a` 明明切成功了，它还报旧 App。
  判断前台的正确姿势是 `lsappinfo front` → `lsappinfo info -only name <ASN>`。

## 语音输入：按住说话 → 直接出文字

遥控器自己的麦克风拿不到（见上一节的实测），所以**录的是 Mac 的麦克风，遥控器只当"对讲键"**。

按住麦克风键 → 说话 → 松开 → 本地转写 → 文字直接粘到光标处。
不走系统听写的浮窗，终端、Electron 输入框里也能用。

```bash
# 面板里把「麦克风键」设成「按住说话」，或者命令行：
.venv/bin/python mac/siriremote.py keys --siri-action voice --voice-model base
```

### 依赖

```bash
.venv/bin/python -m pip install sounddevice numpy faster-whisper pyobjc-framework-AVFoundation
```

首次运行会从 HuggingFace 下载模型（base 约 140MB），之后走本地缓存。

### 模型怎么选（M3 / CPU int8 实测，5.22s 中文音频）

| 模型 | 转写耗时 | 实时率 | 结果 |
|---|---|---|---|
| tiny | 0.90s | 5.8x | 把"充电器"听成"**冲**电器" |
| **base** | **1.60s** | **3.3x** | 完全正确 ← 默认 |
| small | 4.52s | 1.2x | 完全正确，但等太久 |

`base` 是甜点：说 3 秒等 1 秒。`--voice-model tiny` 更快但同音字容易错；`small` 准确但 5 秒音频要等 4.5 秒。

**别设 `cpu_threads`**：实测 8 线程反而比默认慢一倍（10.3s vs 4.4s），超线程竞争。

### 实现要点

- **模型常驻**：加载一次（约 1 秒），`preload()` 在启动时后台预热，第一次按就来得及。
- **转写在后台线程**：绝不阻塞遥控器事件循环，否则按住说话时滑动会卡。
- **粘贴走剪贴板 + Cmd+V**：`CGEventKeyboardSetUnicodeString` 在部分 Electron 输入框里不生效。
- **录音失败自动降级**：麦克风打不开时这一次退回「按住 Option」，不会让键变成死键。

### 锁屏时麦克风是静音的（排查时踩过）

诊断时一直录到 **-91 dB**（数字静音），换了三条 API 都一样：
ffmpeg（avfoundation）、PortAudio（sounddevice）、AVFoundation（AVAudioRecorder）。
而 `AVCaptureDevice.authorizationStatusForMediaType_` 返回 **3（已授权）**，麦克风音量 94，设备也在。

最后查出来是 **`CGSSessionScreenIsLocked = True`** —— 屏幕锁着。macOS 在锁屏状态会掐掉麦克风采集，
截图也会是全黑。**排查音频问题时先确认屏幕没锁**：

```bash
.venv/bin/python -c "
from Quartz import CGSessionCopyCurrentDictionary
print(CGSessionCopyCurrentDictionary().get('CGSSessionScreenIsLocked'))"
```

另外 ffmpeg 的设备索引要看清：`ffmpeg -f avfoundation -list_devices true -i ""` 里
`[0]` 可能是 **ZoomAudioDevice** 这种虚拟声卡（录出来永远静音），MacBook 麦克风往往是 `[1]`。

### 试过但放弃的路

- **mlx-whisper**（Apple Silicon Metal 加速）：`pip install mlx-whisper` 依赖解析卡了 17 分钟没动，放弃。
  （`mlx` 本身装上了，0.32.4）
- **whisper.cpp**（`brew install whisper-cpp`）：8 分钟没装完，放弃。理论上 Metal 更快，
  但 base 的 3.3x 已经够用，不值得再折腾。

## 控制面板（全部塞进菜单栏弹窗）

**所有设置都在菜单栏弹窗里，不需要打开浏览器。**

```bash
open SiriRemote.app             # 正常用法：点图标
.venv/bin/python mac/panel/server.py   # 只想开服务时（浏览器访问 http://127.0.0.1:8787 也是同一套界面）
```

弹窗分三个页签：

| 页签 | 内容 |
|---|---|
| **触摸** | 272×272 实时预览（橙点 + 渐隐轨迹），下方显示接触面积 / 集合数 / 主程序 PID |
| **模式** | 鼠标模式、指针速度、加速度、按下防飘、手掌误触过滤 |
| **键位** | 11 个键各一个按钮，点开是底部选择面板；麦克风键动作也在这一页 |

改动**即时保存**：开关一点就写 `mac/config.json`，主程序在运行时**自动重启一次**让新配置生效
（防抖 600 ms，连续点多个开关只会重启一次）。

键位选择用**底部弹出面板**而不是原生 `<select>`：WKWebView 里原生下拉在 popover 中焦点行为不稳定，
而且 13 个选项平铺成两列更好点。TV 键的面板底部多一条「App 列表」输入框，填完失焦即保存。

面板只监听 `127.0.0.1`，不对外暴露。

两个设计上的注意点：

- 面板**不打开按键集合**（`12/1` 会被主程序独占），只挂触摸回调做预览，因此可以和主程序同时跑。
  为此 `LiveMac` 加了 `enable_buttons` 开关。
- 触摸预览走 `MultitouchSupport`，多进程各自注册互不影响。

配置优先级：**命令行显式参数 > `config.json` > 内置默认值**。

> 旧的宽屏网页版还在（`mac/panel/index.html`），但界面里已经没有任何入口，
> 只能在浏览器手动访问 `/full`。它需要点「保存」才生效，跟弹窗的即时保存不是一套逻辑，仅作后备。

## 菜单栏程序（SiriRemote.app）

图标常驻屏幕右上角，点一下弹出面板。

```bash
./mac/panel/build-app.sh        # 编译打包（**只在改 App.swift 时需要**）
open SiriRemote.app             # 启动
```

- **左键点图标**：弹出面板，底部是**启动/停止**按钮
  - 按钮直接拉起/结束主程序（`siriremote.py keys`），不用再开终端；状态点会跟着变
  - 启动日志写到 `/tmp/siriremote-main.log`；退出程序时会一并结束主程序，不留孤儿进程
  - 状态点：绿=已接管（主程序在跑）、橙=待启动、灰=遥控器休眠
- **右键点图标**：打开面板 / 完整设置（浏览器）/ 退出
- **自动拉服务**：启动时若 8787 端口没人听，会自己起 `mac/panel/server.py`；退出时一并结束
- 开机自启：系统设置 → 通用 → 登录项，把 `SiriRemote.app` 加进去

**维护边界（重要）**：`SiriRemote.app` 是 ad-hoc 签名，重新编译会让 cdhash 变化，
macOS 认为这是「另一个程序」，辅助功能授权随之失效。所以：

| 改动内容 | 需要重打包？ | 会丢授权？ |
|---|---|---|
| 面板 HTML/CSS/JS | 否 | 否 |
| `server.py` / `probe.py` | 否（重启服务即可） | 否 |
| `siriremote.py` / `pointer.py` | 否 | 否 |
| `App.swift` | 是 | 是 |

排版上踩过的坑：弹窗高度固定 562，内容比它高就会被裁成滚动。键位页 11 行最容易超，
所以 `.krow` 的 `padding` 收到 2px、`.kval` 收到 `3px 9px`（行高 37 → 27px），
并把「轮换的 App」输入框移进选择面板，才把 11 行 + 麦克风键全部放进可视区。

诊断：程序会往 `/tmp/siriremote-app.log` 写启动信息和未捕获异常，排查「图标没出来」时先看它。

## 触摸面积（手掌误触过滤）

`MultitouchSupport` 的触点结构里有 `size` / `major_axis` / `minor_axis` / `density` / `angle`，
但初代遥控器这块 24×18 栅格**只有 `size` 有信息**，而且是 5 档量化值：

| 状态 | size |
|---|---|
| 无接触 | `0` |
| 指尖 | `0.0038` |
| 拇指盖上 | `0.038` → `0.080` → `0.160` |

`major_axis` 恒为 0（没填）；`density` 从 -0.44 单调漂到 -0.98，是个递减计数器不是密度；
`minor_axis` 只有 π/2 和 0.63π 两个值。所以面积只能用 `size` 做粗判，不能当真面积。

据此加了**手掌误触过滤**：`size > palm_size`（默认 `0.02`）就不移动、也不喂手势。
面板里可开关，阈值在 `config.json` 的 `palm_size`。

## 实测结论（macOS 26.5.2，初代 A1962）

遥控器插在 USB 上时的真实情况，和 Linux 完全不是一条路：

| 通道 | macOS 里的样子 | 数据 |
|---|---|---|
| 按键 | HID `usage 12/1`（Consumer Control） | report ID **0xFA** + 1 字节位掩码 |
| 触摸 | `AppleMultitouchDevice`，Transport = `Bluetooth Low Energy`，序列号就是遥控器 | 被系统多点触控驱动接管，hidraw 读不到 |
| 蓝牙 HID | `Services: 0x400000 < BLE >`，没有 `HID ACL` | **不提供输入报文** |
| 厂商通道 | `usage 65280/11` | 只有 feature report 0xBB（39 字节设备信息） |

按键位掩码与 Linux 版 `decode.BUTTONS` **逐位一致**（已用分阶段录制验证）：

| 位 | 按键 | 位 | 按键 |
|---|---|---|---|
| 0x01 | TV | 0x10 | Siri |
| 0x02 | 音量 + | 0x20 | Menu |
| 0x04 | 音量 − | 0x80 | Click |
| 0x08 | 播放/暂停 | | |

触摸走私有 `MultitouchSupport.framework`（`MTDeviceCreateList` / `MTRegisterContactFrameCallback`）。
框架把设备认成 `(0x6d) family, 24 cols X 18 rows` —— 正是初代那块触摸板。
回调给归一化坐标，乘 `SPAN=1650` 后喂给原版 `gestures.py`。

## 它到底改了什么

原版 `device.py` 读 `/sys/class/hidraw/hidraw*`，用 `ioctl(HIDIOCGFEATURE)` 发 `F0 AF` 解锁遥控器；
`keys.py` 往 `/dev/uinput` 写虚拟键盘。macOS 两样都没有，所以：

- **按键**：`hidapi` 读 `usage 12/1`，把 `fa <mask>` 转成 `fc 00 <mask>`，直接喂原版解码器（`adapt()`）
- **触摸**：`MultitouchSupport` 的 contact frame 回调 → 归一化坐标 → 原版手势引擎
- **写**：`Quartz.CGEventCreateKeyboardEvent` + `CGEventPost(kCGHIDEventTap)`
- **没改**：`decode.py`、`gestures.py`

`mac/live_mac.py` 是采集层（按键 + 触摸两路），`mac/siriremote.py` 负责组装和发键。

## 排错

| 现象 | 原因 |
|---|---|
| `scan` 里没有 pid 0x026D | 没配对，或遥控器睡着了（按任意键唤醒） |
| 按键有反应、滑动没反应 | `MultitouchSupport` 回调没拿到帧：给终端开「输入监控」权限 |
| 滑动方向反了 | 上下改 `mac/live_mac.py` 的 `FLIP_Y`、左右改 `FLIP_X`。**实测初代 `FLIP_Y=False`**（MT 的 normalized_y 向上递增） |
| 滑动只能走一格，或一格跳好几下 | 跑 `mac/calibrate.py`，按它给的 `STEP` 建议调 `gestures.py` 顶部常量 |
| **某个方向滑动"失灵"**（多是往右） | 指针内部坐标跑到屏幕外了，见下节 |
| 有事件但没按键 | 没有辅助功能权限 |

### 某个方向滑动失灵（指针坐标跑出屏幕）

**这是最容易被误判成硬件或手势问题的 bug。** 症状：往一个方向滑完全没反应，反方向正常。

根因：`CGEvent` 的鼠标事件是**绝对坐标**，`Pointer` 自己维护浮点位置累加位移。
一直往同一方向滑，内部坐标会一路累加（实测跑到 `x = -4222`），而系统把真实光标钉在屏幕边缘 `x = 0`。
两者分离后，往反方向滑要先"还债"——按 `speed=0.7` 要滑 6000 多个单位（三四个满行程）才动，
表现就是「往右彻底失灵」。

修法（`pointer.py`）：

- `_screen_bounds()` 取所有活动显示器的并集，`_clamp()` 每次移动后把坐标钳在范围内
- 启动时如果读到的位置已经在屏幕外（`_cursor()` 此时可能返回垃圾值，见过 `-6302`），
  直接居中，而不是钉到角落

验证：连续左滑 12 次后内部 `x=0`、光标也停在 0；**再往右滑一次立刻移动 210px**。

诊断命令（主程序要先停，否则两边抢光标）：

```bash
curl -s -X POST http://127.0.0.1:8787/api/stop
.venv/bin/python -c "
from Quartz import CGEventCreate, CGEventGetLocation
loc = CGEventGetLocation(CGEventCreate(None))
print('光标', loc.x, loc.y)"
```

x 是负数或大于屏宽，就是这个问题。

### 唤醒后只有按键能用（触摸失灵）

按键和触摸走的是**两条独立链路**：按键是 HID（`0xFA` 报文），触摸是 `MultitouchSupport` 的私有回调。
遥控器休眠时系统会把 MT 设备注销，唤醒后重建；而 HID 自己会重连。所以会出现「按键好了、触摸还是死的」。

这里有三个坑叠在一起：

1. **启动时遥控器在睡 → 永远不重挂**。原来只在 `_had_device` 为真（掉线又回来）时重挂，
   但程序启动时遥控器如果在睡，`_had_device` 一直是 `False`，之后就再也不会重挂了。
2. **唤醒时 MT 设备晚于 HID 出现**。HID 一恢复就触发重挂，而这时 `MTDeviceCreateList()`
   里还没有遥控器的触摸板 → 重挂了个空，之后没有重试机制。
3. **`MTDeviceCreateList` 每轮返回新的包装对象**（指针每次都变），所以「按指针判重」永远失败。
   重挂一次就重复注册一次，同一帧被投递多次，**鼠标速度翻倍**。
   （`MTDeviceGetDeviceID` 在这套系统上调用会 SIGBUS，不能用来判重。）

现在的处理：

| 机制 | 作用 |
|---|---|
| HID 连上时若 `_mt_found == 0` 就重挂 | 覆盖「程序先起、遥控器后醒」 |
| 收到按键时也检查 | 按键能收说明遥控器在线，触摸板这会儿也该在 |
| 重挂没找到设备 → 12 秒内每 0.3 秒重试 | 等 MT 设备慢慢枚举出来 |
| 帧号去重 `frame_no` | 重复投递只处理一次，`_dup` 记录丢了多少 |
| 两分钟没触摸帧但按键正常 → 主动重挂 | 自动愈合偶发失灵 |
| 注册回调上限 4 个 | 超了就只重新 `MTDeviceStart`，不再追加，避免无限累积 |

#### 这里踩过两个很隐蔽的坑

**坑一：用时间戳去重，把正常帧全杀了**

重挂会让同一帧被投递多次（鼠标速度翻倍），所以加了去重，键用的是 `int(ts * 100000)`。
但 MT 给的 `timestamp` 精度不够、连续帧算出来一样 → 第一帧之后全被丢。
现象：**滑动彻底失灵**，日志里 `累计丢弃重复帧 1560`。

改用 `frame_no`（每帧必增）。另外一个细节：`_last_touch_t`（判断「有没有触摸」）必须
在去重**之前**更新，否则被丢的帧不算数，健康检查会跟着误判、反复重挂。

**坑二：`sources` 表在重挂后会把所有帧挡掉**

回调里有这么一句过滤：

```python
if sources.get(dev, True):   # 查不到就当内置设备，忽略
    return 0
```

`sources` 的 key 是 `MTDeviceCreateList()` 返回的 `devp`，而回调参数 `dev` 是**底层设备指针**。
第一次注册时两者恰好同址；**重挂后 `MTDeviceCreateList` 返回新的包装对象（地址变了），
回调里的 `dev` 还是原来那个** → 查不到 → 默认 `True` → 整帧丢弃。

现象：遥控器睡一觉醒来，滑动就永远死了；重启程序又好了，再睡一次又坏。
日志特征：`累计丢弃重复帧 0`，但健康检查一直报「两分钟没有触摸帧」。

修法：`register_all()` 本来就只注册非内置设备（内置触控板直接跳过），
所以到达回调的帧必然来自遥控器，**这句过滤直接删掉**。

排查时看主程序日志（`/tmp/siriremote-main.log`）：

- `触控就绪（1 个遥控器触摸板）` —— 启动时就挂上了
- `触摸板还没出现，12 秒内持续重试` —— 唤醒时序，正在等
- `触控回调已重挂（1 个…，累计丢弃重复帧 N）` —— 重挂成功；N 就是重复投递的帧数

如果日志正常、触摸页也有反应，但指针不跟手，那就是 `pointer.py` 那边的问题（见上一节的坐标钳制）。

### 停止主程序会误杀无关进程

`server.py` 的 `main_pids()` 原来只按「命令行里含 `mac/siriremote.py`」匹配，
结果 `pgrep -f "mac/siriremote.py"` 这类命令会匹配到**执行它的 shell 自己**，把命令打断（exit 143）。
现在要求命令行以 `.venv/bin/python` 开头才认。

## 后续可做

- 开机自启：写个 LaunchAgent，`ProgramArguments` 指向 `.venv/bin/python mac/siriremote.py keys`
- 触摸板当鼠标：`move` 事件里改成 `CGEventCreateMouseEvent` + `CGWarpMouseCursorPosition`
- 更快的手感：绕开 10 ms 轮询，换成 IOHIDManager 的回调（`IOHIDDeviceRegisterInputReportCallback`），省掉一轮 Python 循环
