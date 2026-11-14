# PnR-only: src/protoemu_pnr_margin.sdc plus a design-wide transition limit of
# 1.0 ns (gen_config.py --pnr-max-transition), so place-and-route repair keeps
# every edge well inside the liberty's 2.507 ns. Window 5 measured each failing
# 3-core family's worst path set by one weak driver on a long net (0.9-2.5 ns
# in one stage, edges past 1.0 ns); 337 driver pins of rt7's final netlist were
# over 1.0 ns. Signoff keeps src/protoemu.sdc and the liberty limits.
# OpenSTA's set_max_transition takes the design or ports, not pins, so the
# SRAM address pins get no tighter limit here (gen_config --sram-addr-buf8).
#
# protoemu_pnr_margin.sdc finds protoemu_pnr.sdc next to $::env(PNR_SDC_FILE),
# which here is this file: point it at src/ while sourcing, then restore it.
set _protoemu_pnr_sdc $::env(PNR_SDC_FILE)
set ::env(PNR_SDC_FILE) [file join [file dirname [file dirname [file normalize $_protoemu_pnr_sdc]]] src protoemu_pnr_margin.sdc]
source $::env(PNR_SDC_FILE)
set ::env(PNR_SDC_FILE) $_protoemu_pnr_sdc
set_max_transition 1.0 [current_design]
