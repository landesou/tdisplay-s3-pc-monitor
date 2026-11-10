# 一块吃灰的 1.9 寸屏，怎么变成电脑性能仪表盘

> 状态说明：这是边做边写的记录。写它的时候屏幕还没点亮（板子没插电脑），
> 所以「编译通过」「上位机 65 个单测通过」「状态机模型验证 0 error」都是真跑过的；
> 「点亮后画面如何」「实测帧率多少」这类没发生的事不会写，追溯不到的地方标 **未验证**。

## 一、起因：一块吃灰的屏

抽屉里躺着一块 LilyGO T-Display-S3：ESP32-S3、1.9 寸 IPS（170×320）、16MB Flash、8MB PSRAM，
丝印 `H569`。买的时候想的是「这么小一块屏，总会有地方用」，然后就一直吃灰。

真正让我动手的是个很日常的场景：跑编译、跑推理、玩游戏的时候，总想瞟一眼 CPU 占用、
GPU 温度、显存和功耗。切任务管理器太慢，开一个悬浮窗又太丑，而且悬浮窗还得占一块屏幕。
目标因此很具体：**桌角一块 320×170 的横屏仪表盘，一秒刷新一次，插上就能用。**
板子是横着立在桌面，所以 UI 按 320 宽、170 高来排，而不是把竖屏转 90 度糊上去。

现成的软件也不是没有，但都不合适：AIDA64 要付费；HWiNFO 免费版的共享内存有时长限制；
更关键的是，它们只能从自己的共享内存里取数，想同时走 USB 串口和 HTTP 两条链路就得自己写。
所以上位机自己来：一个 Python 守护进程 `pcmond`，一套 JSON，USB 和 HTTP 两种传输同时喂。

立项时定下几条规矩，后来一直没变：**先 USB 跑通再加 WiFi 双模**（USB 不用配网、不用连路由器，
是纯桌面场景的最优解）；横屏 320×170；框架用本机已装的 ESP-IDF 5.5.3；除了能跑，还要工程化
（自启、配置、README），并且开源。

## 二、先调研：微雪那个项目能抄吗？

按习惯，动手前先看别人怎么做的。参考项目是 `cc-usage-display`，我把它 clone 到本地读了源码：
`src/main.cpp` 782 行、`src/apps.cpp` 1268 行、`src/ui.cpp` 631 行，companion 的四个 Python 文件
分别是 110 / 102 / 321 / 193 行。读完之后结论就一句话：**架构能抄，驱动不能抄。**

能抄的是架构。它是个「PC 端 Python Companion 读本地数据源 → 局域网 HTTP + Bearer Token →
设备轮询渲染」的结构，设备侧 15 秒拉一次，60 秒无操作熄屏，还有一整套串口调试命令。
这个「采集与显示分离、走局域网、Token 鉴权」的骨架非常干净，直接沿用。

不能抄的是硬件层，而且不是小改，是整层重写。参考项目那块板子是微雪
**ESP32-C6-Touch-AMOLED-2.16**，和我手里的完全是两类东西：

| | 参考项目 | 本项目 |
|---|---|---|
| 屏幕 | 480×480 AMOLED | 1.9″ 170×320 ST7789 IPS |
| 总线 | QSPI | **8-bit Intel 8080 并口** |
| 触摸 | 有（CST9220） | 无 |
| 电源管理 | AXP2101 PMU | 无，只有一个 `LCD_POWER_ON` 使能脚 |
| 其它外设 | QMI8658 IMU、PCF85063A RTC | 无 |
| 框架 | Arduino / PlatformIO | ESP-IDF 5.5.3 |

连分辨率都反着来（480 方屏 vs 320×170 横条），UI 一行都复用不了。所以路线定成：
**架构思想沿用，驱动从零写；传输从「设备轮询 HTTP」改成「USB CDC 推送为主 + HTTP 轮询备用」**
——1Hz 的数据量很小，一根 USB 线供电加传数据，插上就能用，比配 WiFi 靠谱。

这一步的时间花得值得：先分清哪些能搬、哪些不能搬，后面就不会在别人的驱动代码里绕圈子。

## 三、四个关键技术点

### 3.1 这屏根本不是 SPI 的

拿到板子第一件反直觉的事：它的屏**不走 SPI**，走的是 8 位 Intel 8080 并口。数据线 D0–D7 是
GPIO39/40/41/42/45/46/47/48，WR（也就是 i80 的 PCLK）在 GPIO8，DC 在 GPIO7，CS 在 GPIO6，
RST 在 GPIO5，背光在 GPIO38，像素时钟 20MHz。另外还有一个必须单独拉高的 **`LCD_POWER_ON`
（GPIO15）**——它和背光是两回事，后面当坑单独讲。并口占了 13 个 GPIO，但一次传 8 位，
刷屏不吃力；板子的走线既然这么接，就顺着它来。

