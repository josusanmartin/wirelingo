; SWD (ARM Serial Wire Debug) probe engine. SWCLK = side-set pin; SWDIO = OUT, SET
; and IN pin. The host composes SWD packets out of two primitives (like
; raspberrypi/debugprobe does with PIO):
;
;   TX word = [8:0] n-1 (1..512 bits), [9] 1 = read
;   write:  followed by ceil(n/32) data words, LSB first. The probe drives SWDIO,
;           changing it with the falling SWCLK edge; the target samples on the
;           rising edge. A 0 word is pushed when done.
;   read:   SWDIO is released (so a read also provides SWD's turnaround cycles);
;           n bits are sampled just before each rising edge, LSB first, and pushed
;           in 32-bit words, the last one right-aligned.
;
; One SWCLK period = 4 ticks, idling low. CLKDIV 3 at 48 MHz gives 4 MHz SWCLK.
.program swd_probe
.side_set 1 opt
.shift_out right autopull 32
.shift_in right autopush 32
top:
    pull block                side 0
    out r0, 9                         ; n - 1
    out r1, 1                         ; direction
    out null, 22                      ; empty the OSR: data words autopull
    jmp r1, read
    mov pindirs, ~null                ; drive SWDIO
wbit:
    out pins, 1               side 0 [1]  ; falling edge: next bit
    jmp r0--, wbit            side 1 [1]  ; rising edge: the target samples
    set pins, 1               side 0      ; leave SWDIO high
    in null, 32                       ; completion word
    jmp top
read:
    mov pindirs, null                 ; release SWDIO
    mov r2, r0
rbit:
    nop                       side 0 [1]
    in pins, 1                side 1      ; sample (the pin value before this edge)
    jmp r0--, rbit
    and r2, 31                        ; right-align a partial last word:
    xor r2, 31                        ;   31 - ((n-1) mod 32) more zero bits
align:
    jmp r2--, pad
    jmp top
pad:
    in null, 1
    jmp align
