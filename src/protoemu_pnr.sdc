# PnR-only constraints: protoemu.sdc, with the FIFO storage latches timed like
# flops (their D -> Q arcs disabled). Data reaches a FIFO latch while it is open,
# so STA passes the borrowed time on through Q and the resizer kept chasing
# slack downstream of every latch (post-CTS and post-GRT repair took hours).
# Signoff (SIGNOFF_SDC_FILE = protoemu.sdc) times the real latch arcs.
source [file join [file dirname $::env(PNR_SDC_FILE)] protoemu.sdc]
set_disable_timing -from D -to Q [get_lib_cells */sg13cmos5l_dlhq_1]