为什么选 ESP-IDF：它的 `esp_lcd` 自带 i80 总线，而且官方就有
`examples/peripherals/lcd/i80_controller/` 示例，里面正好是 ST7789 面板初始化、内置的
LVGL 9.2.2 移植代码、`esp_lcd_i80_alloc_draw_buffer` 双缓冲，连 flush 回调里的 RGB565
字节交换都写好了。这基本就是为本项目准备的地基，省掉从零啃 8080 时序的成本。

另一个决定：**v1 不启用 PSRAM**。8MB PSRAM 看着香，但 170×320 的双缓冲用内部 RAM
（2×34KB）就够，而 PSRAM 的 octal/quad 模式一旦配错，是「开机直接失败」级别的风险。
先把能跑的路径跑通，再谈优化。

### 3.2 LVGL 9：不走组件管理器，把源码 vendor 进来

UI 用 LVGL 9.2.2。这里有个取舍：可以引 `esp_lvgl_port` 这类现成封装，也可以用官方示例里
自带的移植层（约 40 行）。我选了后者——层数少、代码看得见，出问题知道去哪找。

依赖获取方式也被迫改了。ESP-IDF 的组件管理器在本机连不上 `components-file.espressif.com`，
`idf.py` 直接报 `Cannot establish a connection to the component registry`。但诡异的是，
**同一个 URL 用 python 直接抓是 200，用 IDF 自带 venv 的 python 抓也是 200**——所以不是
网络不通，是组件客户端的 HTTP 请求有问题。官方示例因此 configure 都过不了。

解法是放弃组件管理器：`tools/fetch_deps.cmd` 把 LVGL `v9.2.2` 浅克隆下来、裁剪后放进
`firmware/components/lvgl/`（这个目录 gitignore 掉，不进仓库）。代价是每个 clone 都要跑一次
依赖脚本、工程多十几 MB 源码；好处是**完全离线可复现**，也不受 registry 抖动影响。

裁剪时踩了个很典型的坑：LVGL 的 `env_support/cmake/esp.cmake` 会把 `examples/`、`demos/`
**无条件**加进 include 路径，你以为「用不到删掉省空间」，结果 CMake 直接报
`Include directory ... is not a directory`。只能把两个目录留着（可以是空的）。

### 3.3 USB CDC「一线通」：一根线搞定供电、数据、日志

ESP32-S3 有原生 USB-Serial/JTAG（D-/D+ = GPIO19/20），板上没有外置串口芯片。
这意味着插一根 USB-C 线，同时得到三样东西：供电、数据通道、以及看日志的串口。
对桌面摆件来说这是最舒服的形态——不用配网、不用开什么软件，插上就有数。

协议上做了个反直觉但很实用的选择：**不上二进制协议，就发一行 JSON**。每帧一行、`\n` 结尾、
UTF-8 无 BOM，契约上限 600 字节，实测一帧 409 字节（24 线程时 `cores` 数组占大头），
字段全部可选，缺了就显示 `--`：

```json
{"v":1,"seq":42,"t":1762761600,"host":"MY-PC",
 "cpu":{"avg":23.4,"mhz":2400,"temp":null,"cores":[12,34,5,0]},
 "mem":{"pct":61.2,"used":9340,"total":15288},
 "gpu":{"util":16,"temp":58,"vram_used":1864,"vram_total":8151,"power":29.2},
 "net":{"down":1.2,"up":0.3},"disk":{"read":0.4,"write":2.1},"src":"usb"}
```

（契约里的示例帧，不是实测日志。）为什么这么干：1Hz、600 字节对 USB CDC 来说带宽上毫无压力，
换来的是**可读、可 `grep`、两端共用一份契约**，调试时不用为了「这帧到底对不对」再写一个解析器。
设备侧只在 `src` 字段里自己填 `usb` 或 `wifi`，两条链路报文字节完全相同。

上位机 `pcmond` 只依赖 psutil + pyserial，结构上刻意把采集和传输解耦：采集线程固定周期产出一帧
塞进 `queue.Queue(maxsize=2)`，队列满丢最旧帧，**采集永不阻塞**；USB 和 HTTP 各自读最新快照，
HTTP 的 `/api/stats` 只回最近一帧、不为每个请求重新采集。串口拔掉指数退避重连；
`nvidia-smi` 不存在或超时（3 秒）就让 GPU 字段整体发 `null`；端口被占只关 HTTP，
采集和 USB 继续跑——一句话，**任何单点故障都不许拖垮整个进程**。

### 3.4 状态机：怎么保证「绝不显示过期数据」

