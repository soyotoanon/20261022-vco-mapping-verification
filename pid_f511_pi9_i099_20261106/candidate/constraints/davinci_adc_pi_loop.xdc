# ============================================================================
#  davinci_adc_pi_loop.xdc
#  光强锁定主工程（01–21 全部功能）的引脚与时序约束
#
#  板：正点原子达芬奇 ATK-DF7A35 V2.1（xc7a35tfgg484-2）
#  接线权威版：../../WIRING.md（J2/J3 逐脚按原理图第 8 页坐标提取）
#  ADC/DAC 脚位与已跑通的 passthrough_5mhz 完全一致，不重接线。
#
#  ★ 板上 J2/J3 的 54 脚是 +3.3V，禁止接任何信号（见 WIRING.md B7）
# ============================================================================

set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]

# ---------------------------------------------------------------------------
# 系统时钟与复位
# ---------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN R4 IOSTANDARD LVCMOS33} [get_ports sys_clk]
set_property -dict {PACKAGE_PIN U2 IOSTANDARD LVCMOS33} [get_ports sys_rst_n]
create_clock -period 20.000 -name sys_clk [get_ports sys_clk]

# ---------------------------------------------------------------------------
# AD9248 模块 V1.0：D0..D13 -> J2-1..14
#   ADC 数据随 adc_clk 回拍，按 50 MHz 采样；本镜像刻意在 adc_clk 跳变后
#   一个 sys_clk 才取样；接口时序见 converter_timing.xdc
# ---------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN R17 IOSTANDARD LVCMOS33} [get_ports {adc_d[0]}]
set_property -dict {PACKAGE_PIN J16 IOSTANDARD LVCMOS33} [get_ports {adc_d[1]}]
set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS33} [get_ports {adc_d[2]}]
set_property -dict {PACKAGE_PIN P19 IOSTANDARD LVCMOS33} [get_ports {adc_d[3]}]
set_property -dict {PACKAGE_PIN M17 IOSTANDARD LVCMOS33} [get_ports {adc_d[4]}]
set_property -dict {PACKAGE_PIN P20 IOSTANDARD LVCMOS33} [get_ports {adc_d[5]}]
set_property -dict {PACKAGE_PIN M16 IOSTANDARD LVCMOS33} [get_ports {adc_d[6]}]
set_property -dict {PACKAGE_PIN M15 IOSTANDARD LVCMOS33} [get_ports {adc_d[7]}]
set_property -dict {PACKAGE_PIN L15 IOSTANDARD LVCMOS33} [get_ports {adc_d[8]}]
set_property -dict {PACKAGE_PIN L14 IOSTANDARD LVCMOS33} [get_ports {adc_d[9]}]
set_property -dict {PACKAGE_PIN J17 IOSTANDARD LVCMOS33} [get_ports {adc_d[10]}]
set_property -dict {PACKAGE_PIN K17 IOSTANDARD LVCMOS33} [get_ports {adc_d[11]}]
set_property -dict {PACKAGE_PIN K18 IOSTANDARD LVCMOS33} [get_ports {adc_d[12]}]
set_property -dict {PACKAGE_PIN K19 IOSTANDARD LVCMOS33} [get_ports {adc_d[13]}]
set_property -dict {PACKAGE_PIN H19 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports adc_clk]
set_property -dict {PACKAGE_PIN J19 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports adc_oeb_n]
set_property -dict {PACKAGE_PIN F20 IOSTANDARD LVCMOS33} [get_ports adc_otr]
set_property -dict {PACKAGE_PIN F19 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports adc_pdn]

# ---------------------------------------------------------------------------
# AD9767 模块 V1.3 / ACM9767：CH1 -> J2-19..34
#   AD9767 是**电流输出 20mA**；±5V 双极性由模块上的 I-V 级运放完成，
#   不在 FPGA 内（依据厂家手册 AN9767REV1.0.pdf，DESIGN_DECISIONS.md 4.4）
# ---------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN K16 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[0]}]
set_property -dict {PACKAGE_PIN L16 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[1]}]
set_property -dict {PACKAGE_PIN L20 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[2]}]
set_property -dict {PACKAGE_PIN L19 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[3]}]
set_property -dict {PACKAGE_PIN T20 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[4]}]
set_property -dict {PACKAGE_PIN U20 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[5]}]
set_property -dict {PACKAGE_PIN N19 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[6]}]
set_property -dict {PACKAGE_PIN N18 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[7]}]
set_property -dict {PACKAGE_PIN M20 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[8]}]
set_property -dict {PACKAGE_PIN N20 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[9]}]
set_property -dict {PACKAGE_PIN J21 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[10]}]
set_property -dict {PACKAGE_PIN J20 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[11]}]
set_property -dict {PACKAGE_PIN G20 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[12]}]
set_property -dict {PACKAGE_PIN H20 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da1_data[13]}]
set_property -dict {PACKAGE_PIN L18 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports da1_clk]
set_property -dict {PACKAGE_PIN M18 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports da1_wrt]

