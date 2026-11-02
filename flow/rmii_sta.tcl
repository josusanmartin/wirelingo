# Pad-to-flop and flop-to-pad paths for docs/rmii-study.md, on the signed-off fo6 run.
# Numbers land in pe.rmii.STA_FO6. Runs on runner1, read-only on the run directory:
#   docker run --rm -v ~/protoemu-flow/protoemu-exp-fo6/runs/wokwi:/work/run:ro \
#     -v ~/protoemu-flow/protoemu-exp-fo6/src:/work/src:ro -v ~/ttpdk:/pdk:ro -v $PWD:/x:ro \
#     -e STDLIB=sg13cmos5l_stdcell_typ_1p20V_25C.lib \
#     -e SRAMLIB=RM_IHPSG13_1P_256x32_c2_bm_bist_typ_1p20V_25C.lib \
#     ghcr.io/librelane/librelane:3.1.0.dev3 sta -no_splash -exit /x/rmii_sta.tcl
# (slow: stdcell ..._slow_1p08V_125C, SRAM ..._slow_1p08V_125C; fast: stdcell
# ..._fast_1p32V_m40C, SRAM ..._fast_1p32V_m55C.) The _373xx_ names are fo6's first
# synchroniser flops of uio0..4, the endpoints of `report_checks -from uio_in[i] -path_delay min`.
set run /work/run
set pdk /pdk/ihp-sg13cmos5l/libs.ref
read_liberty $pdk/sg13cmos5l_stdcell/lib/$::env(STDLIB)
read_liberty $pdk/sg13cmos5l_sram/lib/$::env(SRAMLIB)
read_verilog $run/final/nl/tt_um_josusanmartin_protoemu.nl.v
read_verilog /work/src/macros/RM_IHPSG13_1P_256x32_c2_bm_bist.v
link_design tt_um_josusanmartin_protoemu
read_spef $run/final/spef/nom/tt_um_josusanmartin_protoemu.nom.spef
read_sdc $run/final/sdc/tt_um_josusanmartin_protoemu.sdc
foreach {p f} {uio_in[0] _37305_ uio_in[1] _37306_ uio_in[2] _37307_ uio_in[3] _37308_ uio_in[4] _37309_} {
  foreach m {max min} {
    puts "--- $p $f $m"
    report_checks -from [get_ports $p] -to [get_pins $f/D] -path_delay $m -format full_clock_expanded -digits 3
  }
}
foreach {i o} {8 0 9 1 10 2 11 3} {
  set n [get_nets "u_chip.g_core\[0\].g_on.u_core.pin_out_cur\[$i\]"]
  foreach m {max min} {
    puts "--- pin_out$i uo_out$o $m"
    report_checks -through $n -to [get_ports "uo_out\[$o\]"] -path_delay $m -format full_clock_expanded -digits 3
  }
}
