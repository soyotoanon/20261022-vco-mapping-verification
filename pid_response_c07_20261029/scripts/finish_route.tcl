set script_dir [file dirname [file normalize [info script]]]
set root_dir [file normalize [file join $script_dir ..]]
set out_dir [file join $root_dir build]
set bit_dir [file join $root_dir bitstream]
open_checkpoint [file join $out_dir routed.dcp]
phys_opt_design -directive AggressiveExplore
route_design -directive Explore
report_drc -file [file join $out_dir drc.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose \
    -max_paths 20 -file [file join $out_dir timing_summary.rpt]
report_utilization -file [file join $out_dir utilization_post_route.rpt]
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
