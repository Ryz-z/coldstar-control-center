<div align="center">

<img src="extras/colorful-control-center.svg" width="110" alt="logo">

# colorful-control-center

**给七彩虹隐星P15（蓝天/Clevo 模具）笔记本的 Ubuntu 控制中心**

风扇转速 · 温度曲线 · RGB 键盘灯效 · 系统托盘常驻

</div>

---

一个开源实现的 Linux 控制中心，功能对标 Windows 下七彩虹官方 “Control Center”：
通过笔记本固件的 **ACPI _DSM（`CLV0001`）** / **WMI（`ABBC0F6D`）** 官方接口直接与 EC 通信——
和官方控制软件走的是同一条通道，而不是暴力读写 EC 寄存器。

> **实测机型**：Colorful 隐星P15 `P15 I5X1` · BIOS `1.07.03COLO` · Ubuntu 26.04 · kernel 7.0 · 三区 RGB 键盘

## ✨ 功能

### 🌀 风扇控制
- 六种工作模式：**自动(EC) / 静音 / 均衡 / 性能 / 全速 / 自定义曲线**
- **自定义温度-风扇曲线**：可视化拖拽编辑，实时生效
- 双风扇手动定速（联动 / 独立），可分别调好再一次性应用
- 预设模式带**智能缓冲**：短暂温度尖峰平滑过渡，风扇不再一惊一乍
- 安全设计：EC 有效下限 25%；≥90°C 自动接管满转；一键“交还 EC 自动控制”

### ⌨️ 键盘灯效
- 三区 RGB 独立配色（可一键同步）+ 亮度无级调节
- 8 种**硬件级灯效**：呼吸 / 循环 / 舞动 / 闪烁 / 随机 / 节拍 / 波浪
- 总开关支持彻底关灯（关灯后调整亮度/颜色不再误点亮）
- 白色背光键盘机型自动适配（亮度档位模式）

### 🖥 系统集成
- **系统托盘常驻**：关闭窗口不退出；托盘右键切换工作模式（性能/平衡/安静/自动）或退出
- **开机自启动**开关（设置页一键开启，登录即驻留托盘）
- ODM 性能预设（省电 / 安静 / 性能 / 影音）
- 实时监控：CPU / GPU / 硬盘温度、风扇转速、电池、内存
- 诊断页：原始固件命令控制台 + 一键导出环境报告

## 📥 安装（Ubuntu / Debian）

**1. 安装**（任选其一）：

```bash
sudo apt install ./colorful-control-center_1.0.5_amd64.deb
```

或双击 deb 文件点「安装」（安装包在本仓库 `dist/` 目录，也可从 Releases 获取）。

> 安装脚本会自动：编译内核模块 → 使用本机密钥签名 → 登记安全启动 → 启动后台服务。

**2. 如果开启了 Secure Boot**（一次性登记，重启生效）：

重启时出现蓝色 MOK 界面 → `Enroll MOK` → `Continue` → 输入密码 **`colorful`** → `Reboot`。
（或进入 BIOS 直接关闭 Secure Boot，二选一即可）

**3. 打开**：应用菜单搜索「控制中心」，或终端执行：

```bash
colorful-control-center
```

## 🖥 兼容性

本项目面向**使用 Clevo “Control Center” 固件接口**的机型。在目标机器上执行以下命令快速判断：

```bash
ls /sys/bus/acpi/devices/ | grep CLV0001        # ACPI DSM 接口
ls /sys/bus/wmi/devices/ | grep -i ABBC0F6D     # WMI 接口
```

两者命中其一、且为近年蓝天（Clevo）模具的机型，大概率可直接使用。
实测机型：**七彩虹隐星P15 `P15 I5X1`**。其他机型欢迎提交兼容性报告（Issue）。

## 🏗 项目架构

