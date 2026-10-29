# ============================================================================
#  build.tcl —— 光强锁定主工程（adc_pi_loop）综合/实现/出 bit
#
#  用法（Windows，Vivado 2023.2）：
#    D:\xilinx\Vivado\2023.2\bin\vivado.bat -mode batch -notrace -source scripts\build.tcl
#  或直接双击同目录 build.cmd
#
#  产物：
#    build\  综合/实现报告与 checkpoint
#    bitstream\light_lock_top.bit
#
#  注意：本工程顶层是 light_lock_top；RTL 全部在 rtl\ 下（20 个文件）。
#        hw_params.vh 用 `include 引入，必须 -i 指定 rtl 目录。
# ============================================================================

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file normalize [file join $script_dir ..]]
set out_dir    [file join $root_dir build]
set bit_dir    [file join $root_dir bitstream]
exec D:/mamba/python.exe -I -B [file join $script_dir generate_profile.py]
file mkdir $out_dir
file mkdir $bit_dir

# ---- 源码（列全，避免漏文件）------------------------------------------------
set rtl_files {
    adc_dac_core.v
    adc_drv.v
    adc_stats.v
    add_sat.v
    ctrl_tick.v
    dac_drv.v
    dac_sig_gen.v
    dbg_marker.v
    light_lock_top.v
    log_arbiter.v
    monitor.v
    power_display.v
    power_display_piecewise.v
    performance_probe.v
    pi_ctrl.v
    recip_pow2.v
    reg_file.v
    step_gen.v
    sweep_gen.v
    uart_proto.v
    uart_rx.v
    uart_tx.v
}

foreach f $rtl_files {
    read_verilog [file join $root_dir rtl $f]
}
read_xdc [file join $root_dir constraints davinci_adc_pi_loop.xdc]

# ---- 综合 -------------------------------------------------------------------
synth_design -top light_lock_top -part xc7a35tfgg484-2 -include_dirs [file join $root_dir rtl]
write_checkpoint -force [file join $out_dir post_synth.dcp]
report_utilization -file [file join $out_dir utilization_post_synth.rpt]

# ---- 实现 -------------------------------------------------------------------
opt_design
place_design
phys_opt_design
route_design

# Close the existing PI timing path if placement changes reduce its margin.
set setup_paths [get_timing_paths -delay_type max -max_paths 1]
if {[llength $setup_paths] && [get_property SLACK [lindex $setup_paths 0]] < 0} {
    phys_opt_design -directive AggressiveExplore
    route_design -directive Explore
}

report_drc -file [file join $out_dir drc.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose \
    -max_paths 20 -file [file join $out_dir timing_summary.rpt]
report_utilization -file [file join $out_dir utilization_post_route.rpt]
report_utilization -hierarchical -file [file join $out_dir utilization_hierarchy.rpt]
report_timing -from [get_cells -hier -filter {NAME =~ *u_pi*out_code_reg*}] -to [get_cells -hier -filter {NAME =~ *u_core*u_dac*da1_data_reg*}] -max_paths 5 -file [file join $out_dir pi_to_dac_timing.rpt]
report_timing -to [get_cells -hier -filter {NAME =~ *u_pi*i_at_limit_reg*}] -max_paths 2 -file [file join $out_dir integral_status_timing.rpt]
write_checkpoint -force [file join $out_dir routed.dcp]

# ---- bitstream --------------------------------------------------------------
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 16 [current_design]
set_property CONFIG_MODE SPIx4 [current_design]
# Bitstream is written only after timing checks below.

# ---- 时序门槛：WNS 必须非负，否则构建失败 -----------------------------------
set timing_paths [get_timing_paths -delay_type max -max_paths 1]
if {[llength $timing_paths] > 0} {
    set worst_slack [get_property SLACK [lindex $timing_paths 0]]
    puts "FINAL_WNS=$worst_slack"
    if {$worst_slack < 0} { error "Timing failed: WNS=$worst_slack" }
}
if {[llength $timing_paths] == 0} { error "No timing paths found" }
set hold_paths [get_timing_paths -delay_type min -max_paths 1]
if {[llength $hold_paths] == 0} { error "No hold timing paths found" }
set hold_slack [get_property SLACK [lindex $hold_paths 0]]
puts "FINAL_WHS=$hold_slack"
if {$hold_slack < 0} { error "Hold timing failed" }
write_bitstream -force [file join $bit_dir light_lock_top.bit]
write_cfgmem -force -format mcs -size 16 -interface SPIx4 -loadbit "up 0x0 [file join $bit_dir light_lock_top.bit]" -file [file join $bit_dir light_lock_top.mcs]
puts "BUILD_PASS"
