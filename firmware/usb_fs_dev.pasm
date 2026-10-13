; USB FULL-SPEED device on one core (core 0) with the line unit, on the signed-off
; ISA (no RTL change): docs/usb-full-speed.md. Same host protocol, records and
; flags as usb_ls_dev (pe.usbdev); only the timing differs.
;
; At 48 MHz a full-speed bit is 4 clocks, so every loop that runs per bit must
; take at most 4 instructions (one per clock at CLKDIV = 1):
;   * DATA receive: `in line, 1` (CRC tap) / `jmp t` / `add`: 3 per bit, the
;     loop closed by .wrap (free);
;   * DATA transmit: two checks, `out line, 8`, `jmp r0--`: 4 per byte (32 clocks).
;
; Wiring: D- on the line unit's TXP/RXP pin and D+ on the next pin. Full speed
; idles with D+ high (J), so with D- first the pins see the same J/K/SE0
; patterns as the low-speed firmware with D+ first, and the receiver sees no
; edge at the SE0 -> J end of an EOP (D- stays low). The defines keep the
; low-speed names: DP = the TXP/RXP pin (D-), DM = the next pin (D+), so
; "DM high" is J. The 1.5k pull-up goes on D+.
;
; Line unit: LINECFG = 0x49F3 (as low speed); LINEDIV = 4 << 16 | 4 << 8.
; CRCCTRL: poly 0x8005, 16 bits, IN_TAP.
;
; FIFO limits (8 words each way): an IN packet of up to 24 payload bytes can be
; staged whole (bit count + 7 words); an OUT record of up to 16 payload bytes
; fits (24 bits per word, then the partial word and the trailer). Longer
; packets need the SPI host to stream the FIFOs during the packet. A streamed
; IN whose words don't come in time is cut short (the host sees a bad CRC and
; retries) and sets F_UNDERRUN: the host software clears the TX FIFO and
; re-stages. An OUT token while the RX FIFO lacks room for OUT_WORDS entries
; gets its DATA NAKed (F_NAKOUT), so a slow host never makes the receive loop
; stall (a stall would delay the ACK past the turnaround).
; Turnaround (USB 2.0 §7.1.18.1: 2 to 6.5 bit times = 8 to 26 clocks from the
; host's SE0 -> J to our first K): about 11-16 clocks; the delays on the
; responses keep it off the 2-bit minimum.
;
; Timing annotations (python -m pe.timing firmware/usb_fs_dev.pasm --defs pe.usbdev:defines;
; docs/timing-check.md). Turnaround = from the host's SE0 -> J at the pads to our
; first J -> K at the pads, 8 to 26 clocks. The core sees J (`ack_wait.cond` etc.)
; 1 to 2 clocks after the pad edge (synchroniser, docs/isa.md §2), and our first K
; reaches the pads 2 clocks after `out line` completes (the line unit starts on the
; next cycle and its output register drives the pad the cycle after): adjust=3..4.
; When J becomes visible is an assumption about the host and the line unit: 2..6
; ticks after the frame end reaches the firmware (dend, tok_end and nak_send are each
; 2 ticks after the `in line` that returned T = 1). The receiver samples the SE0 1.5-2.5
; clocks after its edge (§4.7) and J follows 2 bits (8 clocks) later, which gives 3..4
; for clean edges; one more clock each way covers the host's edge jitter.
; stall=...:0: the line transmitter is idle when a response starts, and an IN finds the
; staged packet in the TX FIFO (the host sets F_STAGED after pushing it).
; @deadline ack_ta from=ack_wait.cond after=dend:2..6 to=ack_out avoid=data_bad stall=ack_out:0 min=8 max=26 adjust=3..4 "DATA0/1 -> ACK"
; @deadline in_nak_ta from=resp_wait.cond after=tok_end:2..6 to=resp_out avoid=idle,got_setup,send_data stall=resp_out:0 min=8 max=26 adjust=3..4 reach=conditional "IN -> NAK"
; @deadline in_data_ta from=data_wait.cond after=tok_end:2..6 to=data_out avoid=idle,got_setup,send16,echo stall=staged_pull:0,data_out:0 min=8 max=26 adjust=3..4 reach=conditional "IN -> staged DATA (F_STAGED: the packet is in the TX FIFO)"
; @deadline out_nak_ta from=resp_wait.cond after=nak_send:2..6 to=resp_out stall=resp_out:0 min=8 max=26 adjust=3..4 "OUT DATA without room -> NAK"
; @deadline rx_bit from=rx_bit to=rx_bit.enter avoid=dend max=4 reach=conditional "DATA receive loop keeps up: a bit every 4 clocks"
; @deadline tx_byte from=data_out to=data_out.enter avoid=echo max=32 reach=conditional "DATA transmit loop: the next byte within a byte time"
; Registers: r0 = bit/byte count, r1 = token tail, r2 = scratch, r3 = SYNC+PID.
.program usb_fs_dev
.define F_UNDERRUN F_IN            ; flags 0 and 1 are unused by the one-core firmware
.define F_NAKOUT F_ACK
.define OUT_WORDS 5                ; RX FIFO entries an OUT's record needs: 5 for 8
                                   ; bytes (3 full words, the partial one, the trailer),
                                   ; 8 for 16 bytes; 8 or more: never NAK
.shift_in right autopush 24
.shift_out right autopull 32
idle:
    mov isr, null
    ldi r2, 120                    ; 2.5 us of SE0 is a bus reset
    wait 1 pin DM timeout r2
    jmp t, bus_reset
    wait 0 pin DM                  ; a level: `wait fall` missed an SE0 begun just before it
    jmp !pin DP, idle              ; J -> SE0: reset, rather than packet SYNC
    in line, 8                     ; SYNC
    jmp t, idle
    in line, 8                     ; PID
    jmp t, idle
    mov r3, ::isr                  ; 0x100 | rev8(PID) when SYNC is right
    mov isr, null
    cmp r3, 0x14B                  ; SYNC + ACK: the host took our DATA
    jmp z, got_ack
    mov r2, r3
    and r2, 0x40                   ; 0: a token (PID ends in binary 01)
    jmp !r2, token
    jmp f F_NAKOUT, nak_data       ; DATA of an OUT we can't take
    jmp !f F_EXP, skip             ; DATA we don't expect, or a handshake
; ---------------------------------------------------------------- DATA after SETUP/OUT
    mov crc, ~null                 ; CRC16 over what follows the PID
    ldi r0, 16                     ; bits so far: SYNC + PID
; the per-bit loop: 3 instructions, closed by the wrap (docs/isa.md §4.2)
dbit:
.wrap_target
    in line, 1                     ; through the CRC tap  @point rx_bit
    jmp t, dend
    add r0, 1
.wrap
dend:
    ldi r2, 0x800D                 ; the CRC residue first: the ACK decision is
    mov r1, crc                    ; on the turnaround's critical path
    cmp r1, r2
    jmp !z, data_bad
    cmp r3, 0x1C3                  ; exact SYNC + DATA0, PID complement included
    jmp z, data_ok
    jmp f F_SETUP, data_bad        ; a SETUP's data stage is DATA0
    cmp r3, 0x1D2                  ; exact SYNC + DATA1
    jmp !z, data_bad
data_ok:
    wait 1 pin DM                  ; the host's EOP is over (J)  @point ack_wait
    ldi r2, 0xD280            [2]  ; SYNC + ACK
    mov osr, r2
    out line, 16                   ; @point ack_out
    ldi r1, 0x81                   ; trailer status: good
    jmp trailer
data_bad:
    ldi r1, 0x80                   ; trailer status: bad (no handshake: the host retries)
trailer:
    flag clr F_EXP
    jmp !f F_SETUP, trailer_out
    or r1, 2
    flag clr F_SETUP
trailer_out:
    push block                     ; the last word (bits at the top)
    mov isr, null
    mov r2, r3
    and r2, 0xFF
    shl r2, 8
    or r1, r2                      ; trailer [15:8] = reversed PID
    in r1, 16
    in r0, 16                      ; [31:16] = bits, [15:0] = status: autopushed
    and r1, 1
    jmp r1, echo                   ; our ACK comes back through the receiver
    jmp idle
; ---------------------------------------------------------------- tokens
token:
    in line, 16                    ; address, endpoint, CRC5
    jmp t, idle                    ; too short
    mov r1, ::isr
    mov isr, null
    in line, 1                     ; the frame must end here
    jmp t, tok_end
    jmp skip
tok_end:
filter:
    ldi r2, TOKEN_FILTER           ; written by the host software
    cmp r1, r2
    jmp !z, idle                   ; another address or endpoint, SOF, or a bad CRC5
    cmp r3, 0x196                  ; IN
    jmp z, got_in
    cmp r3, 0x1B4                  ; SETUP
    jmp z, got_setup
    cmp r3, 0x187                  ; OUT
    jmp !z, idle                   ; another token
    flag clr F_SETUP
    mov r2, status                 ; [7:4] = RX FIFO level
    and r2, 0xF0
    cmp r2, (9 - OUT_WORDS) << 4   ; room for the whole record?
    jmp !c, out_full
    flag clr F_NAKOUT
    flag set F_EXP
    jmp idle
out_full:                          ; no: NAK its DATA (flow control; the host retries)
    flag clr F_EXP
    flag set F_NAKOUT
    jmp idle
got_setup:                         ; a SETUP aborts the last transfer:
    flag clr F_NAKOUT              ; (and a SETUP's DATA is never NAKed)
    flag clr F_GOTACK
    flag clr F_STAGED              ; drop a packet still staged for it
flush:
    jmp txne, drop
    flag set F_SETUP
    flag set F_EXP
    jmp idle
drop:
    pull block
    jmp flush
got_in:
    jmp f F_STAGED, send_data
    ldi r3, 0x5A80                 ; SYNC + NAK
; ---------------------------------------------------------------- responses
send16:
    wait 1 pin DM                  ; the host's EOP is over (J)  @point resp_wait
    mov osr, r3               [4]
    out line, 16                   ; @point resp_out
    jmp echo
send_data:
    flag clr F_STAGED
    pull block                     ; bit count  @point staged_pull
    mov r0, osr
    shr r0, 3                      ; bytes
    sub r0, 1
    out null, 32                   ; empty the OSR: the bytes autopull
    wait 1 pin DM                  ; @point data_wait
sbyte:                             ; 4 instructions per byte at most (32 clocks)
    jmp !osre, sgo                 ; bytes left in the OSR
    jmp txne, sgo                  ; or the next word is in the TX FIFO (autopull)
    flag set F_UNDERRUN            ; a streamed packet whose host fell behind: stop
    jmp echo                       ; (the line unit ends the frame; bad CRC, no ACK)
sgo:
    out line, 8                    ; @point data_out
    jmp r0--, sbyte
echo:                              ; our own packet comes back: drop it
    in line, 16
    jmp t, idle
    mov isr, null
    jmp echo
nak_data:
    flag clr F_NAKOUT
nak_skip:
    in line, 16
    jmp t, nak_send
    mov isr, null
    jmp nak_skip
nak_send:
    ldi r3, 0x5A80                 ; SYNC + NAK
    jmp send16
got_ack:
    in line, 8                     ; full extra byte invalidates the handshake;
    jmp t, ack_valid               ; shorter final-bit dribble is allowed
    jmp skip_clear
ack_valid:
    flag set F_GOTACK
    jmp idle                      ; publish only after the tail-end check
skip:                              ; the rest of an unwanted packet
    in line, 16
    jmp t, idle
skip_clear:                        ; avoid autopushing discarded ACK tail bits
    mov isr, null
    jmp skip
bus_reset:
    jmp pin DP, idle               ; timeout in K is not SE0
    flag set F_RESET
    wait 1 pin DM
    wait 0 flag F_RESET
    jmp idle
