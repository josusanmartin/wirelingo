; Philips RC-5 infrared remote transmitter with its 36 kHz carrier: written
; after the RTL freeze (tag rc-93c2137-t48ba), firmware only
; (docs/post-freeze-protocols.md).
;
; Pin IR: the LED driver (active high): OUT_BASE = IR (OUT_COUNT 1) and the
; side-set pin (SIDE_BASE = IR, `.side_set 1 opt`). 1 tick = 1/3 carrier
; period: CLKDIV = f_clk / 108 kHz (462.96 at 50 MHz). The carrier is 1 tick
; on, 2 off (36 kHz, 33 % duty). RC-5: a half bit is 32 carrier periods
; (96 ticks, 888.9 us at 36 kHz), a bit 1.778 ms; bi-phase with a 1 sent as
; a space then a burst, a 0 as a burst then a space. A frame is 14 bits, MSB
; first: S1 = 1, S2 = 1 (or the complement of command bit 6: RC-5X), the
; toggle bit T, 5 address bits, 6 command bits; frames start every 64 bits
; (113.78 ms) while a key is held.
;
; Every carrier period is three instructions, the same in a burst and in a
; space (`mov pins, r1` puts the half's level on the pin for one tick, the
; next two drive it low by side-set), and the bookkeeping between two halves
; (the next half's level, the counter) fills the off ticks of the last period:
; the carrier keeps its 3-tick rhythm across half bits, and two bursts in a
; row (a 1 followed by a 0) are one burst of 64 periods. The 28 halves of a
; frame and one more (a space) are in the OSR; PULL_THRESH = 29, so `jmp
; !osre` sees the end of the frame in that space.
;
; The toggle bit is kept here (r3) across frames: it changes on every new
; key press and stays for repeats. Host protocol, TX FIFO word: [5:0]
; command, [6] command bit 6 (S2 = its complement; 0 for plain RC-5),
; [11:7] address, [12] repeat (the same toggle bit as the last frame).
; Frames start 12288 ticks (113.78 ms) apart while the host keeps the FIFO
; fed. pe.postfreeze3.rc5_word.
;
; r0 = carrier periods left in the half, r1 = the half's level, r2 = the
; code word / scratch, r3 = the toggle bit.
;
; Timing annotations (python -m pe.timing firmware/rc5_tx.pasm -D IR=8):
; @assume clkdiv=462.963                           ; 50 MHz: 108 kHz ticks
; @deadline carrier from=on to=on avoid=frame_end min=3 max=3 unit=tick reach=conditional "36 kHz: one period every 3 ticks, in a half and across halves"
; @deadline duty from=on to=off min=1 max=1 unit=tick "on for 1 tick of 3"
; @deadline half from=half to=half avoid=frame_end min=96 max=96 unit=tick reach=conditional "every half bit: 32 carrier periods"
; @deadline frame from=first to=first stall=next:0 min=12288 max=12288 unit=tick "frame to frame: 64 bits (113.78 ms)"
.program rc5_tx
.side_set 1 opt
.shift_out left 29
.shift_in left
.out_pins IR 1
.define PAD 12288 - 29 * 96 - 51     ; the rest of 64 bits after the frame
    ldi r3, 0                         ; the toggle bit
    mov r1, null
next:
    pull block                        ; a key code   @point next
    mov r2, osr
    and r2, 0x1000
    jmp !r2, newkey
    jmp rep                           ; a repeat: the same toggle bit
newkey:
    xor r3, 1                         ; a new key press: toggle
rep:                                  ; build the 14-bit code word: 1, S2, T, A, C
    ldi r2, 0x2000                    ; S1
    mov r0, osr
    and r0, 64
    xor r0, 64
    shl r0, 6                         ; S2 = !C6 -> bit 12
    or r2, r0
    mov r0, r3
    shl r0, 11
    or r2, r0                         ; T -> bit 11
    mov r0, osr
    and r0, 0xF80
    shr r0, 1                         ; A -> bits 10..6
    or r2, r0
    mov r0, osr
    and r0, 63
    or r2, r0                         ; C -> bits 5..0
    shl r2, 2                         ; MSB at bit 15
    mov isr, null
    ldi r0, 13
halves:                               ; each bit b -> halves !b, b (1 = burst)
    mov r1, r2
    shr r1, 15
    xor r1, 1
    in r1, 1
    xor r1, 1
    in r1, 1
    shl r2, 1
    jmp r0--, halves
    in null, 4                        ; 28 halves, then spaces
    mov osr, isr
    ldi r0, 30
    out r1, 1                 [1]     ; the first half's level (S1: a space)   @point first
.wrap_target
period:
    mov pins, r1                      ; on, if the half is a burst   @point on
    jmp !osre, more           side 0  ; @point off
    jmp frame_end             side 0
more:
    jmp r0--, period          side 0
    mov pins, r1                      ; the half's last period   @point on
    out r1, 1                 side 0  ; @point off
    ldi r0, 30                side 0  ; @point half
.wrap
frame_end:                            ; 2 ticks into the 29th half (a space)
    ldi r0, PAD
pad:
    jmp r0--, pad
    jmp next