这类仪表盘最容易翻车的地方不是画不出来，而是**数据早就断了，界面还在显示旧数字**。
串口线松了、PC 睡眠了、WiFi 掉线了，屏幕上那个「CPU 23%」可能已经是十分钟前的。
用户不会去核对，他会信。所以设备侧的「新鲜度 + 仲裁」逻辑我没有直接写 `if`，
先用 logicprobe 建模验证了一遍（模型在 `docs/design/link-state-machine.model.json`）。

状态是：`INIT` / `NO_LINK` / `ONLINE_USB` / `ONLINE_WIFI` / `STALE` / `SLEEP`（深睡终态），
阈值 3 秒：3 秒没有新帧就进 `STALE`，界面标灰、状态灯变色。三轮才验证通过，
过程本身比结论更值钱：

- **v0，6 error。** 第一个错误很典型：我写了条 `stale-marked` 不变式（要求 `STALE` 时
  `age==3`），而 schema 规定 init 状态**无条件**参与 `var-in-range` 检查，`min=3` 在初始状态
  就必挂。第二个是运行态是个**封闭 SCC 且没有终态**，liveness 直接不过。第三个是
  `maxTicks=2` 被判「驻留超期」。
- **v1，4 error。** 补了 `SLEEP` 终态解决 SCC、删了那条不变式、把 `maxTicks` 提到 3——
  A14 仍然报 `ticks=4`。
- **v2，pass_with_findings，0 error / 21 warning。** 这时候才想通：**A14 的驻留计数不会因为
  `usb_frame` 重置 `age` 而清零**，而「数据一直在流就一直在 ONLINE」本来就是正确行为，
  驻留时长天然无界。`maxTicks` 表达不了「断流 3 秒内转 STALE」这件事，删掉它，改用
  `age` 守卫 + `var-in-range` 不变式来覆盖。

最后跑过的检查包括可达性、无死锁、活跃性、确定性、守卫完备、三条不变式、变量域，
以及一堆对抗性检查。**21 条 warning 全是设计使然**：10 条是「该状态下不可能发生的事件被忽略」
（比如非 INIT 状态收到 `boot_done`），1 条是「帧与 tick 的到达顺序会影响结果」——这是物理事实，
不是 bug。结论是这台状态机可以照着写代码了：「绝不在旧数据上显示在线」「3 秒断流转 STALE」
「USB 优先」三条硬要求都有可执行模型背书。

顺便说个心得：**这种小状态机值得建模，恰恰因为它小**——人脑觉得「就几个 if」所以不验证，
而真正难想全的正是 init 状态、守卫边界、死锁这些角落。

## 四、踩坑集：现象 → 根因 → 解法

**坑 1：`LCD_POWER_ON` 不拉高，屏永远不亮。**
现象：背光 PWM 调得再对，屏幕也是黑的。根因：这块板的屏电源使能在 GPIO15
（`LCD_POWER_ON`，高有效），跟背光是两回事。解法：初始化流程里第一步就把它拉高。
说明：这条来自板级资料核对，板子还没插上，属于「预埋坑」，点亮阶段第一件事就是验它——**未验证**。

**坑 2：`idf.py` 报「python.exe doesn't exist」，但 python 明明存在。**
现象：在 bash（Git Bash/MSYS2）里 export 了 IDF 环境变量后，`idf.py` 报
`python.exe doesn't exist! Please run the install script`。根因：MSYS 会改写/弄丢环境变量里的
Windows 路径，`IDF_PYTHON_ENV_PATH=D:\...` 传过去已经变形；另外
`idf_tools.py export --format key-value` 输出的 `PATH` 是分号形式，直接 export 进 bash 会把
coreutils 全干掉（`head: command not found`）。解法：所有 IDF 构建改走 `cmd.exe` 批处理
（`tools/idf_build.cmd`），bash 只留一层包装（`tools/build.sh`）。还有个补丁：导出的 `PATH`
**不带 System32**，`xcopy`/`dir` 会找不到，必须在导出后 `set "PATH=%PATH%;%PATH_ORIG%"` 拼回来。

**坑 3：`.cmd` 里写中文，整段批处理崩掉。**
现象：`tools/idf_build.cmd` 一运行就报 `'f_build.cmd' 不是内部或外部命令`，还把注释里的
`tools\idf_build.cmd [build|flash|...]` 当命令执行（`|` 被当管道）。根因：cmd.exe 按 **cp936**
解析批处理文件，UTF-8 中文注释变成乱码字节，`rem` 行内容被当成命令。解法：**所有 `.cmd` /
`.bat` 一律纯 ASCII，注释也用英文**；bash 脚本不受影响，中文随便写。

