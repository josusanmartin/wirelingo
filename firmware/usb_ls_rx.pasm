; USB low-speed receiver (1.5 Mbit/s) at clk = 48 MHz, CLKDIV = 1: 32 ticks per bit.
;
; NRZI decoding uses WAIT's edge detection with a timeout:
;   * an edge on D- within the window  -> a 0 bit (and resynchronises the bit clock)
;   * no edge shortly after the expected boundary -> a 1 bit
; Stuffed bits (a 0 after six 1s) are dropped; SE0 (both lines low) ends the packet.
;
; Received after the PID: tokens (always) and DATA packets that follow a SETUP
; or OUT to us (F_EXP). Everything else (handshakes, our own transmissions,
; unexpected DATA) is skipped to its end without touching the FIFO.
;
; Tokens are filtered: the 16 bits after the PID (address, endpoint, CRC5) must
; equal the immediate at label `filter` (bit-reversed, see pe.usb.token_filter).
; The host software writes that word (address 0 at first, the new address
; after SET_ADDRESS), so the device ignores other addresses, other endpoints
; and tokens with a bad CRC5. Then:
;   IN          -> set F_IN (usb_ls_tx sends the staged packet or NAK)
;   SETUP / OUT -> set F_EXP (a DATA packet follows); SETUP also sets F_SETUP
; An ACK from the host (after our DATA packet) sets F_GOTACK.
;
; DATA records (RX FIFO, ISR shifts right, autopush 24): every DATA packet
; received while F_EXP is set gives one record, CRC good or bad:
;   payload + CRC16 bits, 24 per word in bits [31:8] (low byte 0), the first
;   received bit lowest; then the partial last word (its bits at the top; an
;   all-zero word if none); then a trailer: [31:16] = bits received including
;   SYNC and PID, [7] = 1 (marks the trailer: payload words have [7:0] = 0),
;   [15:8] = reversed PID, [1] = SETUP association, [0] = CRC/PID good and ACKed.
; F_SETUP marks a SETUP's data stage (firmware clears it after recording it).
; The largest record
; (8 data bytes) is 5 words, so the host must keep 5 entries free.
;
; Timing: D is the tick a WAIT completes. The next WAIT's first attempt is at
; D+w, and its timeout N = target - w. The "no edge" decision is taken 10 ticks
; after the expected cell boundary (target 42 after an edge, then every 32),
; not mid-cell: after six 1s at the +-1.5% clock tolerance the boundaries drift
; by ~3 ticks, and the next WAIT must be armed before the following boundary,
; so 10 leaves ~5 ticks of margin on both sides with this code's overhead.
; Paths of different length load different budgets so the decision time is the
; same whichever branch is taken (the once-per-packet PID grab takes 3 ticks more).
;
;
; After 16 bits, r3 = bit-reverse of the ISR, i.e. r3[15:8] = rev(SYNC) = 0x01 and
; r3[7:0] = rev(PID). Token PIDs end in binary 01, so a token has
; r3 & 0xC0 == 0x80. At the end of a DATA packet while F_EXP is set: clear
; F_EXP, and set F_ACK if the CRC16 checks out. The CRC tap (CRCCTRL: poly
; 0x8005, 16 bits, IN_TAP) sees every received bit; it is preset after the PID
; so that at EOP it holds the residue 0x800D for a correct packet.
;
; Registers: r0 = bit count, r1 = ones remaining (6..0), r2 = timeout budget, r3 = PID.
; OSR = all ones.
.program usb_ls_rx
.shift_in right autopush 24
start:
    mov osr, ~null                 ; source of 1 bits for "in osr, 1"
idle:
    ldi r0, 0
    ldi r1, 6                     ; ones remaining before a mandatory stuffed zero
    ldi r2, 120                    ; sustained SE0 (2.5 us at 48 MHz) is reset
    wait 1 pin DM timeout r2       ; normal EOP is only 64 ticks
    jmp t, bus_reset
    wait fall DM                   ; J -> K: first transition of SYNC (a 0 bit)
    in null, 1                     ; D+1
    add r0, 1                      ; D+2
    ldi r2, 37                     ; D+3   (W = D+5, target 42)
    jmp wait_bit                   ; D+4
; -- after SYNC + PID, ending in a 1 bit. The tightest path: a 2.5 % fast host
; needs the next WAIT armed by D+18 (one tick later loses edges), so this falls
; into wait_bit. Expected DATA (F_EXP) presets the CRC; a token doesn't need it.
o_grab:
    mov r3, ::isr                  ; D+11  SYNC + PID
    mov isr, null                  ; D+12  drop SYNC + PID from the output
    jmp f F_EXP, o_data            ; D+13
    cmp r3, 0x187                  ; D+14  OUT is the only supported token ending in 1
    jmp !z, rx_skip                ; D+15  ACK and unexpected DATA are skipped
    ldi r2, 15                     ; D+16  (W = D+17, target 32)
wait_bit:
    wait edge DM timeout r2
    jmp t, one                     ; D+1
; -- edge on D-: J (0 bit), K (0 bit) or SE0 (end of packet)
    jmp pin DM, zero_j             ; D+2
    jmp !pin DP, eop               ; D+3   D- low and D+ low: SE0
    jmp zero                       ; D+4
zero_j:
    nop                            ; D+3
    nop                            ; D+4
zero:
    cmp r1, 0                      ; D+5
    ldi r1, 6                      ; D+6
    jmp z, stuffed                 ; D+7
    in null, 1                     ; D+8
    add r0, 1                      ; D+9
    cmp r0, 16                     ; D+10
    jmp z, z_grab                  ; D+11
    ldi r2, 28                     ; D+12  (W = D+14, target 42)
    jmp wait_bit                   ; D+13
