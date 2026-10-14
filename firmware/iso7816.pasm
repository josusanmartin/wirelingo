; ISO 7816-3 smart-card interface device (reader), character layer for T=0:
; written after the RTL freeze (tag rc-93c2137-t48ba), firmware only
; (docs/post-freeze-protocols.md). ISO/IEC 7816-3 (2006) clauses 6..8 and 10.
;
; Pins: IO = the card's I/O contact, a uio pin in open-drain mode (OD_MASK)
; with a pull-up (OUT_BASE = IO, OUT_COUNT = 1, IN_BASE = IO); RST = the
; card's reset contact (any output pin). CLK comes from the second core
; (firmware/iso7816_clk.pasm); VCC is the host's (a GPIO). TB ticks per etu
; (define, 124): with the clock core at CLKDIV D, this core runs at CLKDIV
; 6 * D, so one etu is exactly 372 CLK periods (Fi = 372, Di = 1, the
; values until a PPS changes them; other F/D are other TB/CLKDIV pairs).
;
; Characters: a start bit (L), 8 data bits, a parity bit making the number
; of ones even, then the guard time (12 etu from start to start, + N extra
; guard etu when we send). Direct convention: H = 1, LSB first; inverse
; convention: L = 1, MSB first. The convention is detected on TS, the first
; character of the answer to reset (0x3B direct, 0x3F inverse; as raw levels
; LSB first 0x3B and 0x03), and kept in flag FINV (the host may also set it).
;
; Error signal and repetition (T=0): a receiver that finds a parity error
; holds I/O low from 10.5 etu after the character's start for 1..2 etu (we:
; 1.5), and the sender repeats the character. Receiving, we signal up to
; RMAX errors per character, then accept it and flag it; sending, we sample
; I/O at 11 etu, and on an error signal wait for the line and send the
; character again 13 etu after the previous start, at most RMAX times more,
; then abort the command.
;
; Host protocol. TX FIFO, one command word, [1:0] command, [15:8] count:
;   0 SEND: count = n bytes (1..255), [23:16] N (extra guard time, etu);
;           then ceil(n / 4) data words, the first byte in [7:0]. Before the
;           first character the line must have been idle for TURN etu (the
;           16 etu between characters in opposite directions).
;   1 RECV: count = m characters (1..255); [23:16] the first character's
;           timeout in units of 8 etu (0: WT), then WT (define, etu) between
;           characters; [24] the first character is TS: detect the
;           convention (an answer to reset).
;   2 RESET: RST low for RSTLO etu (>= 400 CLK), then high (cold reset, with
;           CLK and VCC already on; warm reset). I/O released; FINV cleared.
;   3 DEACT: RST low, I/O low (then the host stops CLK and VCC).
; RX FIFO:
;   received character: 1 << 24 | gave up << 12 | error signals << 8 | byte
;   RECV end:           2 << 24 | inverse << 18 | bad TS << 17 | timeout << 16
;                       | error signals in all << 8 | characters received
;   SEND end:           3 << 24 | aborted << 16 | repetitions << 8 | bytes not sent
; A full RX FIFO drops the record and sets flag FOV. pe.postfreeze3.
;
; r0 = bit counter / repetitions / wait loops, r1 = the frame (sending) or
; parity (receiving), r2 = counts and flags, r3 = timeouts and scratch; the
; CRC register holds N or m in [15:8] and the error count in [7:0] (the host
; sets CRCCTRL to width 16 with the taps off).
;
; Timing annotations (python -m pe.timing firmware/iso7816.pasm -D IO=0 -D RST=8 -D TB=124 -D WT=64 -D RMAX=4 -D RSTLO=2 -D TURN=6 -D FINV=5 -D FOV=7;
; WT only counts the timeout loop, which the checker unrolls: 9600 takes it minutes):
; @assume clkdiv=36                                ; 50 MHz with the clock core at 6: 1 etu = 4464 clocks
; @deadline tx_bit from=bit to=bit avoid=rel min=124 max=124 unit=tick "every bit we send: 1 etu"
; @deadline tx_par from=bit to=rel avoid=bit min=124 max=124 unit=tick "parity bit, then I/O released at 10 etu"
; @deadline tx_esmp from=rel to=esmp min=124 max=124 unit=tick "the card's error signal sampled at 11 etu"
; @deadline tx_gt from=rel to=bit min=248 unit=tick "the next start at >= 12 etu (+ N)"
; @deadline tx_rep from=esig_seen to=bit min=248 unit=tick "a repetition >= 2 etu after the error signal is seen"
; @deadline rx_mid from=sw to=mid avoid=rx_more min=62 max=62 unit=tick reach=conditional "start bit seen -> its middle"
; @deadline rx_bit from=sample to=sample avoid=sw min=124 max=124 unit=tick reach=conditional "every bit sampled 1 etu after the last"
; @deadline rx_err from=par to=esig avoid=rx_got,rx_giveup,ts_bad min=99 max=149 unit=tick reach=conditional "our error signal starts 10.5 etu (+-0.2) after the start bit's edge"
; @deadline rx_esig from=esig to=esig_rel min=186 max=186 unit=tick "our error signal lasts 1.5 etu"
; @deadline rx_turn from=par to=sw.enter avoid=esig,rx_end,ts_bad stall=put:0 max=248 unit=tick reach=conditional "watching for the next start bit before 11.5 etu"
.program iso7816
.shift_out right
.shift_in right
.out_pins IO 1
.in_pins IO
.define HB TB / 2
top:
    pull block                        ; a command
    out r0, 8                         ; [1:0] command
    out r2, 8                         ; count
    out r3, 8                         ; N / first timeout
    out r1, 8                         ; flags
    jmp !r0, send
    sub r0, 1
    jmp !r0, recv
    sub r0, 1
    jmp !r0, reset
