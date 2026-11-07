; DMX512 transmitter (ANSI E1.11, DMX512-A): written after the RTL freeze
; (tag rc-01ce16d-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; Line: 250 kbit/s, 8N2, LSB first, idle (mark) high; TX = an OUT pin driving
; the RS-485 transceiver's DI. 1 tick = 1 us: CLKDIV = f_clk / 1 MHz (50 at
; 50 MHz, exact), 4 ticks per bit.
;
; Packet: BREAK 176 us (E1.11 transmit: >= 92 us), MARK AFTER BREAK 12 us
; (>= 12 us), then n slots (the START code first): each slot is a start bit,
; 8 data bits and 2 stop bits, 44 us, followed by MTBS us of mark (the "mark
; time between slots", a define: 0 = back to back, as fast as DMX512 goes;
; sigrok 0.5.3's dmx512 decoder needs MTBS >= 1, docs/post-freeze-protocols.md). If the host's data is late,
; the line marks (high) between slots or after the MAB, which E1.11 allows up
; to 1 s. Short packets are padded with mark before the next BREAK so that
; BREAK to BREAK is >= 1204 us (E1.11's minimum packet period).
;
; Host protocol (TX FIFO): one header word, [9:0] = n (1..513 slots including
; the START code; 0 is ignored), then ceil(n / 4) data words, 4 slots per
; word, the first slot in [7:0] (pe.postfreeze.dmx_words). Nothing comes back.
; The line marks between packets for as long as the host leaves the FIFO
; empty: a DMX receiver expects a new packet within 1 s.
;
; r0 = bit counter, r1 = slots left, r2 = break-to-break budget (us).
;
; Defines: TX (pin), MTBS (us). Timing annotations, for MTBS = 0
; (python -m pe.timing firmware/dmx512_tx.pasm -D TX=8 -D MTBS=0):
; @assume clkdiv=50                                ; 50 MHz, 1 tick = 1 us
; @deadline break from=brk to=mab min=4600 max=8800 "BREAK: E1.11 TX >= 92 us (176 nominal)"
; @deadline mab from=mab to=start avoid=brk min=600 stall=refill:0 "MAB: E1.11 TX >= 12 us (and < 1 s)"
; @deadline bit from=start to=bit avoid=stop min=4 max=4 unit=tick "start bit: 4 us"
; @deadline data_bit from=bit to=bit avoid=stop min=4 max=4 unit=tick "data bit to data bit: 4 us"
; @deadline last_bit from=bit to=stop avoid=bit min=4 max=4 unit=tick "bit 7 to the stop bits: 4 us"
; @deadline slot from=start to=start avoid=brk stall=refill:0 min=44 max=44 unit=tick reach=conditional "slot to slot inside a packet, host keeping up: 44 us"
; @deadline stop from=stop to=start avoid=brk min=8 unit=tick reach=conditional "2 stop bits (+ mark time if the host is late)"
; @deadline stop_last from=stop to=brk avoid=start min=8 unit=tick reach=conditional "the last slot's stop bits are complete before the next BREAK"
; BREAK to BREAK >= 1204 us is not a checker deadline: it rests on r2 = PAD - 44 n,
; a relation between two registers the checker does not track (it would take the
; pad loop's minimum as 1 tick). test_dmx512.py measures it for n = 1..40, 512, 513.
.program dmx512_tx
.shift_out right
.define PAD 1008                      ; BREAK to BREAK = 196 + PAD us when padded (1204)
top:
    pull block                        ; header (the line marks while the FIFO is empty)
    out r1, 10                        ; n
    out null, 22                      ; OSR empty: the data words follow
    jmp !r1, top                      ; n = 0: no packet
    ldi r2, PAD                       ; budget left after BREAK + MAB + overheads
    pin TX, lo                [175]   ; BREAK, 176 us                       @point brk
    pin TX, hi                [10]    ; MARK AFTER BREAK, 12 us             @point mab
slot:
    pull ifempty block                ; next 4 slots (a late host stretches the mark)  @point refill
    pin TX, lo                        ; start bit                           @point start
    ldi r0, 7                 [2]
bit:
    out pins, 1                       ; data bit, LSB first                 @point bit
    jmp r0--, bit             [2]
    pin TX, hi                        ; 2 stop bits                         @point stop
    sub r2, 44 + MTBS                 ; 44 + MTBS us per slot
    sub r1, 1                 [3 + MTBS]
    jmp r1, slot
    cmp r2, PAD + 1                   ; wrapped below 0: the packet is long enough
    jmp !c, top
pad:
    jmp r2--, pad                     ; mark before break: r2 + 1 us
    jmp top
