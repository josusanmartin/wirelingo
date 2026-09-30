; USB low-speed receiver (1.5 Mbit/s) at clk = 48 MHz, CLKDIV = 1: 32 ticks per bit.
;
; NRZI decoding uses WAIT's edge detection with a timeout:
;   * an edge on D- within the window  -> a 0 bit (and resynchronises the bit clock)
;   * no edge shortly after the expected boundary -> a 1 bit
; Stuffed bits (a 0 after six 1s) are dropped; SE0 (both lines low) ends the packet.
;
; Output (RX FIFO, ISR shifts right, autopush 32): only DATA packets that follow
; a SETUP/OUT token are pushed, without SYNC and PID: the payload + CRC16 bytes
; packed little-endian 4 per word, then the partial last word, then a word with
; the number of bits received including SYNC and PID (so payload+CRC bits =
; count - 16). An 8-byte SETUP payload is exactly 4 words, which fits the FIFO.
; Tokens and handshakes only raise flags; F_SETUP marks that the pushed packet
; was a SETUP's data stage (the host clears it). Once the PID is in (bit 16),
; anything other than an expected DATA packet is skipped to its end, so nothing
; of it (not even autopushed words) reaches the FIFO.
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
; Token handling (so the TX core can answer within the bus turnaround time):
; after 16 bits, r3 = bit-reverse of the ISR, i.e. r3[15:8] = rev(SYNC) = 0x01 and
; r3[7:0] = rev(PID). At the end of a 32-bit packet:
;   IN          -> set F_IN (usb_ls_tx sends the staged packet or NAK)
;   SETUP / OUT -> set F_EXP (a DATA packet follows)
; At the end of a DATA packet while F_EXP is set -> clear F_EXP, and set F_ACK
; if the CRC16 checks out. The CRC tap (CRCCTRL: poly 0x8005, 16 bits, IN_TAP)
; sees every received bit; it is preset after the PID so that at EOP it holds
; the residue 0x800D for a correct packet.
;
; Registers: r0 = bit count, r1 = consecutive 1s, r2 = timeout budget, r3 = PID.
; OSR = all ones.
.program usb_ls_rx
.shift_in right autopush 32
start:
    mov osr, ~null                 ; source of 1 bits for "in osr, 1"
idle:
    ldi r0, 0
    ldi r1, 0
    wait 1 pin DM                  ; bus idle (J)
    wait fall DM                   ; J -> K: first transition of SYNC (a 0 bit)
    in null, 1                     ; D+1
    add r0, 1                      ; D+2
    ldi r2, 37                     ; D+3   (W = D+5, target 42)
    jmp wait_bit                   ; D+4
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
    cmp r1, 6                      ; D+5
    ldi r1, 0                      ; D+6
    jmp z, stuffed                 ; D+7
    in null, 1                     ; D+8
    add r0, 1                      ; D+9
    cmp r0, 16                     ; D+10
    jmp z, z_grab                  ; D+11
    ldi r2, 28                     ; D+12  (W = D+14, target 42)
    jmp wait_bit                   ; D+13
z_grab:                            ; once per packet, after SYNC + PID
    mov r3, ::isr                  ; D+12  SYNC + PID
    mov crc, ~null                 ; D+13  CRC16 covers what follows the PID
    mov isr, null                  ; D+14  drop SYNC + PID from the output
    jmp !f F_EXP, rx_skip          ; D+15  not an expected DATA packet
    ldi r2, 24                     ; D+16  (W = D+18, same decision time)
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
    add r1, 1                      ; D+5
    in osr, 1                      ; D+6
    add r0, 1                      ; D+7
    cmp r0, 16                     ; D+8
    jmp z, o_grab                  ; D+9
    ldi r2, 20                     ; D+10  (W = D+12, target 32)
    jmp wait_bit                   ; D+11
o_grab:
    mov r3, ::isr                  ; D+10
    mov crc, ~null                 ; D+11
    mov isr, null                  ; D+12
    jmp !f F_EXP, rx_skip          ; D+13
    ldi r2, 16                     ; D+14  (W = D+16, same decision time)
    jmp wait_bit                   ; D+15
; -- tokens, handshakes and our own transmissions: nothing to receive, just find
; the end of the packet. One qualified WAIT detects SE0 (D+ low while D- is low);
; it must still be there 8 ticks later, which rejects glitches during J/K edges.
rx_skip:
    wait 0 pin DP if pin DM == 0
    nop                       [7]
    jmp pin DP, rx_skip
    jmp pin DM, rx_skip
    cmp r3, 0x196                  ; SYNC + IN
    jmp z, got_in
    cmp r3, 0x1B4                  ; SYNC + SETUP
    jmp z, got_setup
    cmp r3, 0x187                  ; SYNC + OUT
    jmp z, got_out
    jmp eop_skip
; -- end of packet: raise the response flags first (the TX core must answer
; within the bus turnaround), then push the tail words (may wait for the host)
eop:
    cmp r0, 32
    jmp !z, eop_data
    cmp r3, 0x196                  ; SYNC + IN
    jmp z, got_in
    cmp r3, 0x1B4                  ; SYNC + SETUP
    jmp z, got_setup
    cmp r3, 0x187                  ; SYNC + OUT
    jmp z, got_out
eop_data:                          ; DATA (a zero-length DATA is also 32 bits) or handshake
    cmp r0, 16
    jmp z, eop_skip                ; handshake
    jmp !f F_EXP, eop_skip         ; not after SETUP/OUT (e.g. our own IN response)
    flag clr F_EXP
    ldi r2, 0x800D                 ; CRC16 residue of a correct packet
    mov r1, crc
    cmp r1, r2
    jmp !z, eop_skip               ; bad CRC: stay silent, the host retries
    flag set F_ACK
    push block                     ; partial last word (bytes in the top bits)
    mov isr, r0
    push block                     ; bit count
    jmp idle
got_in:
    flag set F_IN
    jmp eop_skip
got_setup:
    flag set F_SETUP
got_out:
    flag set F_EXP
eop_skip:
    mov isr, null
    jmp idle
