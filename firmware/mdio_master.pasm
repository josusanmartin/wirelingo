; MDIO station management entity (STA): IEEE 802.3 Clause 22 management frames
; to Ethernet PHYs. Written with the RTL frozen, firmware only
; (docs/bench-firmware.md).
;
; Frame, 32 bits after the preamble (MSB first on the wire):
;   PRE    PRE ones (32 in Clause 22)
;   ST     01
;   OP     10 read, 01 write
;   PHYAD  5 bits, REGAD 5 bits
;   TA     write: we drive 1, 0. read: we release MDIO, the PHY drives 0 in
;          the second bit time
;   DATA   16 bits, MSB first (write: ours; read: the PHY's)
;   then one idle MDC clock with MDIO released (the pull-up holds it high).
;
; Pins: MDC on the side-set pin (any output, `MDC` define and SIDE_BASE), MDIO
; a uio pin NOT in open-drain mode with a pull-up on the board; OUT_BASE =
; IN_BASE = MDIO, OUT_COUNT 1. MDIO is driven push-pull (pin_oe) while the
; frame is ours, high impedance otherwise.
;
; Clock: T ticks per bit (even), high T/2, low T/2. T = 20 at CLKDIV 1 and
; 50 MHz is 2.5 MHz (400 ns: 802.3's minimum period, no margin); a larger T
; or CLKDIV is slower. MDC stops low between frames (802.3 sets no maximum).
; We change MDIO on the falling MDC edge, T/2 ticks before and after the
; rising edges the PHY samples on. We sample on our own rising edge: `in pins,
; 1 side 1` reads the pad 2 clocks (the synchroniser) before MDC rises at the
; pad, so a PHY may change MDIO any time after the rising edge up to
; 2 clocks before the next one.
;
; Host protocol. TX FIFO, one word per frame: the 32 bits after the preamble
; (pe.mdio.frame_word), bit 31 first; bit 29 (OP's high bit) picks read. On a
; read the low 18 bits are not used. RX FIFO, one word per frame:
;   read:  1 << 17 | TA2 << 16 | data    TA2, the second turnaround bit, is 0
;          when a PHY answered, 1 when nobody drove MDIO (the data is then
;          the pull-up's 0xFFFF)
;   write: 1 << 18
; The host may be slow: nothing is timed outside a frame, a full RX FIFO
; holds MDC low between frames.
;
; r0 = bit counter / scratch, r1 = read flag.
;
; Timing annotations (python -m pe.timing firmware/mdio_master.pasm -D MDIO=0 -D MDC=8 -D T=20 -D PRE=32):
; @assume clkdiv=1                                 ; 50 MHz: 1 tick = 20 ns
; @deadline mdc_hi from=r.enter to=f.enter min=10 max=10 unit=tick "MDC high 10 ticks: 200 ns (802.3 >= 160 ns)"
; @deadline mdc_lo from=f.enter to=r.enter avoid=top min=10 max=10 unit=tick reach=conditional "MDC low inside a frame 10 ticks: 200 ns (>= 160 ns)"
; @deadline mdc_per from=r.enter to=r.enter avoid=top min=20 max=20 unit=tick reach=conditional "MDC period inside a frame 20 ticks: 400 ns (>= 400 ns)"
; @deadline su from=d to=r.enter avoid=top min=10 max=10 unit=tick "MDIO set up 10 ticks before MDC rises (>= 10 ns)"
; @deadline hd from=h.enter to=d avoid=top,rel min=10 max=10 unit=tick reach=conditional "MDIO held 10 ticks after MDC rises (>= 10 ns)"
; @deadline turn from=rel to=ta2 min=30 max=30 unit=tick "MDIO released 30 ticks (600 ns) before TA2 is sampled"
; @deadline ta1 from=rel to=ta1r.enter min=10 max=10 unit=tick "MDIO released 10 ticks (200 ns) before the rising edge of TA1"
.program mdio_master
.side_set 1
.shift_out left
.shift_in left
.out_pins MDIO 1
.in_pins MDIO
.side_pins MDC
.define H T/2
    pin MDC, lo             side 0        ; an output, low; MDIO stays released
top:
    pull block              side 0
    mov r1, ::osr           side 0        ; bit 29 lands on bit 2
    and r1, 4               side 0        ; r1 != 0: a read
    ldi r0, PRE-2           side 0
    pin MDIO, hi            side 0 [H-1]  ; @point d
pre:
    nop                     side 1 [H-1]  ; @point r
    jmp r0--, pre           side 0 [H-1]  ; @point f
    ldi r0, 12              side 1 [H-1]  ; @point r @point h
hdr:                                      ; ST, OP, PHYAD, REGAD but the last bit
    out pins, 1             side 0 [H-1]  ; @point d @point f
    jmp r0--, hdr           side 1 [H-1]  ; @point r @point h
    out pins, 1             side 0 [H-1]  ; A0   @point d @point f
    ldi r0, 17              side 1 [H-2]  ; @point r @point h
    jmp r1, read            side 1
wbit:                                     ; TA (1, 0), then the data
    out pins, 1             side 0 [H-1]  ; @point d @point f
    jmp r0--, wbit          side 1 [H-1]  ; @point r @point h
    pin MDIO, hiz           side 0 [H-1]  ; @point f
    nop                     side 1 [H-1]  ; one idle clock   @point r
    ldi r0, 4               side 0        ; the record, 1 << 18   @point f
    mov isr, r0             side 0
    in null, 16             side 0
    jmp finish              side 0
read:
    pin MDIO, hiz           side 0 [H-1]  ; turnaround: ours ends here   @point f @point rel
    ldi r0, 1               side 1 [H-1]  ; TA1: nobody drives   @point r @point ta1r
    mov isr, r0             side 0 [H-2]  ; the record's marker, shifted to bit 17   @point f
    ldi r0, 16              side 0
rbit:                                     ; TA2, then the data: sampled as MDC rises
    in pins, 1              side 1 [H-1]  ; @point r @point ta2
    jmp r0--, rbit          side 0 [H-1]  ; @point f
    nop                     side 1 [H-1]  ; one idle clock   @point r
    nop                     side 0        ; @point f
finish:
    push block              side 0
    jmp top                 side 0
