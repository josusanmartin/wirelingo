; Logic-analyzer capture: up to 8 channels, P[IN_BASE] .. P[IN_BASE+7].
; Assemble with MASK = the channel mask (bit i = channel i, 1..0xFF); the host
; drains RXF. pe.la turns the records into edges, a VCD and an I2C decode.
;
; Each RX word is one record: [31:24] marker, [23:16] channel state (all 8
; channels as sampled; the host applies MASK), [15:0] R2, a tick counter that
; counts down from 0xFFFF while the WAIT waits (its timeout register):
;   0xF0  first record: the state when the capture started (counter: ignore)
;   0xFF  the masked state differs from the previous record's: a change; or it
;         doesn't and the counter is 0: idle, 65536 ticks without a change; or
;         it doesn't and the counter isn't 0: a pulse that was over a tick
;         after it was seen (on an unknown channel)
;   0xFC  gap: RXF was full and the core waited for the host (untimed); the
;         records after it are timed from an unknown instant
; Bit 31 is always set, so a word read from an empty RXF (0) is never a
; record: the host can drain with blind READ_FIXED bursts and drop zeros.
;
; Timing, in ticks (tick = CLKDIV clocks; pad inputs reach a core 2 clocks
; late, docs/isa.md §2, a constant offset). `wait pins != r0` completes on the
; first tick W at which a masked channel differs from the last record's state;
; with CLKDIV > 1 a difference between ticks is kept until the next tick.
; A = the WAIT's first attempt, k = 0xFFFF - R2: W = A + k.
;   first: the start sample is tick 0, the first WAIT attempt tick 7
;   after a record at W: the state is sampled at W + 1, the next WAIT starts
;   at W + 7
; So an edge at least 7 ticks after the previous record's W is timestamped
; exactly; one within the next tick is folded into that record (1 tick early);
; one 2-6 ticks after it is timestamped at W + 7 (up to 5 ticks late). Nothing
; is lost but pulses shorter than that. pe.la.decode_records applies these rules.
;
; Rate: a record per change, at most one per 7 ticks. RXF holds 8 records; past
; that the rate is what the host drains, and a gap word marks the wait.
;
; r0 = the last record's state (the WAIT's reference), r1 = 0xFF (the change
; marker), r2 = tick counter. R3 is unused.
.program la_capture
.shift_in left autopush 32
    mov r0, pins                ; tick 0: the start sample
    ldi r1, 0xF0
    in r1, 8                    ; marker 0xF0: first record
    in r0, 8
    in r2, 16                   ; (autopush)
    ldi r1, 0xFF                ; marker 0xFF from now on
    ldi r2, 0xFFFF
.wrap_target
loop:
    wait pins != r0 mask MASK timeout r2    ; W (A = the first attempt)
    mov r0, pins                ; W+1: the new state
    in r1, 8                    ; W+2: marker
    in r0, 8                    ; W+3: state
    jmp rxf, full               ; W+4
    in r2, 16                   ; W+5: counter; autopush (RXF has room)
    ldi r2, 0xFFFF              ; W+6
.wrap                           ; next WAIT at W+7
full:                           ; RXF full: wait for the host, then a gap word
    in r2, 16                   ; autopush waits for room (untimed)
    ldi r2, 0xFC00
    in r2, 16
    in null, 16                 ; 0xFC000000 (autopush, may wait too)
    ldi r2, 0xFFFF
    jmp loop
