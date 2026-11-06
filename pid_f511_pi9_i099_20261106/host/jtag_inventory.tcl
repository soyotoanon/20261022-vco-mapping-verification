# Identify the hardware only. Do not program or reset the FPGA.
open_hw_manager
connect_hw_server -url localhost:3121
puts "JTAG_TARGETS=[get_hw_targets]"
set target {}
foreach t [get_hw_targets] {
    if {[string match *210512180081 $t]} {set target $t}
}
if {$target eq ""} {error "Expected JTAG adapter 210512180081 not found"}
current_hw_target $target
open_hw_target
foreach dev [get_hw_devices] {
    puts "JTAG_DEVICE=$dev PART=[get_property PART $dev]"
}
close_hw_target
disconnect_hw_server
close_hw_manager
puts "JTAG_INVENTORY_PASS"
