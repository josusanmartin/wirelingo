; A stand-in for an Ethernet PHY's IEEE 802.3 Clause 22 management interface:
; 32 16-bit registers behind one PHY address, answering an STA over MDC/MDIO.
; Not a PHY: there is no line side, and only what is listed here is modelled
; (docs/bench-firmware.md). Written with the RTL frozen, firmware only.
;
; Why the registers are in the core: MDIO has no clock stretching, so a read
; must be on the wire two MDC periods after REGAD; a host round trip over SPI
; cannot do that. The 32 registers are a jump table in instruction memory:
;   register 0 (control)      R2: read/write; bits 15 (reset) and 9 (restart
;                             auto-negotiation) read back 0; nothing else
;                             happens on them
;   register 1 (status)       R3: read-only to the STA; the host may write it
;                             any time (link up/down)
;   register 4 (advertising)  the CRC register (width 16, no taps): read/write
;   the other 29              constants, defines V2, V3, V5..V31
; Writes to the other registers are ignored. The host sets R2, R3 and CRC
; before starting the core (pe.mdio.phy_setup) and can read them.
;
; Pins: MDIO a uio pin, not open drain, with a pull-up on the board; MDC any
; input (define). OUT_BASE = IN_BASE = MDIO, OUT_COUNT 1. CLKDIV 1. BASE is
; the program's load address (256 on core 1): the table jump is absolute.
;
; Sampling: both pins are read after the WAIT on MDC's rising edge completes,
; which is the second synchronised sample with MDC high, 1 to 2 clocks after
; the edge at the pad: an STA must hold MDIO for more than 2 clocks (40 ns at
; 50 MHz) after MDC rises, against 802.3's 10 ns minimum: exactly 2 clocks works
; only with MDC on the chip's clock grid. No setup time is needed.
; Driving: MDIO changes 4 to 5 clocks after MDC rises at the pad (80..100 ns at
; 50 MHz; 802.3: 0..300 ns).
;
; Frame: 32 ones on MDIO with 32 MDC clocks, then ST 01, OP 10 or 01, PHYAD
; (ours or the frame is ignored), REGAD. Read: MDIO stays released through
; TA1, we drive 0 after TA1's rising edge (TA2), D15..D0 after the next 16
; rising edges, and release after D0's. Write: TA must be 1, 0 and the 16 bits
; are applied after D0. A short preamble, ST 00 (Clause 45), OP 00 or 11 or
; another PHYAD: MDIO stays released and we hunt for the next preamble.
;
; No timeout: every state moves on MDC edges alone, so an STA that abandons a
; frame leaves us in it (docs/bench-firmware.md, the measured consequences).
;
; RX FIFO: a word per write frame addressed to us, whether it was applied or
; not: 1 << 23 | REGAD << 18 | TA << 16 | data. Pushed without blocking: a
; host that stops reading loses records, not bus timing.
;
; r0 = bit counter, r1 = register number / value, r2, r3 as above.
;
; Timing annotations (python -m pe.timing firmware/mdio_phy.pasm --defs pe.mdio:phy_timing_defines):
; @assume clkdiv=1                                 ; 50 MHz: 1 tick = 20 ns
; @deadline c2o from=w.done to=drv.done adjust=3..4 min=4 max=5 "MDIO driven 4..5 clocks after MDC rises at the pad (802.3: <= 300 ns, 15 clocks)"
; @deadline wc2o from=sb.done to=sdrv.done min=1 max=1 unit=tick "a data bit is out 1 tick after the WAIT"
; @deadline dec from=hj.enter to=disp.enter avoid=hb max=10 unit=tick reach=conditional "last header bit's loop branch to the table jump"
; @deadline sendup from=send.enter to=taw.enter min=2 max=2 unit=tick "table entry's successor to the TA1 WAIT"
; @deadline wrup from=wj.enter to=hunt_w.enter avoid=wb max=14 unit=tick "last data bit of a write back to the preamble hunt"
.program mdio_phy
.shift_out left
.shift_in left
.out_pins MDIO 1
.in_pins MDIO
hunt:
    pin MDIO, hiz
