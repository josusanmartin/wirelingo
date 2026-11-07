; DALI application controller (IEC 62386-101 forward frames out, backward
; frames in): written after the RTL freeze (tag rc-01ce16d-t48ba), firmware
; only (docs/post-freeze-protocols.md).
;
; Pins: TX = the bus level we drive (OUT_BASE = TX, OUT_COUNT = 1; 1 = idle /
; released, 0 = the interface shorts the bus), RX = the bus level seen through
; the receiver (IN_BASE = RX; 1 = idle). The interface circuit's inversions
; are the board's business. 1 tick = Te / TE (TE ticks per half bit, define;
; 32): CLKDIV = f_clk / (2400 * TE), 651.04 at 50 MHz.
;
; Bits are bi-phase: a 1 is low then high, a 0 high then low, Te = 416.67 us
; per half; a frame is a start bit (a 1), n data bits MSB first, then the
; line idles high (the stop condition). Forward frames of 16 (control gear)
; or 24 bits (control devices, ed. 2), or any n in 1..24.
;
; Before each forward frame the bus must have been idle (high) for SETTLE
; ticks, measured from when the command is taken (33.6 Te = 14.0 ms: the
; 13.5 ms between forward frames of DALI-2's highest priority, and more than
; the 2.4 ms after a backward frame); a bus that goes low meanwhile (another
; controller, a late backward frame) restarts the count once it is idle.
;
; Backward frame: if asked, the start bit's fall must come within WIN of
; the end of the forward frame's last bit (10.5 ms: the settling-time
; window's maximum; its minimum, 5.5 ms, is the gear's duty: the time is
; reported and the host judges it). Then every interval between edges is
; measured (the timeout counter of a kind-7 WAIT, which compares the line
; with its last level, so an edge during the bookkeeping is not lost) and
; classified as IEC 62386-101 ed. 2 tables 18/19 ask, with the limits in the
; grey areas: a half bit is 22..44 ticks (286..573 us; the standard accepts
; 333.3..500), a double half bit 45..83 ticks (586..1081; 666.7..1000), only
; a half bit after a bit boundary or the start bit's fall; a mid-bit edge
; gives the bit (the new level). No edge for 2.6 Te with the line high ends
; the frame, which must then stay idle for 2400 us after its last edge (the
; receiver's stop condition); anything else is a violation, after which the
; firmware waits for 2400 us of idle bus. A DALI-2 controller takes a
; violated backward frame as an answer (several gear answering at once):
; the host decides.
;
; Host protocol. TX FIFO, one word per forward frame: the n data bits
; left-aligned in [31:8] (MSB first), n in [4:0] (1..24), [5] = 1 to receive
; a backward frame. RX FIFO, two words per frame with [5] = 1:
;   bits:   the received bits right-aligned, the start bit included (a good
;           backward frame: 9 bits, 0x100 | answer);
;   status: [7:0] bits received (start bit included), [8] violation,
;           [9] no answer (nothing within WIN), [31:16] the settling time:
;           ticks from the end of the forward frame's last bit to the start
;           bit's fall (+-1 tick).
; pe.postfreeze.dali_word, dali_records.
;
; r0 = bits to send / the window counter (kept for the report), r1 = bit /
; the line level, r2 = the command / bits received and flags, r3 = timeouts.
;
; Timing annotations (python -m pe.timing firmware/dali_ctrl.pasm -D TX=8 -D RX=16 -D TE=32):
; @assume clkdiv=651.04                            ; 50 MHz: 1 tick = Te / 32
; @deadline half from=half to=half avoid=fend min=32 max=32 unit=tick reach=conditional "every half bit: Te = 32 ticks"
; @deadline last_half from=sec to=fend avoid=half min=32 max=32 unit=tick reach=conditional "the last half bit"
; @deadline settle from=fend to=half avoid=bstart min=1075 unit=tick reach=conditional "without a backward frame: forward frame to forward frame: >= SETTLE (14.0 ms) of idle bus"
; @deadline rx_start from=bstart to=wb.enter avoid=noans min=8 max=8 unit=tick reach=conditional "start bit fall -> first interval WAIT: P_B + 1"
; @deadline rx_bb from=wb to=wm.enter avoid=viol,rend min=11 max=11 unit=tick reach=conditional "boundary -> mid-bit WAIT: P_M + 1"
; @deadline rx_mm from=wm to=wm.enter avoid=viol,rend,wb min=11 max=11 unit=tick reach=conditional "mid-bit -> mid-bit WAIT: P_M + 1"
; @deadline rx_mb from=wm to=wb.enter avoid=viol,rend,wm min=8 max=8 unit=tick reach=conditional "mid-bit -> boundary WAIT: P_B + 1"
.program dali_ctrl
.shift_out left
.shift_in left
.out_pins TX 1
.in_pins RX
.define SETTLE TE * 336 / 10          ; 14.0 ms
.define WIN TE * 252 / 10 - 6         ; 10.5 ms from the frame's end (the WAIT starts 6 ticks after it)
.define HMIN TE * 7 / 10              ; shortest half bit
.define HMAX TE * 14 / 10             ; longest half bit
.define DEND TE * 26 / 10             ; longest double half bit: no edge after it ends the frame
.define STOPT TE * 577 / 100          ; 2400 us: the stop condition after the last edge
.define P_B 7                         ; instructions from a WAIT's completion to bnd's WAIT
.define P_M 10                        ; ... to mid's WAIT
.define DMAX DEND - P_M - 1           ; the interval WAITs' timeout
.define XB1 P_B + 2 + DMAX - HMIN     ; at bnd: r3 < XB1 <=> interval >= HMIN
.define XB2 P_B + 1 + DMAX - HMAX     ;         r3 < XB2 <=> interval > HMAX
.define XM1 P_M + 2 + DMAX - HMIN     ; at mid: the same
.define XM2 P_M + 1 + DMAX - HMAX
top:
    pull block                        ; the command
    mov r2, osr                       ; n | reply flag
idle:
    ldi r3, SETTLE
    wait 0 pin RX timeout r3          ; does the bus stay idle for SETTLE?
    jmp t, send
    wait 1 pin RX                     ; no: someone's frame; wait for its end
    jmp idle
send:
    mov r0, r2
    and r0, 31
    sub r0, 1                         ; n - 1 (jmp r0--)
    pin TX, lo                [TE - 1]  ; start bit: low      @point half
    pin TX, hi                [TE - 2]  ; then high           @point half
txbit:
    out r1, 1                         ; the next bit, MSB first
    mov pins, ~r1             [TE - 1]  ; first half: its complement  @point half
    mov pins, r1              [TE - 3]  ; second half: the bit  @point half @point sec
    jmp r0--, txbit
    nop
    pin TX, hi                        ; end of the last bit: the stop condition  @point fend
    mov r0, r2
    and r0, 32
    jmp !r0, top                      ; no backward frame expected
    ldi r1, 1
    ldi r0, WIN
    wait pins != r1 mask 1 timeout r0 ; the backward frame's start bit   @point bstart
    jmp t, noans
    ldi r1, 0                         ; the line is low
    ldi r2, 0                         ; bits received
    mov isr, null             [2]     ; (P_B: 7 instructions to bnd's WAIT)
bnd:                                  ; the last edge was a bit boundary or the start
    ldi r3, DMAX
    wait pins != r1 mask 1 timeout r3 ; @point wb
    jmp t, rend
    xor r1, 1                         ; the new level
    cmp r3, XB1
    jmp !c, viol                      ; shorter than a half bit
    cmp r3, XB2
    jmp c, viol                       ; longer: only a half bit may follow a boundary
    in r1, 1                          ; a mid-bit edge: the bit is the new level
    add r2, 1                 [1]     ; (P_M: 10 instructions to mid's WAIT)
mid:                                  ; the last edge was mid-bit
    ldi r3, DMAX
    wait pins != r1 mask 1 timeout r3 ; @point wm
    jmp t, rend
    xor r1, 1
    cmp r3, XM1
    jmp !c, viol                      ; shorter than a half bit
    cmp r3, XM2
    jmp !c, bnd                       ; a half bit: a boundary edge
    in r1, 1                          ; a double half bit: the next mid-bit edge
    add r2, 1
    jmp mid
rend:                                 ; no edge for DEND ticks
    jmp !r1, viol                     ; the line is low: no stop condition
    ldi r3, STOPT - DEND - 3
    wait pins != r1 mask 1 timeout r3 ; idle until 2400 us after the last edge?
    jmp t, report
viol:
    or r2, 0x100                      ; violation
    ldi r1, 1
vidle:
    ldi r3, STOPT
    wait 0 pin RX timeout r3          ; 2400 us of idle bus
    jmp t, report
    wait 1 pin RX
    jmp vidle
noans:
    ldi r2, 0x200                     ; no answer
    mov isr, null
report:
    push block                        ; the bits
    ldi r1, WIN + 6
    sub r1, r0                        ; the settling time
    in r1, 16
    in r2, 16
    push block                        ; the status
    jmp top
