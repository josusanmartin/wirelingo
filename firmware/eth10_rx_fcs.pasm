; 10BASE-T receiver that checks the FCS in the engine (v0.3 ISA, docs/isa.md):
; eth10_rx plus the 32-bit CRC tap and 16-bit compares (CMPW). First written for
; the v3 study (docs/v3-study.md), with the CRC's top half fixed.
; The line unit recovers the Manchester bits as for eth10_rx: LINECFG = EN | RX_EN
; | CODE 2 (0x4005), LINEDIV[31:16] = 5 (50 MHz), LINEPINS[12:8] = RXP.
; The 802.3 CRC-32 with IN_TAP, bits in wire order: CRCCTRL = 0x009F1DB7 and
; POLY_HI = 0x04C1 (isa.crcctrl(0x04C11DB7, 32, in_tap=1)); over DA .. FCS of a
; good frame it ends at the residue 0xC704DD7B.
;
; The core finds the SFD itself: the 16 newest bits, newest first, are 0xAAAB
; only at the SFD's end (...1010 1010 1010 1011). It then presets the CRC and
; queues the frame proper (DA .. FCS) to the RX FIFO, 24 bits per word in [31:8]
; (the first bit lowest), the last word (its k bits at the top; an empty word
; if k = 0), then a trailer: count << 16 | 0x80 | FCS good ([0]). Only trailers
; have a non-zero low byte. A frame that ends before an SFD (a link pulse,
; noise) leaves no record. The host no longer searches for the SFD or computes
; the CRC; it reads [0].
;
; Timing at 50 MHz (5 clocks per bit): the preamble loop is 6 instructions per
; bit, so the line unit's 32-bit queue holds up to ~13 bits behind by the SFD;
; the frame loop is 3 per bit (the wrap is free) and catches up. (At 40 MHz,
; 4 clocks per bit, the preamble could fill the queue: use 50 MHz.)
; r0 = scratch, r1 = bits after the SFD, r2 = trailer status.
.program eth10_rx_fcs
.shift_in right autopush 24
.shift_out right
frame:
pre:                              ; the preamble, a bit at a time
    in line, 1
    jmp t, lost                   ; the frame ended before an SFD
    mov r0, ::isr                 ; r0[0] = the newest bit, r0[15] 15 bits before it
    mov isr, isr                  ; ISR_CNT = 0: nothing is pushed in the preamble
    cmp r0, 0xAAAB                ; CMPW: the SFD's last bit just came
    jmp !z, pre
    mov isr, null                 ; the frame proper: DA .. FCS
    mov crc, ~null                ; CRC-32 preset
    ldi r1, 0
.wrap_target
bit:
    in line, 1                    ; through the CRC tap
    jmp t, done                   ; the frame ended
    add r1, 1
.wrap
done:
    push block                    ; the last word
    mov osr, crc                  ; the residue
    ldi r2, 0x80                  ; trailer: FCS bad
    out r0, 16
    cmp r0, 0xDD7B                ; CMPW, low half
    jmp !z, trailer
    out r0, 16
    cmp r0, 0xC704                ; high half
    jmp !z, trailer
    ldi r2, 0x81                  ; FCS good
trailer:
    in r2, 16
    in r1, 16                     ; ISR = count << 16 | status, autopushed
    jmp frame
lost:
    mov isr, null
    jmp frame
