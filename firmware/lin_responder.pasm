; LIN responder (slave node) with sync-field bit-rate detection: written after
; the RTL freeze (tag rc-93c2137-t48ba), firmware only
; (docs/post-freeze-protocols.md).
;
; Pins: RX = the transceiver's RXD (the bus), IN_BASE = RX; TX = its TXD
; (1 = recessive), OUT_BASE = TX, OUT_COUNT = 1. 1 tick = 0.5 us: CLKDIV =
; f_clk / 2 MHz (25 at 50 MHz); the node's own clock may be off by +-14 %
; (LIN 2.x, a node without a crystal): it measures the commander's bit time
; on every sync field and uses it for the rest of the frame.
;
;   break:  dominant for >= BRK ticks (define; 11 nominal bits = 1144 at
;           19200 bit/s)
;   sync:   0x55; the time from its start bit's falling edge to its 5th
;           falling edge is 8 bits: T = (that + 4) / 8 ticks, accepted if
;           TMIN <= T <= TMAX (defines)
;   PID:    received at T; parity P0 = ID0^ID1^ID2^ID4, P1 = !(ID1^ID3^ID4^ID5)
;           checked here
;   then:   if the host has staged a response for this ID, publish it (data
;           and the checksum computed here, classic or enhanced), else receive
;           whatever response follows, up to an inter-byte timeout of BTMO
;           ticks, and check its checksum both ways.
; Every byte is received at mid-bit of the measured T, re-synchronised on its
; start bit. A dominant field read as 0x00 with a framing error is a break:
; the frame ends there and the new header is handled.
;
; Storage: T lives in the CRC register (CRCCTRL = width 16, no taps: the host
; writes 15 << 16), the only spare 16-bit register; r0 = delays / timeouts,
; r1 = sum / count, r2 = PID (and the byte count in [15:8]), r3 = byte.
;
; Host protocol.
;   TX FIFO (staging): word 0 = ID | enhanced << 8 | n << 12 (n = 1..8 data
;   bytes), then ceil(n / 4) words of data, the first byte in [7:0]. The
;   staged response waits in OSR until a header with its ID arrives (headers
;   for other IDs leave it there), and is sent once.
;   RX FIFO records:
;     header   T << 16 | 0x1000 | parity error << 8 | PID
;     byte     0x2000 | byte              (each byte received)
;     end      0x3000 | count << 4 | classic ok | enhanced ok << 1
;              | framing error << 2 | published << 3
;   F2 is set if a record was dropped because the RX FIFO was full (the host
;   must read at least one record per byte time, 520 us at 19200 bit/s).
;   pe.postfreeze.lin_records decodes them.
;
; Timing annotations (python -m pe.timing firmware/lin_responder.pasm -D RX=16 -D TX=9 -D BRK=1144 -D TMIN=89 -D TMAX=119 -D BTMO=2000):
; @assume clkdiv=25
; Bit timing is T ticks, a value measured at run time: the checker cannot
; bound loops whose count is a measurement (it reports them as unbounded), so
; test_lin.py measures the bits instead. What it can check:
; @deadline header_record from=pid_ret to=hdr_rec avoid=idle,ferr_hdr max=60 unit=tick "the header record <= 60 ticks (30 us) after the PID's stop bit"
.program lin_responder
.shift_in right
.shift_out right
idle:
    wait 0 pin RX                     ; dominant
    ldi r0, BRK
    wait 1 pin RX timeout r0
    jmp t, brk                        ; long enough: a break
    jmp idle                          ; a byte or noise
brk:
    wait 1 pin RX                     ; the break delimiter
    ldi r0, BRK
    wait 0 pin RX timeout r0          ; sync start bit, within BRK ticks
    jmp t, idle
    ldi r3, 0xFFFF
    wait fall RX timeout r3           ; falling edges at bits 2, 4, 6, 8
    wait fall RX timeout r3
    wait fall RX timeout r3
    wait fall RX timeout r3
    ldi r0, 0xFFFF
    sub r0, r3                        ; ticks waited: 8 bits - 6
    add r0, 10                        ; + 6, + 4 to round
    shr r0, 3                         ; T
    cmp r0, TMIN
    jmp c, idle                       ; too fast
    cmp r0, TMAX + 1
    jmp !c, idle                      ; too slow
    mov crc, r0
    mov r0, crc
    shl r0, 2                         ; 4 bits: stop of the sync + space
    call rx_byte                      ; PID
    jmp t, idle                       ; @point pid_ret
    jmp r0, ferr_hdr
    mov r2, r3                        ; PID
    ; expected PID from the ID: P0 in r3 bit 6, P1 in r0 bit 7
    mov r1, r2
    and r1, 63
    mov r3, r1
    mov r0, r1
    shr r0, 1
    xor r3, r0                        ; ID0 ^ ID1
    shr r0, 1
    xor r3, r0                        ; ^ ID2
    shr r0, 2
    xor r3, r0                        ; ^ ID4
    and r3, 1
    shl r3, 6
    mov r0, r1
    shr r0, 1                         ; ID1
    shr r1, 3
    xor r0, r1                        ; ^ ID3
    shr r1, 1
    xor r0, r1                        ; ^ ID4
    shr r1, 1
    xor r0, r1                        ; ^ ID5
    xor r0, 1
    and r0, 1
    shl r0, 7
    or r0, r3
    mov r1, r2
    and r1, 63
    or r0, r1                         ; the PID it should be
    cmp r0, r2
    ldi r3, 0x1000
    jmp z, hdr_ok
    ldi r3, 0x1100                    ; parity error
