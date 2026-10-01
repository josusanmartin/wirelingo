; 10BASE-T transmitter (IEEE 802.3 clause 14): Manchester, preamble + SFD,
; TP_IDL, and normal link pulses (NLP) while idle.
; clk = 40 MHz, CLKDIV = 1: 4 ticks per bit, so every half bit is exactly 2 ticks.
; Side-set pins (2): SIDE_BASE = TD+, SIDE_BASE+1 = TD-. side 1 = H (TD+ high),
; side 2 = L (TD- high), side 0 = idle (no differential voltage). Drive the
; isolation transformer through series resistors (the usual TD+/TD- network).
;
; Manchester (802.3): first half = complement of the bit, second half = the bit,
; so a 1 is L then H and a 0 is H then L.
;
; Host protocol (FIFO join: TXF + RXF = 2*D = 16 words of TX buffering):
;   1. write R0 = number of frame bits - 1 (frame = destination MAC .. FCS,
;      so 8 * bytes - 1; the host computes the FCS);
;   2. push the frame bytes little-endian, 4 per word (bytes go out LSB first);
;   3. set flag F_GO. The core sends preamble + SFD + frame and clears F_GO when
;      the line is idle again. A 64-byte (minimum) frame fits in the FIFOs; a
;      longer one needs the host to keep pushing (one word per 3.2 us).
;
; Each data bit is one 4-instruction block (`zero` or `one`): check for the
; last bit, count, fetch the next bit, branch. The wrap closes the loop for free.
.program eth10_tx
.side_set 2
.shift_out right autopull 32
.fifo join_tx
idle:
    nop                       side 1 [3]   ; link pulse: 100 ns H
    ldi r1, 9999              side 0
idle_wait:                                 ; 10000 x 64 ticks = 16 ms
    jmp f F_GO, frame         side 0 [31]
    jmp r1--, idle_wait       side 0 [31]
    jmp idle                  side 0
frame:
    ldi r2, 30                side 0       ; 31 x "10", then "11" (7 x 0x55, 0xD5, LSB first)
pre:
    nop                       side 2 [1]   ; 1
    nop                       side 1 [1]
    nop                       side 1 [1]   ; 0
    jmp r2--, pre             side 2 [1]
    nop                       side 2 [1]   ; 1
    nop                       side 1 [1]
    nop                       side 2 [1]   ; 1 (last SFD bit)
    out r3, 1                 side 1       ;   first frame bit (autopull)
    jmp r3, one               side 1       ;   falls into `zero` for a 0
.wrap_target
zero:
    jmp !r0, zlast            side 1       ; H  (r0 = 0: this is the last bit)
    sub r0, 1                 side 1       ; H
    out r3, 1                 side 2       ; L  next bit
    jmp !r3, zero             side 2       ; L
one:
    jmp !r0, olast            side 2       ; L
    sub r0, 1                 side 2       ; L
    out r3, 1                 side 1       ; H
    jmp r3, one               side 1       ; H  (a 0 wraps to `zero`)
.wrap
zlast:
    nop                       side 1       ; H
    nop                       side 2 [1]   ; L
    jmp tp_idl                side 1       ; TP_IDL: 275 ns H after a 0
olast:
    nop                       side 2       ; L
    nop                       side 1 [1]   ; H  (TP_IDL: 300 ns H after a 1)
tp_idl:
    nop                       side 1 [9]
    flag clr F_GO             side 0
    jmp !osre, flush          side 0       ; drop the rest of a part-used word
    jmp idle_wait             side 0
flush:
    out null, 32              side 0
    jmp idle_wait             side 0
