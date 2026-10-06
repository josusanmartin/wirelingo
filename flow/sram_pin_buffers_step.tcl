# Project.SramPinBuffersTcl runs after post-GRT timing repair; the composite
# step legalizes next, and the design repair that follows re-runs global routing.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
read_current_odb
source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl
set_propagated_clock [all_clocks]
source [file join [file dirname [info script]] sram_pin_buffers.tcl]
protoemu_sram_pins::run
write_views
