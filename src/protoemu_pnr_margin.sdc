# PnR-only experiment: protoemu_pnr.sdc with 0.75 ns more setup uncertainty on
# clk, so post-CTS and post-GRT timing repair aim past the slow corner's
# placement/global-routing estimate error (v2c: post-CTS repair reached 0 ns on
# placement parasitics, global routing then showed -4.7 ns). The FIFO latches
# borrow, so their slack stays 0 rather than becoming unfixable violations (as a
# resizer setup margin would make them). Signoff keeps protoemu.sdc.
source [file join [file dirname $::env(PNR_SDC_FILE)] protoemu_pnr.sdc]
set_clock_uncertainty -setup [expr {$::env(CLOCK_UNCERTAINTY_CONSTRAINT) + 0.75}] [get_clocks clk]
