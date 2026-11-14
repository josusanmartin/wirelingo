# Worst setup slack of a finished run with the FIFO latches excluded, one corner
# per call (flow/sta_nonlatch.py runs it in the run's LibreLane image, read-only).
# The run's own STA reports report_worst_slack over every endpoint: a latch D pin
# that borrows time has slack exactly 0, so that worst slack reads 0 whenever
# every other endpoint is positive. This reports both, and the worst endpoint
# among flop data pins, macro input pins and outputs.
# Environment: STA_LIBS (Liberty files), STA_NETLIST, STA_SPEF, STA_SDC, STA_TOP,
# STA_MACROS (macro cell names), CLOCK_PERIOD and the variables the SDC reads.
foreach lib $::env(STA_LIBS) { read_liberty $lib }
read_verilog $::env(STA_NETLIST)
link_design $::env(STA_TOP)
read_spef $::env(STA_SPEF)
read_sdc $::env(STA_SDC)
puts "== ALL"
report_worst_slack -max -digits 4
set latch_d [all_registers -level_sensitive -data_pins]
puts "== LATCH [llength $latch_d]"
if {[llength $latch_d]} {
  report_checks -path_delay max -to $latch_d -group_path_count 1 -format full -digits 4
}
set macro_in {}
foreach ref $::env(STA_MACROS) {
  foreach c [get_cells -filter "ref_name == $ref" *] {
    foreach p [get_pins -of_objects $c] {
      if {[get_property $p direction] == "input"} { lappend macro_in $p }
    }
  }
}
set ends [concat [all_registers -edge_triggered -data_pins] $macro_in [all_outputs]]
puts "== NONLATCH [llength $ends]"
report_checks -path_delay max -to $ends -group_path_count 1 -format end -digits 4
puts "== END"