deact:
    pin RST, lo
    pin IO, lo
    jmp top
reset:
    pin RST, lo
    pin IO, hi                        ; I/O in reception mode
    flag clr FINV
    ldi r3, RSTLO * TB
rst_lo:
    jmp r3--, rst_lo
    pin RST, hi
    jmp top
; ================================================================ send
send:                                 ; r2 = n, r3 = N
    shl r3, 8
    mov crc, r3                       ; N << 8, no repetitions yet
    shl r2, 8                         ; n left -> [15:8]
turn:
    ldi r3, TURN * TB
    wait 0 pin IO timeout r3          ; the line idle for TURN etu?
    jmp t, tx_next
    wait 1 pin IO                     ; no: the card is still sending
    jmp turn
tx_next:
    pull ifempty block                ; 4 bytes per word   @point tpull
    shr r2, 8
    shl r2, 8                         ; n left, no byte
    out r3, 8
    or r2, r3                         ; the byte -> [7:0]
    ldi r0, 0                         ; repetitions of this character
tx_try:                               ; even parity of the byte
    mov r1, r2
    and r1, 255
    mov r3, r1
    shr r3, 4
    xor r1, r3
    mov r3, r1
    shr r3, 2
    xor r1, r3
    mov r3, r1
    shr r3, 1
    xor r1, r3
    and r1, 1
    jmp !f FINV, tx_dir
    xor r1, 1                         ; inverse: the parity level is the complement
    mov isr, null
    mov r3, r2
    in r3, 8
    mov r3, ::isr                     ; reverse8(byte)
    xor r3, 0xFF                      ; ... complemented: the levels, first bit = LSB
    jmp tx_frame
tx_dir:
    mov r3, r2
    and r3, 255               [3]     ; (as long as the inverse path)
tx_frame:
    shl r1, 9
    or r1, 0x400                      ; the end marker
    shl r3, 1                         ; start bit 0
    or r1, r3
txb:
    mov pins, r1              [TB - 4]  ; @point bit
    shr r1, 1
    cmp r1, 1
    jmp !z, txb
    mov pins, r1              [TB - 1]  ; 10 etu: release I/O   @point rel
    jmp pin IO, tx_ok                 ; 11 etu: the card's error signal?   @point esmp
    add r0, 1                         ; the card signalled an error
    mov r3, crc
    add r3, 1
    mov crc, r3
    cmp r0, RMAX + 1
    jmp !c, tx_abort                  ; repeated RMAX times: give up
    ldi r3, 2 * TB
    wait 1 pin IO timeout r3          ; the error signal ends (by 13 etu)
    ldi r3, 2 * TB - 21               ; 2 etu after it is seen   @point esig_seen
tx_rdly:
    jmp r3--, tx_rdly
    jmp tx_try
tx_ok:                                ; 11 etu
    sub r2, 0x100
    mov r3, r2
    shr r3, 8
    jmp !r3, tx_done                  ; all sent
    mov r3, crc
    shr r3, 8
    mov r1, r3
    shl r1, 7
    shl r3, 2
    sub r1, r3                        ; N * 124 ticks (N etu: TB = 124)
    ldi r3, TB - 43                   ; the rest of the 12 etu
tx_g12:
    jmp r3--, tx_g12
tx_gn:
    jmp r1--, tx_gn                   ; N etu
    jmp tx_next
tx_abort:
    mov r0, r2
    shr r0, 8                         ; bytes not sent
    or r0, 0x100                      ; (+ aborted, below: 1 << 16)
    jmp tx_rec
tx_done:
    ldi r0, 0
