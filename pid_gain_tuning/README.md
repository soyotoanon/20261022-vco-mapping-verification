# PI 最终调参结果与代码归档

本目录归档 Kimi 于 **2026-10-23** 晚间完成的调参与后续噪声测量，整理上传日期为 **2026-10-25**。最终选择 **C21 / V3**。这里报告保存的实测记录；归档时没有重新连接硬件或改变参数。

| 项目 | 最终记录 |
|---|---:|
| Kp | 12 |
| Ki（每个有效采样） | 0.75 |
| 控制更新率 | 5 MHz |
| 目标 ADC / 基准 DAC | 9199 / 6860 |
| 3 dB 扰动抑制带宽 | 13.5117 kHz（离散点插值） |
| 10 kHz 闭环/开环幅度 | −5.3653 dB |
| 已测最大放大 | +5.6191 dB，约 48.15 kHz |
| 开环相对 RMS，0.5 Hz–100 kHz | 0.243748% |
| 闭环相对 RMS，同频段 | 0.123021% |
| 暗场等效相对 RMS，同频段 | 0.060501% |

RMS 振幅相对开环降低约 **49.53%**，没有扣除测量噪声底。扰动抑制比定义为 `20 log10(闭环基频幅度 / 开环基频幅度)`：负值表示抑制。扫频扰动为按直流标定换算的控制端标称 **0.0836224 Vpp**，未将其等同于交流端口电压的独立实测。

结果已达到当次 10 kHz 带宽目标，但高频仍有放大，保留为阶段性方案。全部为反馈 PD 的环内测量，尚无第二路独立 PD 验证。高频峰为已测点最大值；13.51 kHz 不直接等于开环单位增益交越频率。

## 最终结果和原始数据

- [最终结果 JSON](results/final_summary.json)
- [C21 参数档案](candidates/C21.json)
- [20 点完整扫频 JSON](measurements/20261023_181931_fullV/V3/sweep_full.json) / [CSV](results/selected_sweep.csv)；另有[7点先行筛选](measurements/20261023_181012_verifyV/V3/candidate.json)，10 kHz 的数值来自该次筛选。
- [最后一次 RIN 谱 CSV](measurements/20261023_192619/RIN.csv) / [RMS](measurements/20261023_192619/RMS.json) / [参数与采集方法](measurements/20261023_192619/session.json)
- [最终 RIN 与扫频图](results/final_results.png)
- [模型引导优化对比图](results/模型引导优化对比.png)
- [所有候选参数](candidates/候选参数记录.md) / [历史优化记录](results/优化记录.md)

C19/V1 和 C20/V2 也已保留：各自带宽约 11.42、15.38 kHz，已测最大放大约 +4.46、+6.24 dB。C1–C18 为早期候选，不替代最终 C21。

## 固件和测量程序

- `rtl/`、`constraints/`、`tb/`：A624 FPGA 源码、约束与仿真。
- `scripts/build.cmd`：Vivado 2023.2 构建；Vivado 安装路径可在脚本中调整。
- `bitstream/`：保存的 bit/MCS 构建产物。**A624 当时仅加载到 FPGA RAM；Flash 没有更新。MCS 文件存在不表示已经烧入 Flash。固件默认参数也不等于 C21，需在线设置候选。**
- `host/performance/`：完整测量 UI，包含噪声谱、RMS、扫频、扫幅和二维热图；扰动输入使用 Vpp。
- `switch_candidate.py`：A624 上在线设置 C21 或其他候选；使用前需释放 COM9。
- [增益格式](GAIN_FORMAT.md)：Kp=12 对应寄存器 `0x1A=0x8C00`；Ki=0.75 对应 `0x1B=0xE000`；5 MHz 对应 `0x11=4`。

```console
python -m pip install -r requirements.txt
python host/performance/app.py
# 关闭测量界面、确认 A624 固件和工作点正常后，可在线选择：
python switch_candidate.py C21
```

目标光强和 PI 参数均可在线设置，无需为改变目标值重新综合。命令行脚本不会将 C21 写入 Flash。具体接线和目标工作点以实验现场为准。

## 实验与模型代码

`experiments/` 收录 Kimi 的辨识、模型拟合、候选验证、完整扫描和绘图脚本；只将本机绝对工程路径改为相对目录。`experiments/original/` 保留逐字原始脚本作对照。测量脚本会连接实物；`sysid_sweep_b.py` 还会临时改变采样率，按脚本记录恢复 V3。离线分析可运行：

```console
python experiments/model_fit_opt.py measurements/20261023_175118_sysidA/sweepA
python experiments/plot_result.py
```

模型中的相位、约 5.3–5.6 μs 延迟和增益变化，是根据幅度数据并选择相位分支、采用单极点加延迟模型拟合的估计，**不是直接相位测量，也不足以单独确定延迟来自哪个器件**。这些估计与实测带宽/RMS 分开解释。19:15 的 12.5 MHz 扩展扫描没有完整结束，保留原始数据和恢复寄存器记录，不作为最终完整扫描。

## 验证与溯源

```console
python -m unittest discover -s host/performance -p "test_*.py"
python scripts/test.py
```

本次归档重新执行 Python 测量测试、由扫频原始结果重算带宽、由 RIN CSV 重算 RMS；之前的四项 XSim 仿真和构建证据保存在 `validation/`。本次未重新综合或烧录硬件。`validation/publication_manifest.json` 记录归档文件 SHA-256。历史 JSON 中绝对路径保留用于溯源；可直接使用上面的仓库相对链接。
