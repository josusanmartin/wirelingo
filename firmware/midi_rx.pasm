; MIDI 1.0 receiver and message parser: written after the RTL freeze (tag
; rc-93c2137-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; Pin RX: the opto-isolator's output (IN_BASE = RX; idle high, 8N1, LSB
; first, 31250 baud +-1 %). TB ticks per bit (define, 128): CLKDIV =
; f_clk / (31250 * TB), 12.5 at 50 MHz. Every byte is re-synchronised on its
; start bit (seen by a level WAIT, so a start bit that comes while the
; parser is still busy is not lost) and sampled at mid-bit; the parser
; runs in the half stop bit before the next start bit can come
; (checker: at most 48 of the 64 ticks).
;
; The parser turns the byte stream into whole messages, as the MIDI 1.0
; specification asks of a receiver:
;   * running status: data bytes after a complete channel message start
;     another one with the same status (0x80..0xEF; 1 data byte for 0xCn and
;     0xDn, 2 for the others);
;   * system common (0xF1..0xF7) clears running status: 0xF1 and 0xF3 take 1
;     data byte, 0xF2 2, 0xF4..0xF7 none;
;   * system real-time (0xF8..0xFF) may come anywhere, even inside another
;     message or a SysEx; it is passed on at once and changes nothing;
;   * SysEx: 0xF0, data bytes, then 0xF7, or any other status but real-time
;     ends it (the status is then parsed as usual);
;   * data bytes with no status to belong to are ignored.
;
; Host protocol (RX FIFO), one word per item; tag = [31:24]:
;   tag 0..2: a message, [7:0] status, [15:8] first data byte, [23:16] second,
;             tag = the number of data bytes (real-time, 0xF0 starting a
;             SysEx and 0xF7 ending one: 0);
;   tag 0x81..0x83: SysEx data, tag & 3 bytes in [7:0], [15:8], [23:16];
;   tag 0xC0: a framing error (stop bit low): the byte in [7:0], dropped.
; A full RX FIFO drops the item and sets flag FOV (define) for the host.
; pe.postfreeze2.midi_rx_events turns the words into messages.
;
; r1 = the byte, r2 = running status | data bytes needed << 8 (in SysEx:
; 0xF0 | bytes held << 8), r3 = the first data byte | 0x100 (in SysEx: up
; to 2 bytes), r0 = bit counter / scratch.
;
; Timing annotations (python -m pe.timing firmware/midi_rx.pasm -D RX=16 -D TB=128 -D FOV=7):
; @assume clkdiv=12.5                              ; 50 MHz: 1 tick = 1/128 bit
; @deadline mid from=sw to=sample min=64 max=64 unit=tick "start bit seen -> its middle"
; @deadline bit from=sample to=sample avoid=sw min=128 max=128 unit=tick reach=conditional "start, data and stop bits sampled TB apart"
; @deadline parse from=stop to=sw.enter avoid=ferr stall=put:0 max=63 unit=tick "the parser finishes in the half stop bit (stall=put:0: the host keeps the FIFO from filling)"
.program midi_rx
.shift_in right
.in_pins RX
    ldi r2, 0                         ; no running status
    ldi r3, 0
start:
    wait 0 pin RX                     ; a start bit                     @point sw
    nop                       [TB / 2 - 2]
    jmp pin RX, start                 ; gone by mid-bit: noise          @point sample
    ldi r0, 7                 [TB - 2]
bit:
    in pins, 1                [TB - 2]  ; LSB first                     @point sample
    jmp r0--, bit
    jmp !pin RX, ferr                 ; the stop bit                    @point sample @point stop
    in null, 24
    mov r1, isr                       ; the byte
    cmp r1, 0x80
    jmp c, data
    cmp r1, 0xF8
    jmp !c, realtime
    mov r0, r2
    and r0, 0xFF
    cmp r0, 0xF0
    jmp z, sx_end                     ; a status ends a SysEx
status:
    ldi r3, 0
    mov r2, r1
    cmp r1, 0xF0
    jmp !c, system
    mov r0, r1                        ; channel message
    and r0, 0xE0
    cmp r0, 0xC0                      ; 0xCn, 0xDn: one data byte
    jmp z, need1
need2:
    or r2, 0x200
    jmp start
need1:
    or r2, 0x100
    jmp start
system:
    jmp z, sx_start                   ; 0xF0 (Z from the compare)
    cmp r1, 0xF2
    jmp z, need2
    cmp r1, 0xF4
    jmp c, need1                      ; 0xF1, 0xF3
    ldi r2, 0                         ; 0xF4..0xF7: no data, no running status
realtime:
    in r1, 8                          ; [7:0] status, no data
    in null, 24
    call put
    jmp start
sx_start:
    in r1, 8                          ; report the SysEx start
    in null, 24
    call put
    jmp start
data:
    mov r0, r2
    and r0, 0xFF
    jmp !r0, start                    ; no status: ignored
    cmp r0, 0xF0
    jmp z, sx_data
    jmp r3, second
    mov r0, r2
    and r0, 0x300
    cmp r0, 0x100
    jmp z, one_byte
    mov r3, r1
    or r3, 0x100                      ; the first of two
    jmp start
one_byte:
    in r2, 8
    in r1, 8
    in null, 8
    ldi r0, 1
    jmp done
second:
    in r2, 8
    in r3, 8
    in r1, 8
    ldi r0, 2
done:
    in r0, 8                          ; the number of data bytes
    call put
    ldi r3, 0
    mov r0, r2
    and r0, 0xF0
    cmp r0, 0xF0
    jmp !z, start                     ; a channel message: running status stays
    ldi r2, 0                         ; system common: no running status
    jmp start
sx_data:
    mov r0, r2
    shr r0, 8                         ; bytes held
    jmp !r0, sx0
    sub r0, 1
    jmp !r0, sx1
    in r3, 8                          ; the third: a data word
    shr r3, 8
    in r3, 8
    in r1, 8
    ldi r0, 0x83
    in r0, 8
    call put
    ldi r2, 0xF0
    jmp start
sx0:
    mov r3, r1
    add r2, 0x100
    jmp start
sx1:
    shl r1, 8
    or r3, r1
    add r2, 0x100
    jmp start
sx_end:                               ; flush the bytes held, then the status
    mov r0, r2
    shr r0, 8
    jmp !r0, sx_flushed
    sub r0, 1
    jmp !r0, sx_one
    in r3, 8
    shr r3, 8
    in r3, 8
    in null, 8
    ldi r0, 0x82
    jmp sx_tag
sx_one:
    in r3, 8
    in null, 16
    ldi r0, 0x81
sx_tag:
    in r0, 8
    call put
sx_flushed:
    cmp r1, 0xF7
    jmp !z, status                    ; ended by another status
    ldi r2, 0                         ; 0xF7 (EOX): report it
    jmp realtime
ferr:                                 ; the stop bit is low
    in null, 24
    mov r1, isr
    ldi r0, 0xC0
    in r1, 8
    in null, 16
    in r0, 8
    call put
    wait 1 pin RX                     ; until the line is idle again
    jmp start
put:                                  ; push the ISR; a full FIFO drops it
    jmp rxf, full
    push noblock                      ; @point put
    ret
full:
    flag set FOV
    push noblock                      ; (drops the word, clears the ISR)
    ret
