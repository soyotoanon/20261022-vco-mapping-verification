# GitHub 归档说明：F129

2026-10-29 已完成 Flash 写入、校验及启动读回；这是历史部署记录，不代表当前运行状态。

保留 C07：Kp=9、Ki=17695/32768 每采样、5 MHz。源码、参考模块、测试、约束、构建脚本、bit/MCS 和验证证据一并归档。`source-manifest.json` 对应已验证源文件的原始字节，`build_summary.json` 给出已构建 bit/MCS 的 SHA-256。

本次上传没有连接串口、重启控制台或烧录设备。上传归档不改变现场配置；F22A 的真实光学带宽尚未重新测量。

界面运行代码与依赖见 [PID Control Console](https://github.com/soyotoanon/20261026-PID-Control-Console)。本目录的 `scripts/publish_console.py`（如有）仅是原工作站的控制台安装脚本，不是 GitHub 上传脚本。

构建环境是 Windows、Vivado 2023.2、xc7a35tfgg484-2。脚本保留验证时使用的 `D:/Xilinx/Vivado/2023.2` 与 `D:/mamba/python.exe` 路径；其他电脑需按实际安装位置配置。`scripts/deploy.py` 依赖 requests、pySerial 及原工作站控制台。Git 不保存构建时间戳，重新检出后应先验证并重新构建，再在获得明确烧录授权后运行部署。当前没有 F22A 烧录授权。

本次只是归档已验证版本，未重新综合或修改 RTL。详见 [原工程说明](README.md) 和 [验证记录](validation.json)。
