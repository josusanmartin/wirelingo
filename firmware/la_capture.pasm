; Logic-analyzer capture: up to 8 channels, P[IN_BASE] .. P[IN_BASE+7].
; The host preloads R3 with the channel mask (bit i = channel i, at most 0xFF;
; registers survive a restart) and drains RXF. pe.la turns the records into
; edges, a VCD and an I2C decode.
;
; Each RX word is one record: [31:24] marker, [23:16] channel state (masked),
; [15:0] R2, the sample counter (counts down from 0xFFFF):
;   0xF0  first record: the state when the capture started
;   0xFF  change: the state differs from the previous record's
;   0xFE  idle: 65536 samples without a change (state unchanged)
;   0xFC / 0xF8  change / idle that follows a gap (below)
; Bit 31 is always set, so a word read from an empty RXF (0) is never a
; record: the host can drain with blind READ_FIXED bursts and drop zeros.
;
; Timing, in ticks (tick = CLKDIV clocks; CLKDIV 1 = one sample per 5 clocks):
; the loop samples the pins every 5 ticks (`mov r0, pins`; pad inputs reach a
; core 2 clocks late, docs/isa.md §2, a constant offset). B = the first sample
; after a record, k = 0xFFFF - R2 the samples taken since:
;   change at B + 5k; the next sample is 12 ticks after the change sample
;   idle:  the next sample is at B + 5*65535 + 14
;   first: the start sample is tick 1 after the restart, the next is tick 13
; So an edge is timestamped to within 5 ticks; changes closer together than
; 12 ticks fold into one record (several channels change at once), and a pulse
; shorter than 5 ticks can be missed. pe.la.decode_records applies these rules.
;
; Rate: a record per change, at most one per 12 ticks. RXF holds 8 records; past
; that the rate is what the host drains. If RXF is full the core waits for the
; host (`push block`): that time is not observed, and the next record's marker
; says so (0xFC/0xF8: the interval before it is a lower bound and edges may be
; missing).
;
; r0 = sample, r1 = state of the last record, r2 = sample counter, r3 = mask.
; The ISR holds the next record's marker byte (its low 8 bits are shifted to
; [31:24] by the two INs).
.program la_capture
.shift_in left
    mov isr, ~null              ; tick 0: marker 0xFF...
    mov r0, pins                ; tick 1: the start sample
    and r0, r3
    in null, 4                  ; marker 0xF0: first record
    jmp change                  ; same path as a change: next sample at tick 13
poll:
    mov r0, pins                ; S
    and r0, r3                  ; S+1
    cmp r0, r1                  ; S+2
    jmp !z, change              ; S+3
    jmp r2--, poll              ; S+4: 65536 samples per R2 round
    in null, 1                  ; idle: marker bit 0 = 0
change:
    jmp rxf, full               ; S+4 (idle: S+6)
    in r0, 8                    ; S+5
    in r2, 16                   ; S+6
    push                        ; S+7
    mov isr, ~null              ; S+8: next marker 0xFF
next:
    mov r1, r0                  ; S+9
    ldi r2, 0xFFFF              ; S+10
    jmp poll                    ; S+11, next sample S+12 (idle: S+14)
full:                           ; RXF full: wait for the host, flag the gap
    in r0, 8
    in r2, 16
    push block
    mov isr, ~null
    in null, 2                  ; next marker 0xFC: follows a gap
    jmp next
