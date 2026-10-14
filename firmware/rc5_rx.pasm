; Philips RC-5 infrared receiver: written after the RTL freeze (tag
; rc-93c2137-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; Pin IR: the output of a 36 kHz IR receiver module (demodulated, active
; low: low while a burst is received; IN_BASE = IR). TB ticks per bit
; (define, 128): CLKDIV = f_clk * 1.7778 ms / TB, 694.4 at 50 MHz (1 tick =
; 13.9 us).
;
; RC-5 is bi-phase (a 1 is a space then a burst: high then low here) with
; one transition in the middle of every bit. The first fall of the line is
; the middle of S1. From each mid-bit edge the receiver waits 3/4 of a bit
; and samples the line (the first half of the next bit: its value), then
; waits for that bit's mid-bit edge with an edge WAIT started there, which
; ignores the boundary edge before it and accepts the mid-bit edge from
; 3/4 to 5/4 of a bit after the last one (a timeout is an error). Every bit
; re-synchronises on its own edge, so a sender's rate and the receiver
; module's lengthening of bursts only need to stay inside +-25 % per bit.
; A frame is 14 bits; then the line must be idle (high) for 2 bits before
; the next frame is looked for.
;
; RX FIFO: a frame: 1 << 16 | the 14 bits (S1 in bit 13, S2, T, A4..A0,
; C5..C0); an error: 2 << 16 | bits still expected (the frame is dropped).
; A full RX FIFO drops the word and sets flag FOV. pe.postfreeze3.rc5_record.
;
; r0 = bits left, r1 = scratch, r3 = timeouts; the ISR collects the bits.
;
; Timing annotations (python -m pe.timing firmware/rc5_rx.pasm -D IR=16 -D TB=128 -D FOV=7):
; @assume clkdiv=694.444                           ; 50 MHz: 1 tick = 1/128 bit
; @deadline s1 from=s1 to=sample avoid=start min=96 max=96 unit=tick "S1's mid-bit edge -> S2 sampled 3/4 bit later"
; @deadline smp from=edge to=sample avoid=err,start min=96 max=96 unit=tick reach=conditional "every mid-bit edge -> the next bit sampled 3/4 bit later"
; @deadline win from=sample to=edge.enter max=4 unit=tick "the edge WAIT starts right after the sample (3/4 bit)"
; @deadline rec from=last to=put avoid=err,bit max=8 unit=tick "the frame is in the FIFO right after its last edge"
.program rc5_rx
.shift_in left
.in_pins IR
start:
    ldi r3, 2 * TB
    wait 0 pin IR timeout r3          ; idle for 2 bits?
    jmp t, idle
    wait 1 pin IR                     ; no: wait for the end of the burst
    jmp start
idle:
    wait 0 pin IR                     ; S1's mid-bit edge (the first burst)   @point s1
    nop                       [TB / 4 - 2]
    jmp pin IR, start                 ; gone 1/4 bit later: noise
    mov isr, null
    ldi r1, 9
    in r1, 4                          ; the record's tag, S1 = 1
    ldi r0, 12                [TB / 2 - 5]
bit:
    mov r1, pins                      ; the first half of the next bit: its value   @point sample
    in r1, 1
    ldi r3, TB / 2
    wait edge IR timeout r3           ; its mid-bit edge, 3/4..5/4 bit after the last   @point edge
    jmp t, err
    nop                       [3 * TB / 4 - 4]
    jmp r0--, bit                     ; @point last
    call put
    jmp start
err:                                  ; no mid-bit transition
    mov isr, null
    ldi r1, 2
    in r1, 16
    in r0, 16
    call put
    jmp start
put:                                  ; push the ISR; a full FIFO drops it
    jmp rxf, full
    push noblock                      ; @point put
    ret
full:
    flag set FOV
    push noblock                      ; @point put
    ret
