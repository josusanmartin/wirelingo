# Exact (unclamped) setup slack of a finished run at CLOCK_PERIOD, one corner per
# call: syn/8x4-study/sta_period.tcl's reads (final netlist, nom SPEF, signoff
# SDC with the run's variables), then the worst endpoint per family. Read-only.
set run /work/run
set pdk /pdk/ihp-sg13cmos5l/libs.ref
read_liberty $pdk/sg13cmos5l_stdcell/lib/$::env(STDLIB)
read_liberty $pdk/sg13cmos5l_sram/lib/$::env(SRAMLIB)
read_verilog $run/final/nl/tt_um_josusanmartin_protoemu.nl.v
read_verilog /work/src/macros/RM_IHPSG13_1P_256x32_c2_bm_bist.v
link_design tt_um_josusanmartin_protoemu
read_spef $run/final/spef/nom/tt_um_josusanmartin_protoemu.nom.spef
read_sdc /work/src/protoemu.sdc
puts "period $::env(CLOCK_PERIOD) corner $::env(STDLIB) sram $::env(SRAMLIB)"
puts "== report_worst_slack -max (all endpoints, latches included)"
report_worst_slack -max -digits 4
puts "== the worst endpoint overall (report_checks, unconstrained by family)"
report_checks -path_delay max -group_path_count 1 -format end -digits 4
set latch_d [all_registers -level_sensitive -data_pins]
puts "LATCH_D_PINS [llength $latch_d]"
puts "== worst latch D (time borrowing)"
report_checks -path_delay max -to $latch_d -group_path_count 1 -format full -digits 4
set addr {}
set srams [get_cells -filter "ref_name == RM_IHPSG13_1P_256x32_c2_bm_bist" *]
puts "SRAMS [llength $srams]"
set sram_in {}
foreach c $srams { foreach p [get_pins -of_objects $c] { if {[get_property $p direction] == "input"} { lappend sram_in $p } } }
foreach p $sram_in { if {[string match "*/A_ADDR*" [get_full_name $p]]} { lappend addr $p } }
set regs [concat [all_registers -edge_triggered -data_pins] $sram_in [all_outputs]]
puts "== worst flop, SRAM-pin or output endpoint (latches excluded)"
report_checks -path_delay max -to $regs -group_path_count 3 -format end -digits 4
report_checks -path_delay max -to $regs -group_path_count 1 -format full_clock_expanded -digits 4
puts "ADDR_PINS [llength $addr]"
puts "== worst A_ADDR endpoint"
report_checks -path_delay max -to $addr -group_path_count 3 -format end -digits 4
report_checks -path_delay max -to $addr -group_path_count 1 -format full_clock_expanded -digits 4
set oe {}
foreach i $::env(OE_INSTS) { lappend oe [get_pins $i/D] }
puts "OE_D_PINS [llength $oe]"
puts "== worst pin_oe endpoint"
report_checks -path_delay max -to $oe -group_path_count 3 -format end -digits 4
report_checks -path_delay max -to $oe -group_path_count 1 -format full_clock_expanded -digits 4
