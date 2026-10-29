# Recheck the unchanged routed netlist with corrected DAC constraints, then
# regenerate artifacts. This performs no new placement and adds no pipeline.
set root_dir [file normalize [file join [file dirname [info script]] ..]]
set out_dir [file join $root_dir build]
set bit_dir [file join $root_dir bitstream]
open_checkpoint [file join $out_dir routed.dcp]
set_max_delay 20.0 -from [get_clocks sys_clk] -to [get_ports {da1_data[*] da2_data[*] da1_wrt da2_wrt da1_clk da2_clk}]
set_min_delay 0.0 -from [get_clocks sys_clk] -to [get_ports {da1_data[*] da2_data[*] da1_wrt da2_wrt da1_clk da2_clk}]
source [file join $root_dir scripts check_converter_timing.tcl]
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -max_paths 20 -file [file join $out_dir timing_summary.rpt]
report_timing -to [get_ports {da1_data[*] da2_data[*] da1_wrt da2_wrt da1_clk da2_clk}] -delay_type min_max -max_paths 64 -file [file join $out_dir dac_io_timing.rpt]
report_exceptions -file [file join $out_dir timing_exceptions.rpt]
set wns [get_property SLACK [lindex [get_timing_paths -delay_type max -max_paths 1] 0]]
set whs [get_property SLACK [lindex [get_timing_paths -delay_type min -max_paths 1] 0]]
if {$wns < 0 || $whs < 0} {error "Timing failed"}
puts "FINAL_WNS=$wns";puts "FINAL_WHS=$whs"
write_checkpoint -force [file join $out_dir routed.dcp]
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 16 [current_design]
set_property CONFIG_MODE SPIx4 [current_design]
write_bitstream -force [file join $bit_dir light_lock_top.bit]
write_cfgmem -force -format mcs -size 16 -interface SPIx4 -loadbit "up 0x0 [file join $bit_dir light_lock_top.bit]" -file [file join $bit_dir light_lock_top.mcs]
puts "BUILD_PASS"