```
┌─ ui/          Web 界面（原生 HTML/CSS/JS，零外部依赖）
├─ daemon/      Python 守护进程（systemd 常驻，HTTP API 127.0.0.1:8632）
├─ kmod/        内核桥接模块 clevo_cc.ko（暴露 /dev/clevo_cc 设备节点）
├─ launcher/    桌面外壳（WebKit 窗口 + 系统托盘）与命令行工具
├─ extras/      systemd / polkit / 桌面图标 / 睡眠唤醒钩子 / 内核升级钩子
├─ debian/      Debian 打包元数据
└─ build-deb.sh 一键打包脚本
```

数据流：

```
Web UI  ⇄  守护进程(cccd)  ⇄  /dev/clevo_cc  ⇄  ACPI _DSM / WMI  ⇄  EC 固件
```

内核模块只做一件事：把固件的“命令 + 参数 → 返回值”通道安全地暴露给用户态；
所有业务逻辑都在 Python 守护进程中，方便审计与二次开发。

## 🔨 从源码构建

```bash
sudo apt install build-essential linux-headers-$(uname -r) dpkg-deb
bash build-deb.sh
sudo apt install ./out/colorful-control-center_1.0.5_amd64.deb
```

## 📚 文档

- [Clevo 固件协议速查](docs/clevo-protocol.md) —— 协议研究整理（欢迎其他白牌机玩家参考/补充）

## ❓ 常见问题

| 问题 | 解法 |
|---|---|
| 装完风扇/键盘不可控 | 99% 是 Secure Boot 未登记 → 按上文第 2 步重启登记；仍不行请查看控制中心「诊断」页 |
| 想恢复出厂风扇策略 | 界面点「交还 EC 自动控制」，或终端执行 `colorful-cc-cli auto` |
| 查看运行状态 | `colorful-cc-cli status`；模块日志：`/var/log/colorful-control-center-kmod.log` |
| 桌面窗口日志 | `~/.cache/colorful-control-center/gui.log` |
| 内核升级后模块失效？ | 已内置自动重建钩子（`/etc/kernel/postinst.d/`），通常无需手动处理 |
| 卸载 | `sudo apt remove colorful-control-center`（加 `--purge` 连配置一起清） |

## 🙏 致谢

- [tuxedo-drivers](https://github.com/tuxedocomputers/tuxedo-drivers)（TUXEDO Computers）——
  `clevo_cc` 模块的工作方式与协议知识参考自此项目（GPL-2.0+）
- 社区先驱项目：[clevo-indicator](https://github.com/SkyLandTW/clevo-indicator)、[clevo-fan](https://github.com/joshuabach/clevo-fan) 等

## 📄 许可证

[GPL-2.0-or-later](LICENSE)

---

<details>
<summary><b>English Summary (click to expand)</b></summary>

**colorful-control-center** — an open-source Linux control center for Colorful XING P15 (Clevo barebone) laptops running Ubuntu/Debian.

- **Fan control**: auto/quiet/balanced/performance/full-speed modes, custom temperature–fan curve, manual fixed speed — with anti-jitter buffering so brief temperature spikes don't make the fans audibly rev up and down.
- **RGB keyboard**: 3-zone colors, 8 hardware effects, brightness, a master switch that truly turns the backlight off (white-only keyboards supported).
- **System**: system-tray residency (close-to-tray), autostart toggle, ODM performance profiles, live sensors, raw-command diagnostics console.
- **How it works**: talks to the OEM Clevo “Control Center” firmware interface (ACPI _DSM on `CLV0001` / WMI `ABBC0F6D`) through a small GPL kernel bridge (`clevo_cc.ko` exposing `/dev/clevo_cc`), a Python daemon, and a zero-dependency web UI.
- **Build**: `bash build-deb.sh` → install the generated `.deb`. On Secure Boot systems, enroll the local MOK key (password `colorful`) on reboot, or disable Secure Boot.

License: GPL-2.0-or-later. Protocol knowledge credited to the tuxedo-drivers project.

</details>
