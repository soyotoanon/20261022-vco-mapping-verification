# User-authorized board: DaVinci xc7a35t, Digilent cable 210512180081.
# Backup first; no erase if backup fails or JEDEC identification mismatches.
set root [file normalize [file join [file dirname [info script]] ..]]
set mcs [file join $root bitstream light_lock_top.mcs]
set bit [file join $root bitstream light_lock_top.bit]
if {![file exists $mcs] || ![file exists $bit]} {error "Build artifacts missing"}
open_hw_manager
connect_hw_server -url localhost:3121
set targets [get_hw_targets]
set selected {}
foreach t $targets {if {[string match *210512180081 $t]} {lappend selected $t}}
if {[llength $selected]!=1} {error "Expected exactly the verified board cable"}
current_hw_target [lindex $selected 0]
open_hw_target
set devs [get_hw_devices xc7a35t*]
if {[llength $devs]!=1} {error "Expected one xc7a35t"}
set dev [lindex $devs 0]
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev
# Vivado 2023.2 part table maps old n25q128-3.3v alias to this name.
create_hw_cfgmem -hw_device $dev [lindex [get_cfgmem_parts mt25ql128-spi-x1_x2_x4] 0]
set cfg [get_property PROGRAM.HW_CFGMEM $dev]
set_property PROGRAM.FILES [list $mcs] $cfg
set_property PROGRAM.ADDRESS_RANGE use_file $cfg
set_property PROGRAM.ERASE 0 $cfg
set_property PROGRAM.BLANK_CHECK 0 $cfg
set_property PROGRAM.CFG_PROGRAM 0 $cfg
set_property PROGRAM.VERIFY 0 $cfg
create_hw_bitstream -hw_device $dev [get_property PROGRAM.HW_CFGMEM_BITFILE $dev]
program_hw_devices $dev
refresh_hw_device $dev
file mkdir [file join $root backup]
set backup [file join $root backup flash_before_[clock format [clock seconds] -format %Y%m%d_%H%M%S].bin]
readback_hw_cfgmem -all -format bin -file $backup $cfg
if {![file exists $backup] || [file size $backup] < 16777216} {error "Full Flash backup not verified"}
puts "FLASH_BACKUP=$backup"
set_property PROGRAM.ERASE 1 $cfg
set_property PROGRAM.BLANK_CHECK 1 $cfg
set_property PROGRAM.CFG_PROGRAM 1 $cfg
set_property PROGRAM.VERIFY 1 $cfg
program_hw_cfgmem $cfg
puts "FLASH_PROGRAM_VERIFY_PASS"
boot_hw_device $dev
after 3000
refresh_hw_device -update_hw_probes false $dev
set done [get_property REGISTER.IR.BIT5_DONE $dev]
puts "FLASH_BOOT_DONE=$done"
if {$done!=1} {error "Boot from configuration memory did not reach DONE"}
close_hw_target
disconnect_hw_server
close_hw_manager
