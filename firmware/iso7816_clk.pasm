; ISO 7816-3 card clock for firmware/iso7816.pasm: written after the RTL
; freeze (tag rc-93c2137-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; One instruction: pin CLK toggles every tick, so f_CLK = f_clk / (2 *
; CLKDIV). It takes the second core: a protoemu core cannot make a
; continuous clock while it does anything else. With CLKDIV D on this core,
; iso7816.pasm's core runs at CLKDIV 6 * D (TB = 124 ticks per etu of 372
; CLK periods): one etu is exactly 372 periods of this clock. 50 MHz, D = 6:
; CLK 4.1667 MHz, 1 etu = 89.28 us (11200 bit/s). Start both cores with one
; CTRL write; the host stops this core (CTRL enable) to stop the clock, low.
;
; Timing annotations (python -m pe.timing firmware/iso7816_clk.pasm -D CLK=10):
; @assume clkdiv=6
; @deadline half from=clk to=clk min=1 max=1 unit=tick "every half period of CLK: one tick"
.program iso7816_clk
.wrap_target
    pin CLK, toggle                   ; @point clk
.wrap
