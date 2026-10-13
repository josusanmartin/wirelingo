; NEC infrared remote transmitter with a 38 kHz carrier: written after the
; RTL freeze (tag rc-93c2137-t48ba), firmware only
; (docs/post-freeze-protocols.md).
;
; Pin IR (an LED driver, active high). 1 tick = 1/3 carrier period:
; CLKDIV = f_clk / 114 kHz (438.6 at 50 MHz). The carrier is 1 tick on,
; 2 off: 38 kHz at 33 % duty. NEC's unit u = 562.5 us is 64 ticks here
; (561.4 us, -0.2 %); a burst of 1 u is 21 carrier periods (63 ticks).
;   frame:  leader 16 u burst + 8 u space, 32 bits LSB first (address,
;           ~address, command, ~command), stop burst 1 u;
;           bit 0 = 1 u burst + 1 u space, bit 1 = 1 u burst + 3 u space
;   repeat: 16 u burst + 4 u space + 1 u burst
; Frames and repeats start every 192 u (108 ms) while the host keeps the
; FIFO fed: the firmware pads each to 108 ms from its leader.
;
; Host protocol, TX FIFO word: [15:0] address, [23:16] command, [25:24] mode:
; 0 = standard (address [7:0]; the firmware sends address, ~address,
; command, ~command), 1 = extended (16-bit address as given), 2 = repeat
; code (other fields ignored). pe.postfreeze.nec_word.
;
; r0 = loop counter / bit, r1 = address field, r2 = command field,
; r3 = ticks until the next leader. OSR = the 32 data bits.
;
; Every path from the `pull` to the leader's burst takes the same 23 ticks,
; so frames and repeats start exactly 12288 ticks (192 u, 107.8 ms) apart
; when the host keeps up: FRAME_PAD and REP_PAD are what is left of 192 u.
;
; Timing annotations (python -m pe.timing firmware/nec_tx.pasm -D IR=8).
; The points are the `call burst` instructions: the burst's first carrier
; period starts one tick later at every call site.
; @assume clkdiv=438.597                           ; 50 MHz
; @deadline carrier from=on to=on avoid=ret_b min=3 max=3 unit=tick "38 kHz carrier inside a burst"
; @deadline duty from=on to=off min=1 max=1 unit=tick "carrier on for 1/3 period"
; @deadline burst1 from=c1 to=ret_b min=64 max=64 unit=tick "1 u burst: the call + 21 carrier periods (63 ticks)"
; @deadline burst0 from=c0 to=ret_b min=64 max=64 unit=tick "the same for a 0"
; @deadline burst16 from=lead to=ret_b min=1024 max=1024 unit=tick "16 u leader: the call + 341 carrier periods"
; @deadline leader from=lead to=cb min=1536 max=1536 unit=tick "leader burst + space: 24 u (13.5 ms)"
; @deadline zero from=c0 to=cb min=128 max=128 unit=tick "bit 0: 2 u (1.125 ms)"
; @deadline one from=c1 to=cb min=256 max=256 unit=tick "bit 1: 4 u (2.25 ms)"
; @deadline rep from=rlead to=cb min=1280 max=1280 unit=tick "repeat: 16 u burst + 4 u space"
; @deadline rep_period from=rlead to=rlead avoid=lead stall=pull:0 min=12288 max=12288 unit=tick reach=conditional "repeat to repeat: 192 u (107.8 ms)"
; @deadline rep_to_frame from=rlead to=lead avoid=rlead stall=pull:0 min=12288 max=12288 unit=tick reach=conditional "repeat to frame: 192 u"
.program nec_tx
.shift_out right
.shift_in right
.define FRAME_PAD 6563                ; 12288 - 5725: leader, 32 x 128, stop, overheads
.define REP_PAD 10915                 ; 12288 - 1373
top:
    pull block                        ; @point pull
    out r1, 16                        ; address
    out r2, 8                         ; command
    out r3, 2                         ; mode
    cmp r3, 2
    jmp z, repeat
    jmp r3, ext_pad                   ; extended: the address as given
    and r1, 0xFF
    mov r3, r1
    xor r3, 0xFF
    shl r3, 8
    or r1, r3                         ; address | ~address << 8
ext:
    and r2, 0xFF
    mov r3, r2
    xor r3, 0xFF
    shl r3, 8
    or r2, r3                         ; command | ~command << 8
    mov isr, null
    in r1, 16
    in r2, 16
    mov osr, isr                      ; OSR = the 32 data bits, sent LSB first
    ldi r3, FRAME_PAD
    ldi r0, 340                       ; leader: 341 carrier periods (16 u)
    call burst                        ; @point lead @point cb
    ldi r0, 504                       ; 8 u space
    call space
bitloop:
    out r0, 1
    jmp !r0, b0
    ldi r0, 20                        ; 1 u burst
    call burst                        ; @point c1 @point cb
    ldi r0, 181                       ; 3 u space
    call space
    sub r3, 128                       ; a 1 is 128 ticks longer than a 0
    jmp next
b0:
    ldi r0, 20
    call burst                        ; @point c0 @point cb
    ldi r0, 54                        ; 1 u space
    call space
    nop
next:
    jmp !osre, bitloop
    nop                       [1]     ; as `out` + `jmp` of another bit
pad_tail:
    ldi r0, 20                        ; stop burst, 1 u
    call burst                        ; @point cb
    call space_r3                     ; pad to 192 u
    jmp top
repeat:
    ldi r3, REP_PAD           [15]    ; as long as a frame's setup
    ldi r0, 340
    call burst                        ; 16 u    @point rlead @point cb
    ldi r0, 249
    call space                        ; 4 u
    jmp pad_tail
ext_pad:
    nop                       [3]     ; as long as the 5 instructions skipped
    jmp ext
; ---- subroutines
burst:                                ; r0 + 1 carrier periods, 3 ticks each
    pin IR, hi                        ; @point on
    pin IR, lo                        ; @point off
    jmp r0--, burst
    ret                               ; @point ret_b
space_r3:
    mov r0, r3
space:                                ; r0 + 1 ticks
    jmp r0--, space
    ret
