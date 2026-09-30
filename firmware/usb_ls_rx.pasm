; USB low-speed receiver (1.5 Mbit/s) at clk = 48 MHz, CLKDIV = 1: 32 ticks per bit.
;
; NRZI decoding uses WAIT's edge detection with a timeout:
;   * an edge on D- within the window  -> a 0 bit (and resynchronises the bit clock)
;   * no edge shortly after the expected boundary -> a 1 bit
; Stuffed bits (a 0 after six 1s) are dropped; SE0 (both lines low) ends the packet.
;
; Output (RX FIFO, ISR shifts right, autopush 32): packet bytes packed
; little-endian 4 per word (SYNC byte first), then the partial last word, then
; a word with the number of bits received (excluding stuffed bits).
;
; Timing: D is the tick a WAIT completes. The next WAIT's first attempt is at
; D+w, and its timeout N = target - w. The "no edge" decision is taken 10 ticks
; after the expected cell boundary (target 42 after an edge, then every 32),
; not mid-cell: after six 1s at the +-1.5% clock tolerance the boundaries drift
; by ~3 ticks, and the next WAIT must be armed before the following boundary,
; so 10 leaves ~5 ticks of margin on both sides with this code's overhead.
; Every path is padded so w is the same whichever branch is taken.
;
;
; Token handling (so the TX core can answer within the bus turnaround time):
; after 16 bits, r3 = bit-reverse of the ISR, i.e. r3[15:8] = rev(SYNC) = 0x01 and
; r3[7:0] = rev(PID). At the end of a 32-bit packet:
;   IN          -> set F_IN (usb_ls_tx sends the staged packet or NAK)
;   SETUP / OUT -> set F_EXP (a DATA packet follows)
; At the end of a DATA packet while F_EXP is set -> clear F_EXP, set F_ACK.
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
    jmp !z, z_nograb               ; D+11
    mov r3, ::isr                  ; D+12
    jmp z_cont                     ; D+13
z_nograb:
    nop                       [1]  ; D+12..13
z_cont:
    ldi r2, 26                     ; D+14  (W = D+16, target 42)
    jmp wait_bit                   ; D+15
stuffed:
    nop                       [5]  ; D+8..13
    ldi r2, 26                     ; D+14
    jmp wait_bit                   ; D+15
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
    jmp !z, o_nograb               ; D+9
    mov r3, ::isr                  ; D+10
    jmp o_cont                     ; D+11
o_nograb:
    nop                       [1]  ; D+10..11
o_cont:
    ldi r2, 18                     ; D+12  (W = D+14, target 32)
    jmp wait_bit                   ; D+13
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
    jmp z, got_setup
eop_data:                          ; DATA packets (a zero-length DATA is also 32 bits)
    jmp !f F_EXP, eop_push
    flag clr F_EXP
    flag set F_ACK
    jmp eop_push
got_in:
    flag set F_IN
    jmp eop_push
got_setup:
    flag set F_EXP
eop_push:
    push block                     ; partial last word (bytes in the top bits)
    mov isr, r0
    push block                     ; bit count
    jmp idle