# ---------------------------------------------------------------------------
# AD9767 第二路：CH2 -> J2-35..50（21 项里的双通道反馈控制用）
# ---------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN F21 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[0]}]
set_property -dict {PACKAGE_PIN F18 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[1]}]
set_property -dict {PACKAGE_PIN E17 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[2]}]
set_property -dict {PACKAGE_PIN D17 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[3]}]
set_property -dict {PACKAGE_PIN N22 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[4]}]
set_property -dict {PACKAGE_PIN M22 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[5]}]
set_property -dict {PACKAGE_PIN M21 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[6]}]
set_property -dict {PACKAGE_PIN L21 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[7]}]
set_property -dict {PACKAGE_PIN K22 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[8]}]
set_property -dict {PACKAGE_PIN K21 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[9]}]
set_property -dict {PACKAGE_PIN J22 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[10]}]
set_property -dict {PACKAGE_PIN H22 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[11]}]
set_property -dict {PACKAGE_PIN G22 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[12]}]
set_property -dict {PACKAGE_PIN G21 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports {da2_data[13]}]
set_property -dict {PACKAGE_PIN E19 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports da2_clk]
set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports da2_wrt]

# ---------------------------------------------------------------------------
# 板载 USB-UART（【05 参数设置】【16 PI 在线调整】【18 扰动数据回传】的链路）
#   取自达芬奇官方原理图导出约束 DaVinci_FPGA_IO.xdc：
#     uart_rxd = U5（FPGA 收，接桥的 TX）
#     uart_txd = T6（FPGA 发，接桥的 RX）
#   ★ 这根线就是 21 项里 05/16/18 依赖的那条"还没接过"的物理链路；
#     用板载 Type-C 串口时无需飞线，直接用这两脚即可。
# ---------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN U5 IOSTANDARD LVCMOS33} [get_ports uart_rx]
set_property -dict {PACKAGE_PIN T6 IOSTANDARD LVCMOS33 DRIVE 8 SLEW SLOW} [get_ports uart_tx]

# ---------------------------------------------------------------------------
# 板载 LED（心跳 + 状态）
#   DaVinci_FPGA_IO.xdc 给的是 led[0..3] = R2/R3/V2/Y2
# ---------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN R2 IOSTANDARD LVCMOS33} [get_ports led_heartbeat]
set_property -dict {PACKAGE_PIN R3 IOSTANDARD LVCMOS33} [get_ports {led_status[0]}]
set_property -dict {PACKAGE_PIN V2 IOSTANDARD LVCMOS33} [get_ports {led_status[1]}]

# ---------------------------------------------------------------------------
# 时序例外
# ---------------------------------------------------------------------------
# ADC data is synchronous to the generated multiplexed converter clock.
# Converter paths are constrained by converter_timing.xdc.

# 串口输入与 sys_clk 无相位关系（UART 起始位自带再同步）
set_false_path -from [get_ports uart_rx]

# 纯输出端口：无内部捕获路径
set_false_path -to [get_ports {adc_oeb_n adc_pdn}]
# Converter paths are constrained by converter_timing.xdc.
# Converter paths are constrained by converter_timing.xdc.
set_false_path -to [get_ports {uart_tx led_heartbeat led_status[*]}]

# Added 2026-10-20: onboard common-anode display, xx.xxxx mW
set_property -dict {PACKAGE_PIN J15 IOSTANDARD LVCMOS33} [get_ports {seg_sel_n[0]}]
set_property -dict {PACKAGE_PIN H17 IOSTANDARD LVCMOS33} [get_ports {seg_sel_n[1]}]
set_property -dict {PACKAGE_PIN H13 IOSTANDARD LVCMOS33} [get_ports {seg_sel_n[2]}]
set_property -dict {PACKAGE_PIN G17 IOSTANDARD LVCMOS33} [get_ports {seg_sel_n[3]}]
set_property -dict {PACKAGE_PIN H18 IOSTANDARD LVCMOS33} [get_ports {seg_sel_n[4]}]
set_property -dict {PACKAGE_PIN G18 IOSTANDARD LVCMOS33} [get_ports {seg_sel_n[5]}]
set_property -dict {PACKAGE_PIN H15 IOSTANDARD LVCMOS33} [get_ports {seg_n[0]}]
set_property -dict {PACKAGE_PIN G16 IOSTANDARD LVCMOS33} [get_ports {seg_n[1]}]
set_property -dict {PACKAGE_PIN L13 IOSTANDARD LVCMOS33} [get_ports {seg_n[2]}]
set_property -dict {PACKAGE_PIN G15 IOSTANDARD LVCMOS33} [get_ports {seg_n[3]}]
set_property -dict {PACKAGE_PIN K13 IOSTANDARD LVCMOS33} [get_ports {seg_n[4]}]
set_property -dict {PACKAGE_PIN G13 IOSTANDARD LVCMOS33} [get_ports {seg_n[5]}]
set_property -dict {PACKAGE_PIN H14 IOSTANDARD LVCMOS33} [get_ports {seg_n[6]}]
set_property -dict {PACKAGE_PIN J14 IOSTANDARD LVCMOS33} [get_ports {seg_n[7]}]
set_false_path -to [get_ports {seg_sel_n[*] seg_n[*]}]
