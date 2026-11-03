; 10BASE-T match, then answer (docs/eth10-reaction.md): core 0 alone, with the line
; unit both ways, and no host after loading. It hears frames as eth10_match does
; (same SFD hunt, EtherType ETHERTYPE, a 16-bit field in the first two payload bytes
; compared with a threshold). For each frame whose field >= the threshold it owes one
; reply. When a frame has ended and a reply is owed, it waits for RXP to stay without
; a transition for 96 bit times (802.3's inter-frame gap, as eth10_tx_cs does), then
; sends a fixed 64-byte reply frame through the line unit: preamble, SFD, frame, TP_IDL.
;
; The reply is part of the program: 32 sixteen-bit chunks C0..C31 (frame bytes from
; the destination MAC through the FCS, little-endian pairs, LSB first on the wire),
; given as defines by pe.ethreply, which also computes the FCS. The chip has no
; CRC-32, so the reply can't depend on the frame it answers. Its last bit must be 0:
; TP_IDL is END_BITS = 3 bits high after it, 300 ns (pe.ethreply checks).
;
; The line unit: LINECFG = EN | CODE 2 | DIFF | END 2 | END_BITS 3 | RX_EN, TXP = TD+
; (uo0, TD- = uo1), RXP = ui0; LINEDIV RX = clocks per bit, TX = half a bit (2.5
; clocks at 50 MHz, 2 at 40). CLKDIV 1. IFG_TICKS = 96 bit times in clocks less the
; path around the wait (pe.ethreply.WINDOW_TRIM), QUIET_TICKS the wait after our own
; frame (below). Pins: HIT (uo2) is high from the field's last bit to the end of the
; matching frame (after its TP_IDL when a reply is owed).
;
; Carrier while a reply is owed: the window starts once RXP is low, so the far end's
; TP_IDL ends before it, and any transition in it is a frame (or a link pulse)
; starting: the core goes to hear it, and a frame's end starts the wait again.
;
; A frame that arrives while the reply is going out (a collision:
; nothing here detects it, jams or retries) is not heard: after its last chunk the core
; waits until RXP has been quiet for QUIET_TICKS, so it never starts hunting in the
; middle of a frame, and the line unit's 32 stale bits of that frame end in a frame end
; that the hunt consumes. QUIET_TICKS covers the rest of our own frame too, so the
; 96-bit window for a second owed reply starts after the first has ended.
;
; Owed replies are counted in r0 (16 bits). The field goes to the RX FIFO with
; `push noblock`, for a host that wants records; nobody has to read them.
.program eth10_reply
.define ETHERTYPE 0xB588          ; 0x88B5 on the wire, first byte low
.define HIT 10                    ; uo2
.define RXP 16                    ; ui0
.shift_in right
.shift_out right
; @assume clkdiv=1
; @deadline ifg from=defer.done to=first.done avoid=hear stall=first:0..0 reach=conditional "RXP low, 96 bit times of quiet, the first chunk queued (the TX queue is empty: QUIET_TICKS outlasts our last frame)"
; @deadline frame_end from=fend.done to=defer.enter avoid=drain,idle reach=conditional "a frame's end, with a reply owed, to the low wait"
    pull block                    ; the threshold
    mov r2, osr
    ldi r3, ETHERTYPE
    ldi r0, 0
    ldi r1, 0x5555                ; the first preamble chunk waits in the OSR
    mov osr, r1
frame:
    mov isr, null
    in line, 32                   ; most of the preamble, and link pulses end here
    jmp t, ended
hunt:                             ; the SFD ends in 1, 1
    in line, 1
    jmp t, ended
    mov r1, ::isr                 ; the newest bit is bit 0
    and r1, 3
    cmp r1, 3
    jmp !z, hunt
    in line, 32                   ; destination, source: 96 bits
    jmp t, ended
    in line, 32
    jmp t, ended
    in line, 32
    jmp t, ended
    in line, 16                   ; EtherType
    jmp t, ended
    in null, 16
    mov r1, isr
    cmp r1, r3
    jmp !z, drain
    in line, 16                   ; the field
    jmp t, ended
    in null, 16
    mov r1, isr
    cmp r1, r2
    jmp c, drain                  ; below the threshold
    pin HIT, hi
    add r0, 1                     ; one more reply owed
    mov isr, r1
    push noblock
drain:                            ; the rest of the frame
    in line, 16
fend:
    jmp t, ended
    jmp drain
ended:
    jmp !r0, idle
defer:                            ; the window starts when RXP is low: after TP_IDL
    wait 0 pin RXP                ; (at once if it is low already)
low:
    pin HIT, lo
    ldi r1, IFG_TICKS
win:
    wait edge RXP timeout r1      ; IFG_TICKS ticks of quiet, or the first transition
    jmp t, first
hear:
    jmp frame                     ; carrier: a frame (or a link pulse) is arriving, hear it
idle:
    pin HIT, lo
    jmp frame
first:
    out line, 16                  ; preamble: 31 x "10", then "11" (0x55 x 7, 0xD5)
    ldi r1, 0x5555
    mov osr, r1
    out line, 16
    mov osr, r1
    out line, 16
    ldi r1, 0xD555
    mov osr, r1
    out line, 16
    ldi r1, C0
    mov osr, r1
    out line, 16
    ldi r1, C1
    mov osr, r1
    out line, 16
    ldi r1, C2
    mov osr, r1
    out line, 16
    ldi r1, C3
    mov osr, r1
    out line, 16
    ldi r1, C4
    mov osr, r1
    out line, 16
    ldi r1, C5
    mov osr, r1
    out line, 16
    ldi r1, C6
    mov osr, r1
    out line, 16
    ldi r1, C7
    mov osr, r1
    out line, 16
    ldi r1, C8
    mov osr, r1
    out line, 16
    ldi r1, C9
    mov osr, r1
    out line, 16
    ldi r1, C10
    mov osr, r1
    out line, 16
    ldi r1, C11
    mov osr, r1
    out line, 16
    ldi r1, C12
    mov osr, r1
    out line, 16
    ldi r1, C13
    mov osr, r1
    out line, 16
    ldi r1, C14
    mov osr, r1
    out line, 16
    ldi r1, C15
    mov osr, r1
    out line, 16
    ldi r1, C16
    mov osr, r1
    out line, 16
    ldi r1, C17
    mov osr, r1
    out line, 16
    ldi r1, C18
    mov osr, r1
    out line, 16
    ldi r1, C19
    mov osr, r1
    out line, 16
    ldi r1, C20
    mov osr, r1
    out line, 16
    ldi r1, C21
    mov osr, r1
    out line, 16
    ldi r1, C22
    mov osr, r1
    out line, 16
    ldi r1, C23
    mov osr, r1
    out line, 16
    ldi r1, C24
    mov osr, r1
    out line, 16
    ldi r1, C25
    mov osr, r1
    out line, 16
    ldi r1, C26
    mov osr, r1
    out line, 16
    ldi r1, C27
    mov osr, r1
    out line, 16
    ldi r1, C28
    mov osr, r1
    out line, 16
    ldi r1, C29
    mov osr, r1
    out line, 16
    ldi r1, C30
    mov osr, r1
    out line, 16
    ldi r1, C31
    mov osr, r1
    out line, 16                  ; the last chunk is queued: about 3.5 us still to go
    sub r0, 1
    ldi r1, 0x5555                ; the next reply's first chunk
    mov osr, r1
quiet:                            ; our own frame's end, and then a quiet RXP
    ldi r1, QUIET_TICKS
    wait edge RXP timeout r1
    jmp t, ended
    jmp quiet
