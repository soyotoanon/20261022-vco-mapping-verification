# Called with a routed design open and out_dir set. No instrument access.
proc io_arrival {port kind} {
    set p [lindex [get_timing_paths -delay_type $kind -to [get_ports $port] -max_paths 1] 0]
    if {$p eq ""} { error "Missing routed path: $port $kind" }
    set slack [get_property SLACK $p]
    if {![string is double -strict $slack] || $slack < 0} {
        error "Unconstrained or failing DAC path: $port $kind slack=$slack"
    }
    return [expr {[get_property DATAPATH_DELAY $p] + [get_property STARTPOINT_CLOCK_DELAY $p]}]
}
set evidence [open [file join $out_dir converter_timing_summary.json] w]
puts $evidence "\{"
puts $evidence {  "adc_external_round_trip_budget_ns": 4.0,}
puts $evidence {  "dac_external_skew_budget_ns": 2.0,}
puts $evidence {  "external_board_delays_measured": false,}
set worst 1e6
for {set channel 1} {$channel <= 2} {incr channel} {
    set dmin 1e6;set dmax -1e6
    for {set bit 0} {$bit < 14} {incr bit} {
        set port [format {da%d_data[%d]} $channel $bit]
        set dmin [expr {min($dmin,[io_arrival $port min])}]
        set dmax [expr {max($dmax,[io_arrival $port max])}]
    }
    set wmin [io_arrival da${channel}_wrt min];set wmax [io_arrival da${channel}_wrt max]
    set cmin [io_arrival da${channel}_clk min];set cmax [io_arrival da${channel}_clk max]
    # AD9767 Rev G: setup 2 ns, hold 1.5 ns, WRT->CLK >=2 ns,
    # strobe width >=3.5 ns. Sequencer data/WRT/CLK events: 0/20/40 ns;
    # earliest following data event 80 ns. Include clock-tree arrival delays.
    set setup [expr {20.0+$wmin-$dmax-2.0-2.0}]
    set hold [expr {60.0+$dmin-$wmax-2.0-1.5}]
    set latch [expr {20.0+$cmin-$wmax-2.0-2.0}]
    set pulse [expr {min(20.0+$wmin-$wmax,20.0+$cmin-$cmax)-2.0-3.5}]
    set worst [expr {min($worst,$setup,$hold,$latch,$pulse)}]
    puts $evidence "  \"dac${channel}\": \{\"setup_margin_ns\": $setup, \"hold_margin_ns\": $hold, \"wrt_clk_margin_ns\": $latch, \"pulse_margin_ns\": $pulse\},"
}
set adc_setup [get_property SLACK [lindex [get_timing_paths -from [get_ports {adc_d[*] adc_otr}] -delay_type max -max_paths 1] 0]]
set adc_hold [get_property SLACK [lindex [get_timing_paths -from [get_ports {adc_d[*] adc_otr}] -delay_type min -max_paths 1] 0]]
if {![string is double -strict $adc_setup] || ![string is double -strict $adc_hold]} {error "ADC timing is unconstrained"}
puts $evidence "  \"adc_setup_slack_ns\": $adc_setup, \"adc_hold_slack_ns\": $adc_hold,"
set passed [expr {$worst >= 0 && $adc_setup >= 0 && $adc_hold >= 0}]
puts $evidence "  \"passed\": [expr {$passed ? {true} : {false}}]"
puts $evidence "\}";close $evidence
if {!$passed} {error "Converter timing budget failed"}
puts "CONVERTER_TIMING_PASS ADC_setup=$adc_setup ADC_hold=$adc_hold DAC_min_margin=$worst"