**坑 4：Windows 上 `SO_REUSEADDR` 的语义和 Linux 相反。**
现象：开了两个 pcmond 实例，两个都「正常启动」，但 HTTP 请求时好时坏。根因：`socketserver`
的 `allow_reuse_address = True` 在 Linux 上是「TIME_WAIT 后立即重绑」，在 Windows 上却
**允许第二个进程再绑同一个正在监听的端口**——于是两个实例静默抢 8787，谁也不报错。
解法：Windows 上改用 `SO_EXCLUSIVEADDRUSE`，非 Windows 才用 reuse。现在第二个实例会报
`WinError 10048` 并且只关 HTTP 链路，采集和 USB 继续跑，进程不退出。

**坑 5：`psutil.cpu_percent(interval=None)` 第一次调用永远返回 0.0。**
现象：`--once` 打印出来的 CPU 占用率是 0。根因：没有前一次采样作基准，首次调用只能返回 0.0。
解法：启动时先预热调用一次，再睡 0.25 秒开始正式采样；单测把调用顺序锁住了，
防止以后有人「优化」掉预热。

**坑 6：`--once` 里网络和磁盘速率总是 0.00，以为采集写错了。**
现象：单帧输出里 `net` / `disk` 都是 0.00。根因：速率是「累计计数器差分」，单帧时有效窗口
只有预热那 0.2 秒，静默机器上确实接近 0；跑起来后按 1 秒窗口差分就是真实值。
解法：不用改代码，先确认这是单帧的正常现象，再拿长时间运行的结果判断。

**坑 7：多人并行构建，`BUILD_DIR` 写相对路径会把 build 目录建到奇怪的地方。**
现象：给 `idf_build.cmd` 加 `BUILD_DIR` 支持后第一次用相对路径，build 目录跑到仓库根，
kconfgen 直接失败。根因：`-B` 和 `SDKCONFIG` 都按**调用者的 cwd** 解析，不是相对工程目录。
解法：传绝对路径（`%PROJ%\%BUILD_DIR%`）。改完 `BUILD_DIR=build-lead bash tools/build.sh`
实测通过，产物在 `firmware/build-lead/`，flash 参数 `--flash_size 16MB --flash_freq 80m` 正确。

## 五、现状与后续

目前能确认的（都在仓库里可复跑）：ESP-IDF 5.5.3 工程编译通过，`tdisplay_pcmon.bin`
195,264 字节，目标 `xtensa-esp32s3`（不是默认的 esp32），16MB 分区布局下 3MB app 槽还剩 94%；
链路状态机模型验证 0 error / 21 warning；上位机 65 个单测 2.71 秒跑完，`--once` 输出 409 字节
合法帧，里面是 `nvidia-smi` 读出的真实数据（RTX 5060，19%，59 °C，2203/8151 MiB，29.7 W）；
HTTP 鉴权 200 / 401 / 404 都对，连发两次请求 `seq` 都是 7，证明 `/api/stats` 不会重复采集。

还不能确认的：**屏没点亮**——显示方向、offset、mirror、invert、RGB 顺序、刷新率、按键手感，
以及烧录流程、USB 真机丢帧率、WiFi 轮询稳定性，全部 **未验证**。点亮之后第一件事是烧测试图案，
四步试参数把偏移和镜像定下来，然后再谈「好不好看」。这也是我坚持在 README 里放
「已验证 / 未验证」两张清单的原因：开源的工程文档如果只写成功部分，后来的人会被坑一次。

后续计划：① 点亮并校准（offset / mirror / invert / RGB），换上实机照片；② 实机压测 USB CDC
（1Hz、约 400 B/帧的长时间稳定性、拔插重连）；③ WiFi 双模（v1 先 USB，HTTP 链路已经写好）；
④ 3D 打印一个立式外壳，电源走 USB-C。

仓库地址占位：`https://github.com/<your-id>/tdisplay-s3-pc-monitor`（发布时替换；本仓库采用
**MIT** 许可，版权署名 `T-Display-S3 PC Monitor contributors`）。相关文档：架构与接口契约在
`docs/design/architecture.md`，开发日志在 `docs/journal.md`，上位机用法在 `companion/README.md`。

## 六、写在最后

这个项目是边做边记录的：`journal.md` 里每个决策、每条报错原文、每个「当时为什么这么选」都留着，
这篇博客基本是从它摘出来的。好处是写文档的时候不用凭记忆——报错原文、版本号、测试结果
都能直接引用；坏处是日志越写越长，但比起「三个月后想不起来为什么不用另一个方案」，这点长度不算什么。

最后一句实话：这项目最花时间的不是写驱动，而是让 Windows + MSYS + cmd + ESP-IDF 这套组合拳
不互相打架。如果你也在 Windows 上折腾 ESP-IDF，希望上面几个坑能帮你省一个晚上。
