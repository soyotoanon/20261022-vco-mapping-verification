# F511 FPGA 固件源码

F511 取消了 `SETPOINT` 的逐码限速。闭环正常运行时，新目标在下一个有效反馈样本到来时一次更新。旧寄存器 0x2C 保留读写兼容性。F511 已通过 8 项仿真和 Vivado 时序检查。

本目录现存的 `bitstream/`、`build/` 和 `build_summary.json` 是 [F510 构建归档](https://github.com/soyotoanon/20261022-vco-mapping-verification/tree/c37d75282a9655cd13c57270eec6db4763797fa1/pid_dac_pm5v_20261105)。构建 F511 固件时使用本目录源码重新运行构建命令。

## 输出限制

根据历史板端标定 `V≈(D−8198)×0.0006533 V`，14 位 DAC 数字码限制为 **545～15851**，名义端点约 **−4.9997～+4.9997 V**。这是基于标定的数字限制，实际模拟输出电压、元件误差和上电瞬态仍需现场测量。

此限制作用于两路 DAC 的最终输出，覆盖 PI、固定码、扫描、测试波形和双通道直通；0x1E/0x1F 默认 545/15851，仍可在线设置更窄范围。保留原 PID 计算和默认 C07 增益、运行时目标值与显示；F511 的版本标识 0x34 为 `F511`。

## VCO 映射

DDS 第 8 路设为 100 MHz，调频斜率 0 MHz/V，0 V 处振幅 **0.178571 V**，调幅斜率 **0.035714 V/V**。按历史经验实际峰值约为设定值的 0.77，−5～+5 V 预计对应约 0～550 mVpp。若界面只能输入 4 位小数，使用 0.1786 和 0.0357；真实功放输入振幅及低端关光效果需要测量。

## 验证和当前状态

在工程目录执行 `python scripts/verify.py` 验证全部码值、两路 DAC 和不同模式；执行 `python scripts/run_build.py` 综合并检查 FPGA 内部与 ADC/DAC 接口时序。具体验证结果见 `build/verification/results.json`、`build_summary.json` 和 `build/converter_timing_summary.json`。

本版本尚未上板或实测模拟电压。沿用原版上电自动启动 PI 配置；调整 VCO 斜率后原 PI 增益需要重新验收。原控制台中的旧 VCO 估算参数尚未同步修改，不能把其效率估算当作新映射的实时结果。