tx_rec:
    mov r3, crc
    and r3, 255
    shl r3, 8
    mov r1, r0
    and r1, 255
    or r3, r1                         ; repetitions << 8 | bytes not sent
    shr r0, 8                         ; aborted
    or r0, 0x300                      ; tag 3
    in r3, 16
    in r0, 16
    call put
    out null, 32                      ; drop the rest of an aborted command's data
    jmp top
; ================================================================ receive
recv:                                 ; r2 = m, r3 = first timeout (8 etu), r1 = flags
    mov r0, r2
    shl r0, 8
    mov crc, r0                       ; m << 8, no errors yet
    and r1, 1
    shl r1, 12
    or r2, r1                         ; [12] TS pending
    mov r0, r3
    jmp r0, rx_wait
    ldi r0, WT / 8
rx_wait:                              ; r0 chunks of 8 etu
    ldi r3, 8 * TB - 4
    wait 0 pin IO timeout r3          ; a start bit   @point sw
    jmp t, rx_more
    nop                       [HB - 3]
    jmp pin IO, rx_wait               ; mid start bit: high again? noise   @point mid
    ldi r0, 7
    ldi r1, 0                 [TB - 3]
rbit:
    in pins, 1                        ; data bits, the first at 1.5 etu   @point sample
    mov r3, pins
    xor r1, r3                [TB - 4]
    jmp r0--, rbit
    mov r3, pins                      ; the parity bit, 9.5 etu   @point sample @point par
    xor r1, r3                        ; bit 0: the levels' sum
    in null, 24
    mov r3, isr                       ; the raw levels, first bit in bit 0
    mov r0, r2
    and r0, 0x1000
    jmp !r0, rx_conv                  ; not TS
    xor r2, 0x1000                    ; TS: the convention
    cmp r3, 0x3B
    jmp z, ts_dir
    cmp r3, 0x03
    jmp !z, ts_bad
    flag set FINV
    jmp rx_conv
ts_dir:
    flag clr FINV             [1]
rx_conv:                              ; even parity: the levels' sum is 0 (direct) or 1 (inverse)
    jmp !f FINV, rx_par
    xor r1, 1
rx_par:
    and r1, 1
    jmp r1, rx_perr
    jmp !f FINV, rx_dir
    in r3, 8
    mov r1, ::isr
    xor r1, 0xFF                      ; inverse: ~reverse8(levels)
    jmp rx_got
rx_dir:
    mov r1, r3                [2]
rx_got:                               ; the byte (+ error signals for it)
    mov r0, r2
    and r0, 0xF00
    or r1, r0
rx_put:
    in r1, 16
    ldi r0, 0x100
    in r0, 16                         ; tag 1
    call put
    and r2, 0xFF                      ; no errors pending
    sub r2, 1
    jmp !r2, rx_end                   ; m characters
    ldi r0, WT / 8
    jmp rx_wait
rx_perr:                              ; a parity error   (16..20 ticks after the parity sample)
    mov r0, r2
    and r0, 0xF00
    cmp r0, RMAX * 0x100
    jmp !c, rx_giveup
    add r2, 0x100
    mov r0, crc
    add r0, 1
    mov crc, r0
    ldi r3, TB - 21
rx_edly:
    jmp r3--, rx_edly
    pin IO, lo                [TB + HB - 1]  ; the error signal, 10.5 etu   @point esig
    pin IO, hi                        ; released at 12 etu   @point esig_rel
    ldi r0, WT / 8
    jmp rx_wait                       ; the repetition
rx_giveup:                            ; RMAX error signals: take it as it is
    or r2, 0x1000
    jmp !f FINV, rx_gdir
    in r3, 8
    mov r1, ::isr
    xor r1, 0xFF
    jmp rx_gflag
rx_gdir:
    mov r1, r3
rx_gflag:
    mov r0, r2
    and r0, 0x1F00
    or r1, r0
    jmp rx_put
rx_more:
    jmp r0--, rx_wait
    ldi r0, 1                         ; timeout
    jmp rx_rec
ts_bad:
    ldi r0, 2                         ; not a TS
    jmp rx_rec
rx_end:
    ldi r0, 0
rx_rec:                               ; flags in r0: [0] timeout, [1] bad TS
    jmp !f FINV, rx_rec2
    or r0, 4
rx_rec2:
    or r0, 0x200                      ; tag 2
    mov r3, crc
    mov r1, r3
    shr r1, 8                         ; m
    and r2, 0xFF                      ; characters left
    sub r1, r2                        ; received
    and r3, 0xFF
    shl r3, 8
    or r3, r1                         ; error signals << 8 | received
    in r3, 16
    in r0, 16
    call put
    jmp top
put:                                  ; push the ISR; a full FIFO drops it
    jmp rxf, full
    push noblock                      ; @point put
    ret
full:
    flag set FOV
    push noblock
    ret
