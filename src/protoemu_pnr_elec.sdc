# PnR-only: protoemu_pnr_margin.sdc, with the resizer's fanout limit at 6
# (signoff keeps the library's 8). Detailed routing's antenna repair adds
# diodes after every resizer step, and they count as loads: nets repaired to
# 8 ended with 9-12.
source [file join [file dirname $::env(PNR_SDC_FILE)] protoemu_pnr_margin.sdc]
set_max_fanout 6 [current_design]
