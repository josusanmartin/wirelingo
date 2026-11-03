; 10BASE-T transmitter with carrier sense: firmware/eth10_tx.pasm, same waveform
; and same host protocol (read its header), plus two things a half-duplex
; station needs.
;
; 1. It does not transmit into an arriving frame. eth10_tx starts as soon as the
;    host sets F_GO, and the host's look at the line is stale by then: an SPI
;    read, an SPI write and the idle loop's 1.6 us. Here the decision is the
;    core's own: with F_GO set it waits for RXP (the receive comparator, the
;    pin eth10_rx hears on) to stay without a transition for 96 bit times, 802.3's
;    inter-frame gap, and starts the preamble about 3 ticks later. The blind time
;    is the pad's synchronizer and those ticks (docs/eth10-network.md measures it).
;    Any core can read any pad, so this takes no help from core 0.
;
;    The window also covers the chip's own frames: F_GO can't be set again until
;    a frame has finished, so two of them are 96 bit times apart at least.
;
;    F_GO stays set for as long as the line stays busy, and no link pulses go
;    out meanwhile: the host decides how long to wait. To give up, restart this
;    core with its FIFOs cleared and clear F_GO (pe.ethhost: abort_tx).
;
; 2. After a frame the link-pulse timer starts again, so the next pulse comes
;    16 ms after the frame. eth10_tx leaves the count where it was, so its next
;    pulse can follow a frame by a few microseconds.
;
; 40 ticks per 1 us: clk = 40 MHz with CLKDIV 1, or 50 MHz with CLKDIV 1.25.
; RXP is a logical pin number (ui0 = P16) and is a define, like F_GO.
.program eth10_tx_cs
.side_set 2
.shift_out right autopull 32
.fifo join_tx
.define IFG_TICKS 96 * 4
idle:
    nop                       side 1 [3]   ; link pulse: 100 ns H
    ldi r1, 9999              side 0
idle_wait:                                 ; 10000 x 64 ticks = 16 ms
    jmp f F_GO, defer         side 0 [31]
    jmp r1--, idle_wait       side 0 [31]
    jmp idle                  side 0
defer:
    ldi r2, IFG_TICKS - 1     side 0
    wait edge RXP timeout r2  side 0       ; IFG_TICKS ticks, or the first transition
    jmp t, frame              side 0       ; quiet all the way
    jmp defer                 side 0       ; carrier: start the window again
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
    ldi r1, 9999              side 0       ; the next link pulse is 16 ms away
    jmp !osre, flush          side 0       ; drop the rest of a part-used word
    jmp idle_wait             side 0
flush:
    out null, 32              side 0
    jmp idle_wait             side 0
