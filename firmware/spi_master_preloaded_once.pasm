; Headerless one-shot mode0 SPI, core0: caller writes R0=8*N-1, R1=N%4
; while disabled/empty, then preloads ceil(N/4) high-aligned payload words.
; 1..32 bytes fit depth8 with no FIFO header; partial RX is low-aligned.
; Restart retains R0/R1 but clears OSR/ISR counters; HALT follows CS release.
; Startup ISR-clear delay adds 2 ticks without a word: at div3 it prevents
; CS asserting during the trailing host start call (SCK8, setup/hold4/gap8).
; Native regression covers mode0/div3/sync/peer-launch2 and two paid rearmed32B
; frames. Other host speeds need their own startup/hold/wait qualification.
; Final CS hold is 1 tick (3 clocks at div3), not Atlas's4-clock requirement.
; Caller must wait/drain/check HALT/no fault/empty STATUS/idle POUT before
; disabling and rearming; never silently clear unexpected FIFO contents.
.program spi_preloaded_once
.side_set 1
.shift_out left autopull WORD_BITS
.shift_in left autopush WORD_BITS
.wrap_target
frame:
first_payload:
    pull block             side CPOL
    mov isr, null          side CPOL [2]
    pin CS, lo             side CPOL
bit:
    out pins, 1            side CPOL [1]
sample:
    in pins, 1             side (1-CPOL)
    jmp r0--, bit          side (1-CPOL)
    jmp !r1, finish        side (1-CPOL)
tail:
    push block             side (1-CPOL)
finish:
    nop                    side CPOL
    pin CS, hi             side CPOL
    halt                   side CPOL
.wrap