z_grab:                            ; once per packet, after SYNC + PID
    mov r3, ::isr                  ; D+12  SYNC + PID
    mov isr, null                  ; D+13  drop SYNC + PID from the output
    jmp f F_EXP, z_data            ; D+14
    mov r2, r3                     ; D+15
    and r2, 0x40                   ; D+16  0: a token
    jmp r2, rx_skip                ; D+17
    ldi r2, 22                     ; D+18  (W = D+20, same decision time)
    jmp wait_bit                   ; D+19
z_data:
    mov crc, ~null                 ; D+15  CRC16 covers what follows the PID
    ldi r2, 24                     ; D+16  (W = D+18)
    jmp wait_bit                   ; D+17
stuffed:
    ldi r2, 32                     ; D+8   (W = D+10, target 42)
    jmp wait_bit                   ; D+9
; -- no edge by the decision point: a 1 bit, unless the line went SE0 (K -> SE0 moves only D+)
one:
    jmp pin DP, one_k              ; D+2   D+ high: K
    jmp !pin DM, eop               ; D+3   D+ low and D- low: SE0
    jmp one_ok                     ; D+4
one_k:
    nop                            ; D+3
    nop                            ; D+4
one_ok:
    sub r1, 1                      ; D+5  seventh one borrows (C), so abort the packet
    jmp c, stuff_error             ; D+6
    in osr, 1                      ; D+7
    add r0, 1                      ; D+8
    cmp r0, 16                     ; D+9
    jmp z, o_grab                  ; D+10
    ldi r2, 19                     ; D+11  (W = D+13, target 32)
    jmp wait_bit                   ; D+12
o_data:
    mov crc, ~null                 ; D+14  CRC16 covers what follows the PID
    ldi r2, 15                     ; D+15  (W = D+17)
    jmp wait_bit                   ; D+16
; -- handshakes, our own transmissions, unexpected DATA: nothing to receive,
; just find the end of the packet. One qualified WAIT detects SE0 (D+ low while
; D- is low); it must still be there 8 ticks later, which rejects glitches
; during J/K edges.
rx_skip:
    wait 0 pin DP if pin DM == 0
    nop                       [7]
    jmp pin DP, rx_skip
    jmp pin DM, rx_skip
    cmp r3, 0x14B                  ; SYNC + ACK: the host took our DATA packet
    jmp !z, eop_skip
    flag set F_GOTACK
    jmp eop_skip
; -- end of packet: raise the response flags first (the TX core must answer
; within the bus turnaround), then push the tail words (may wait for the host)
eop:
    mov r2, r3
    and r2, 0x40
    jmp r2, eop_data               ; not a token
    cmp r0, 32
    jmp !z, eop_skip               ; malformed token
    mov r1, ::isr                  ; address, endpoint, CRC5 (bit-reversed)
filter:
    ldi r2, TOKEN_FILTER           ; written by the host software
    cmp r1, r2
    jmp !z, eop_skip               ; another address or endpoint, or a bad CRC5
    cmp r3, 0x196                  ; SYNC + IN
    jmp z, got_in
    cmp r3, 0x1B4                  ; SYNC + SETUP
    jmp z, got_setup
    cmp r3, 0x187                  ; SYNC + OUT
    jmp z, got_out
    jmp eop_skip                   ; another token (SOF, PING)
eop_data:                          ; DATA or handshake
    cmp r0, 16
    jmp z, eop_skip                ; handshake
    jmp !f F_EXP, eop_skip         ; not after SETUP/OUT
    flag clr F_EXP
    cmp r3, 0x1C3                  ; exact SYNC + DATA0 (including PID complement)
    jmp z, data_pid_ok
    jmp f F_SETUP, data_bad_pid    ; SETUP data must use DATA0
    cmp r3, 0x1D2                  ; exact SYNC + DATA1
    jmp z, data_pid_ok
data_bad_pid:
    ldi r1, 0x80                   ; invalid PID: record failure, never ACK
    jmp trailer
data_pid_ok:
    ldi r2, 0x800D                 ; CRC16 residue of a correct packet
    mov r1, crc
    cmp r1, r2
    ldi r1, 0x80                   ; trailer status: CRC error (no ACK; the host retries)
    jmp !z, trailer
    flag set F_ACK
    ldi r1, 0x81                   ; trailer status: good
trailer:
    jmp !f F_SETUP, trailer_out
    or r1, 2                     ; record carries SETUP association, not a live flag
    flag clr F_SETUP
trailer_out:
    mov r2, r3
    and r2, 0xFF
    shl r2, 8                     ; trailer [15:8] = reversed PID
    or r1, r2
    push block                     ; partial last word (bits at the top)
    mov isr, null
    in r1, 16                      ; [15:0] = status
    in r0, 16                      ; [31:16] = bit count; ISR_CNT = 32: autopushed
    jmp idle
got_in:
    flag set F_IN
    jmp eop_skip
got_setup:
    flag clr F_STAGED              ; abort the preceding transfer immediately
    flag clr F_GOTACK
    flag set F_SETUP
    jmp expect_data
got_out:
    flag clr F_SETUP
expect_data:
    flag set F_EXP
eop_skip:
    mov isr, null
    jmp idle
bus_reset:
    jmp pin DP, idle              ; timeout in K is not SE0
    flag set F_RESET
    wait 1 pin DM
    wait 0 flag F_RESET            ; software restores filter, FIFOs and transfer state
    jmp idle
stuff_error:
    ldi r3, 0                     ; invalid PID marks any partial DATA record bad
stuff_error_wait:
    wait 0 pin DP if pin DM == 0
    nop                       [7]
    jmp pin DP, stuff_error_wait
    jmp pin DM, stuff_error_wait
    jmp !f F_EXP, eop_skip
    jmp eop_data
