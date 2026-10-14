; MIDI 1.0 transmitter with running status: written after the RTL freeze
; (tag rc-93c2137-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; Pin TX: the line driver's input (OUT_BASE = TX, OUT_COUNT = 1; idle high,
; 8N1, LSB first). TB ticks per bit (define, 32): CLKDIV = f_clk /
; (31250 * TB), 50 at 50 MHz (exact: 32.000 us per bit).
;
; Takes the words midi_rx produces (pe.postfreeze2.midi_message_word):
; [7:0] status, [15:8] and [23:16] data, [31:24] = the number of data bytes
; (0..2), or a SysEx data word (tag 0x81..0x83, tag & 3 bytes). Running
; status, as MIDI 1.0 allows a sender: a channel message (0x80..0xEF) with
; the same status as the last one sent goes without its status byte; any
; system common or SysEx status (0xF0..0xF7) cancels running status, and
; real-time (0xF8..0xFF) leaves it alone. RS (define): 0 sends every
; status byte. The host orders real-time bytes between messages: the
; firmware does not insert them into one.
;
; r0 = the byte being sent, r1 = status, r2 = data 1, r3 = data 2; the
; OSR keeps the tag, the ISR the running status.
;
; Timing annotations (python -m pe.timing firmware/midi_tx.pasm -D TX=8 -D TB=32 -D RS=1):
; @assume clkdiv=50                                ; 50 MHz: 1 us ticks
; @deadline bit from=bit to=bit avoid=stop min=32 max=32 unit=tick "start and data bits: 32 us"
; @deadline stop from=stop to=bit min=32 stall=next:0 max=64 unit=tick "the stop bit and the space before the next byte (host keeping up): under 2 bits"
.program midi_tx
.shift_out right
.out_pins TX 1
    mov isr, null                     ; no running status
next:
    pull block                        ; @point next @loop max=1 (every word sends a byte: no channel message without data)
    out r1, 8                         ; status
    out r2, 8
    out r3, 8                         ; (the OSR keeps the tag)
    mov r0, osr
    and r0, 0x80
    jmp r0, sysex
    cmp r1, 0xF8
    jmp !c, send_status               ; real-time: running status unchanged
    cmp r1, 0xF0
    jmp c, channel
    mov isr, null                     ; system common / SysEx: cancels it
    jmp send_status
channel:
    mov r0, isr                       ; the last status sent
    mov isr, r1                       ; this one is the running status now
    cmp r0, r1
    jmp !z, send_status
    ldi r0, RS
    jmp r0, data                      ; the same status: leave it out (RS = 1)
send_status:
    mov r0, r1
    call tx
data:
    mov r0, osr
    and r0, 3
    jmp !r0, next
    mov r0, r2
    call tx
    mov r0, osr
    and r0, 3
    cmp r0, 2
    jmp !z, next
    mov r0, r3
    call tx
    jmp next
sysex:                                ; tag & 3 raw data bytes
    mov r0, r1
    call tx
    mov r0, osr
    and r0, 3
    cmp r0, 2
    jmp c, next
    mov r0, r2
    call tx
    mov r0, osr
    and r0, 3
    cmp r0, 3
    jmp c, next
    mov r0, r3
    call tx
    jmp next
tx:                                   ; r0[7:0], 8N1
    and r0, 0xFF
    shl r0, 1
    or r0, 0x200                      ; start bit 0, end marker
txb:
    mov pins, r0              [TB - 4]  ; start and data bits    @point bit
    shr r0, 1
    cmp r0, 1
    jmp !z, txb
    pin TX, hi                [TB - 2]  ; stop bit               @point stop
    ret
