# Re-time a finished run's final netlist and extracted SPEF at another clock
# period, one PVT corner per call (syn/8x4-study/sta_period.sh runs all three).
# OpenSTA in the LibreLane image, read-only on the run. Environment: STDLIB,
# SRAMLIB (liberty file names), CLOCK_PERIOD and the other variables
# src/protoemu.sdc reads (sta_period.sh takes them from the run's resolved.json).
set run /work/run
set pdk /pdk/ihp-sg13cmos5l/libs.ref
read_liberty $pdk/sg13cmos5l_stdcell/lib/$::env(STDLIB)
read_liberty $pdk/sg13cmos5l_sram/lib/$::env(SRAMLIB)
read_verilog $run/final/nl/tt_um_josusanmartin_protoemu.nl.v
read_verilog /work/src/macros/RM_IHPSG13_1P_256x32_c2_bm_bist.v
link_design tt_um_josusanmartin_protoemu
read_spef $run/final/spef/nom/tt_um_josusanmartin_protoemu.nom.spef
read_sdc /work/src/protoemu.sdc
puts "period $::env(CLOCK_PERIOD) corner $::env(STDLIB)"
report_worst_slack -max -digits 3
report_tns -max -digits 3
report_worst_slack -min -digits 3
report_tns -min -digits 3
# setup violators, and the worst flop/SRAM endpoint (a latch D that borrows
# shows slack 0; signoff.py's 'worst flop' excludes those)
set nv 0
set worst_reg 1e9
foreach pin [sta::endpoints] {
  set s [sta::pin_slack $pin max]
  if {$s < 0} { incr nv }
  set inst [$pin instance]
  if {$inst ne "NULL"} {
    set cell [[$inst liberty_cell] name]
    if {![string match "*dlhq*" $cell] && ![string match "*dllr*" $cell] && $s < $worst_reg} { set worst_reg $s }
  }
}
puts "setup_violating_endpoints $nv"
puts "setup_worst_flop_or_sram [format %.3f [expr {$worst_reg * 1e9}]]"
report_checks -path_delay max -group_path_count 1 -format end
