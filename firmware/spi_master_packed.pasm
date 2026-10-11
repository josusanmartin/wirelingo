; Exact-length, MSB-first SPI packets on core0. Assemble CPOL=mode>>1,
; WORD_BITS=32 (packed), CS=10; select program mode&1. Threshold8 is the
; byte-per-word traffic control with the identical CS/header instructions.
; Own MOSI8/SCK9/CS10; input MISO16; no flags or second core required.
; One TX header: high16=8*N-1, low16=N modulo (WORD_BITS/8).
; Public helper accepts N=1..8192 bytes. Raw malformed headers are outside
; that contract: no hardware validation/abort or timeout is implemented.
; Header plus ceil(N/4) payload words: only28B payload fully preloads depth8.
; TX words are high-aligned; final unused bytes are ignored on wire.
; RX full words are MSB-first, partial final word is low-aligned. There is
; no RX metadata, dummy clock or extra aligned PUSH. Request length frames RX.
; Reusable program: CS remains high until both header and first payload arrive.
; TX waits idle, RX waits at sample phase (active CPHA0, idle CPHA1), CS low.
; Peers must tolerate these intra-CS pauses; no indefinite-host guarantee.
; Synchronizers enabled, integer divider>=3, tested launch delays1/2 clocks.
; CPHA0 body4 ticks/bit. CPHA1 body5: idle PULL guards the active OUT against
; starvation, OUT delay1 allows synchronized MISO to settle before IN.
; MOV OSR,OSR in the header resets its shift counter: threshold8 must not
; accidentally autopull a payload during the second 16-bit header field.

.program spi_packed_cpha0
.side_set 1
.shift_out left autopull WORD_BITS
.shift_in left autopush WORD_BITS
.wrap_target
frame:
    pull block             side CPOL
    out r0, 16             side CPOL
    mov osr, osr           side CPOL
    out r1, 16             side CPOL
first_payload:
    pull block             side CPOL
    mov isr, null          side CPOL
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
    jmp frame              side CPOL
.wrap

.program spi_packed_cpha1
.side_set 1
.shift_out left autopull WORD_BITS
.shift_in left autopush WORD_BITS
.wrap_target
frame:
    pull block             side CPOL
    out r0, 16             side CPOL
    mov osr, osr           side CPOL
    out r1, 16             side CPOL
first_payload:
    pull block             side CPOL
    mov isr, null          side CPOL
    pin CS, lo             side CPOL
bit:
    pull ifempty block     side CPOL
    out pins, 1            side (1-CPOL) [1]
sample:
    in pins, 1             side CPOL
    jmp r0--, bit          side CPOL
    jmp !r1, finish        side CPOL
tail:
    push block             side CPOL
finish:
    nop                    side CPOL
    pin CS, hi             side CPOL
    jmp frame              side CPOL
.wrap
