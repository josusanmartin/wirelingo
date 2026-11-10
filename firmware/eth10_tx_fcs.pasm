; 10BASE-T transmitter that appends the FCS in the engine (v0.3 ISA, docs/isa.md):
; eth10_tx, with the 32-bit CRC tap on OUT. Everything else is eth10_tx's:
; Manchester, preamble + SFD, TP_IDL, normal link pulses while idle, 4 ticks per
; bit (clk = 40 MHz at CLKDIV 1, or 50 MHz at CLKDIV 1.25), side-set TD+/TD-.
;
; CRC: the 802.3 CRC-32 with OUT_TAP, CRCCTRL = 0x00AF1DB7 and POLY_HI = 0x04C1
; (isa.crcctrl(0x04C11DB7, 32, out_tap=1)). Every `out r3, 1` of a frame bit
; feeds it; the core presets it at the frame's start.
;
; Host protocol (as eth10_tx, without the FCS):
;   1. write R0 = number of frame bits - 1, the frame being destination MAC ..
;      payload (8 * bytes - 1); the core computes and sends the FCS;
;   2. push the frame bytes little-endian, 4 per word (bytes go out LSB first);
;   3. set flag F_GO. The core sends preamble + SFD + frame + FCS and clears
;      F_GO when the line is idle again.
;
; The FCS is the complement of the CRC after the last data bit, x^31 first.
; In the last data bit's three free ticks the core moves the CRC, bit-reversed,
; into the OSR (OUT shifts right, so CRC[31] comes out first) and takes its
; first bit; the FCS blocks send each bit inverted (`fone` for a CRC 0, `fzero`
; for a CRC 1), so no tick is needed for the complement. r1 counts the 32 FCS
; bits (`jmp r1--`: test and count in one tick); its free tick is a second jump
; that closes the FCS loop (the wrap closes the data loop).
.program eth10_tx_fcs
.side_set 2
.shift_out right autopull 32
.fifo join_tx
idle:
    nop                       side 1 [3]   ; link pulse: 100 ns H
    ldi r1, 9999              side 0
idle_wait:                                 ; 10000 x 64 ticks = 16 ms
    jmp f F_GO, frame         side 0 [31]
    jmp r1--, idle_wait       side 0 [31]
    jmp idle                  side 0
frame:
    mov crc, ~null            side 0       ; CRC-32 preset
    ldi r1, 31                side 0       ; the FCS bits after the first
    ldi r2, 30                side 0       ; 31 x "10", then "11" (7 x 0x55, 0xD5, LSB first)
pre:
    nop                       side 2 [1]   ; 1
    nop                       side 1 [1]
    nop                       side 1 [1]   ; 0
    jmp r2--, pre             side 2 [1]
    nop                       side 2 [1]   ; 1
    nop                       side 1 [1]
    nop                       side 2 [1]   ; 1 (last SFD bit)
    out r3, 1                 side 1       ;   first frame bit (autopull; the CRC tap)
    jmp r3, one               side 1       ;   falls into `zero` for a 0
.wrap_target
zero:
    jmp !r0, zlast            side 1       ; H  (r0 = 0: this is the last data bit)
    sub r0, 1                 side 1       ; H
    out r3, 1                 side 2       ; L  next bit
    jmp !r3, zero             side 2       ; L
one:
    jmp !r0, olast            side 2       ; L
    sub r0, 1                 side 2       ; L
    out r3, 1                 side 1       ; H
    jmp r3, one               side 1       ; H  (a 0 wraps to `zero`)
.wrap
zlast:                                     ; the last data bit, a 0: its CRC is final
    mov osr, ::crc            side 1       ; H  OSR[0] = CRC[31]
    out r3, 1                 side 2       ; L  (r3 = CRC[31]; the FCS bit is its complement)
    jmp r3, fzero             side 2       ; L  falls into `fone` for a CRC 0
fone:                                      ; an FCS bit 1: L then H
    jmp r1--, fo2             side 2       ; L
    nop                       side 2       ; L  the last FCS bit:
    jmp tp_idl                side 1 [1]   ; H  TP_IDL: 300 ns H after a 1
olast:                                     ; the last data bit, a 1
    mov osr, ::crc            side 2       ; L
    out r3, 1                 side 1       ; H
    jmp !r3, fone             side 1       ; H  falls into `fzero` for a CRC 1
fzero:                                     ; an FCS bit 0: H then L
    jmp r1--, fz2             side 1       ; H
    nop                       side 1       ; H  the last FCS bit:
    nop                       side 2 [1]   ; L
    jmp tp_idl                side 1       ; TP_IDL: 275 ns H after a 0
fo2:
    out r3, 1                 side 2       ; L  the next CRC bit
    jmp r3, fo_z              side 1       ; H
    jmp fone                  side 1       ; H  CRC 0: an FCS 1
fo_z:
    jmp fzero                 side 1       ; H  CRC 1: an FCS 0
fz2:
    out r3, 1                 side 1       ; H
    jmp r3, fz_z              side 2       ; L
    jmp fone                  side 2       ; L
fz_z:
    jmp fzero                 side 2       ; L
tp_idl:
    nop                       side 1 [9]
    flag clr F_GO             side 0
    jmp !osre, flush          side 0       ; (the FCS used the whole OSR: never taken)
    jmp idle_wait             side 0
flush:
    out null, 32              side 0
    jmp idle_wait             side 0
