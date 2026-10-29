set root [file normalize [file join [file dirname [info script]] ..]]
open_hw_manager
connect_hw_server -url localhost:3121
set target {}
foreach t [get_hw_targets] {if {[string match *210512180081 $t]} {set target $t}}
if {$target eq ""} {error "Verified board not connected"}
current_hw_target $target
open_hw_target
set dev [lindex [get_hw_devices xc7a35t*] 0]
if {$dev eq ""} {error "Expected xc7a35t"}
current_hw_device $dev
set_property PROGRAM.FILE [file join $root bitstream light_lock_top.bit] $dev
program_hw_devices $dev
refresh_hw_device -update_hw_probes false $dev
puts "RAM_DONE=[get_property REGISTER.IR.BIT5_DONE $dev]"
close_hw_target
disconnect_hw_server
close_hw_manager