hdr_ok:
    or r3, r2
    mov r0, crc
    mov isr, null
    in r3, 16
    in r0, 16
    call record                       ; header: T << 16 | 0x1000 | perr << 8 | PID   @point hdr_rec
    and r3, 0x100
    jmp r3, listen                    ; bad parity: never answer it
    jmp !osre, staged
    jmp txne, stage
    jmp listen
stage:
    pull block                        ; a staged response: word 0
staged:
    mov r3, osr
    and r3, 63
    mov r1, r2
    and r1, 63
    cmp r3, r1
    jmp !z, listen                    ; for another ID: keep it
; ---------------------------------------------------------------- publish
    out null, 8                       ; ID
    out r3, 1                         ; enhanced?
    ldi r1, 0
    jmp !r3, classic
    mov r1, r2                        ; enhanced: the sum starts with the PID
classic:
    out null, 3
    out r2, 4                         ; n
    out null, 16                      ; OSR empty: the data words follow
    sub r2, 1
    shl r2, 8                         ; n - 1 in [11:8] (the PID is no longer needed)
    mov r0, crc                       ; response space: 1 bit
pspace:
    jmp r0--, pspace
pbyte:
    pull ifempty block
    out r3, 8
    add r1, r3
    cmp r1, 256
    jmp c, pnc
    sub r1, 255                       ; end-around carry
pnc:
    call tx_byte
    sub r2, 0x100
    jmp !c, pbyte                     ; until n bytes
    mov r3, r1
    xor r3, 0xFF
    and r3, 0xFF
    call tx_byte                      ; checksum
    out null, 32                      ; leftover bytes of the last word
    ldi r3, 0x3008                    ; end: published
    mov isr, r3
    call record
    jmp idle
; ---------------------------------------------------------------- listen
listen:
    and r2, 0xFF                      ; PID, count 0
    ldi r1, 0                         ; sum
lbyte:
    ldi r0, BTMO
    call rx_byte
    jmp t, lend
    jmp r0, lferr
    ldi r0, 0x2000
    or r0, r3
    mov isr, r0
    call record                       ; byte
    add r1, r3
    cmp r1, 256
    jmp c, lnc
    sub r1, 255
lnc:
    add r2, 0x100
    jmp lbyte
lend:
    ldi r3, 0x3000
    jmp lflags
lferr:
    jmp r3, lferr_byte
    ldi r3, 0x3004                    ; 0x00 + framing error: a break
    call lflags_sub
    wait 1 pin RX
    jmp brk
lferr_byte:
    ldi r3, 0x3004                    ; a framing error
lflags:
    call lflags_sub
    jmp idle
lflags_sub:                           ; r3 |= count << 4 | checksum flags; send it
    mov r0, r2
    shr r0, 4
    and r0, 0xF0
    or r3, r0                         ; count
    cmp r1, 0xFF
    jmp !z, lnot_classic
    or r3, 1                          ; classic: data + checksum = 0xFF
lnot_classic:
    mov r0, r2
    and r0, 0xFF
    add r1, r0                        ; + PID
    cmp r1, 256
    jmp c, lnc2
    sub r1, 255
lnc2:
    cmp r1, 0xFF
    jmp !z, lnot_enh
    or r3, 2                          ; enhanced: PID + data + checksum = 0xFF
lnot_enh:
    mov isr, r3
    jmp record                        ; (its RET returns to our caller)
ferr_hdr:                             ; the PID had a framing error
    jmp r3, idle
    wait 1 pin RX                     ; 0x00: a break
    jmp brk
; ---------------------------------------------------------------- subroutines
record:                               ; push ISR; F2 if the RX FIFO is full
    jmp rxf, rec_full
    push noblock
    ret
rec_full:
    flag set 2
    ret
rx_byte:                              ; r0 = ticks to wait for the start bit.
    wait fall RX timeout r0           ; T = 1: none. Else r3 = byte,
    jmp t, rx_ret                     ; r0 = 1 on a framing error.
    mov r0, crc
    mov r3, r0
    shr r3, 1
    add r0, r3                        ; 1.5 T: the middle of bit 0
    sub r0, 9
    ldi r3, 0x80
rx_wait0:
    jmp r0--, rx_wait0
rx_bit:
    in pins, 1                        ; LSB first          @point sample
    mov r0, crc
    sub r0, 6
rx_wait:
    jmp r0--, rx_wait
    shr r3, 1
    jmp r3, rx_bit
    in null, 24
    mov r3, isr                       ; the byte
    ldi r0, 0
    jmp pin RX, rx_ret                ; stop bit
    ldi r0, 1                         ; @point sample
rx_ret:
    ret
tx_byte:                              ; send r3[7:0], 8N1, T ticks per bit
    shl r3, 1
    or r3, 0x600                      ; start 0, stop 1, end marker
tx_bit:
    mov pins, r3                      ; @point drive
    mov r0, crc
    sub r0, 7
tx_wait:
    jmp r0--, tx_wait
    shr r3, 1
    cmp r3, 1
    jmp !z, tx_bit
    ret