h0:
    ldi r0, 31
h1:
    wait rise MDC                     ; @point hunt_w
    jmp !pin MDIO, h0                 ; a zero before 32 ones: start again
    jmp r0--, h1
st:
    wait rise MDC                     ; (more ones are fine)
    jmp pin MDIO, st
    mov isr, null                     ; ST's 0 seen: ST1, OP, PHYAD, REGAD
    ldi r0, 12
hb:
    wait rise MDC
    in pins, 1
    jmp r0--, hb                      ; @point hj
    mov r1, isr
    shr r1, 5                         ; ST1 OP1 OP0 PHYAD
    cmp r1, 0xC0 | PHYAD              ; 1 10 PHYAD
    jmp z, read
    cmp r1, 0xA0 | PHYAD              ; 1 01 PHYAD
    jmp z, write
    jmp h0                            ; not ours, or not Clause 22
; ---------------------------------------------------------------- read
read:
    mov r1, isr
    and r1, 31
    jmp !r1, table                    ; (redundant with the jump below: it is the path by
    shl r1, 1                         ;  which the static checker, which does not follow a
    add r1, table + BASE              ;  computed jump, reaches `send`)
    mov pc, r1                        ; @point disp
table:                                ; each entry: r1 = the value, jmp send
    mov r1, r2                        ; 0 control
    jmp send
    mov r1, r3                        ; 1 status
    jmp send
    ldi r1, V2
    jmp send
    ldi r1, V3
    jmp send
    mov r1, crc                       ; 4 advertising
    jmp send
    ldi r1, V5
    jmp send
    ldi r1, V6
    jmp send
    ldi r1, V7
    jmp send
    ldi r1, V8
    jmp send
    ldi r1, V9
    jmp send
    ldi r1, V10
    jmp send
    ldi r1, V11
    jmp send
    ldi r1, V12
    jmp send
    ldi r1, V13
    jmp send
    ldi r1, V14
    jmp send
    ldi r1, V15
    jmp send
    ldi r1, V16
    jmp send
    ldi r1, V17
    jmp send
    ldi r1, V18
    jmp send
    ldi r1, V19
    jmp send
    ldi r1, V20
    jmp send
    ldi r1, V21
    jmp send
    ldi r1, V22
    jmp send
    ldi r1, V23
    jmp send
    ldi r1, V24
    jmp send
    ldi r1, V25
    jmp send
    ldi r1, V26
    jmp send
    ldi r1, V27
    jmp send
    ldi r1, V28
    jmp send
    ldi r1, V29
    jmp send
    ldi r1, V30
    jmp send
    ldi r1, V31
    jmp send
send:
    mov osr, r1
    out null, 16                      ; the value in OSR[31:16]
    wait rise MDC                     ; TA1: the STA has released MDIO   @point w @point taw
    pin MDIO, lo                      ; TA2 is ours: 0   @point drv
    ldi r0, 15
sbit:
    wait rise MDC                     ; @point w @point sb
    out pins, 1                       ; D15..D0   @point drv @point sdrv
    jmp r0--, sbit
    wait rise MDC                     ; the STA samples D0 here   @point w
    pin MDIO, hiz                     ; @point drv
    jmp h0
; ---------------------------------------------------------------- write
write:
    mov r1, isr
    and r1, 31                        ; REGAD
    mov r0, r1
    or r0, 32                         ; the record's marker, shifted to bit 23
    mov isr, r0
    ldi r0, 17
wb:
    wait rise MDC                     ; TA and the data
    in pins, 1
    jmp r0--, wb                      ; @point wj
    mov r0, ::isr
    shr r0, 14                        ; [1] = ISR[16], [0] = ISR[17]
    cmp r0, 1                         ; TA = 1, 0
    jmp !z, wdone
    mov r0, isr                       ; the data
    jmp !r1, w0
    cmp r1, 4
    jmp !z, wdone
    mov crc, r0                       ; register 4
    jmp wdone
w0:                                   ; register 0
    ldi r1, 0x7DFF
    and r0, r1
    mov r2, r0
wdone:
    push noblock
    jmp h0
