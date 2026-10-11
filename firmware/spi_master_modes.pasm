; SPI controller, modes 0..3, MSB first, 8-bit full-duplex frames.
; Assemble BOTH blocks with CPOL = mode >> 1; select block mode & 1:
;   asm.assemble_all(source, {"CPOL": mode >> 1})[mode & 1]
; Example mode 3 MicroPython export (core must be disabled before pe.load):
;   PYTHONPATH=tools python -m pe.export firmware/spi_master_modes.pasm \
;     --program spi_master_cpha1 --define CPOL=1 --core 0 \
;     --out-pins 8,1 --side-pins 9 --in-pins 16 --clkdiv 4
; SCK = side-set pin, MOSI = OUT pin (count 1), MISO = IN pin.
; Push TX bytes in [31:24]; RX bytes appear in [7:0].
;
; Set SCK's PIN_OUT to CPOL before starting (and OE for bidirectional pins).
; CS belongs to the host or a wrapper; assert it after establishing idle SCK.
; Explicit PULL at each frame boundary keeps SCK idle on TX starvation,
; including CPHA1: no leading edge occurs until a complete byte is available.
; A full RX FIFO stretches the sample phase until the host drains it; use
; with peers that tolerate such pauses, or keep RX drained during a frame.
;
; Compact programs use 6 words/24 bytes and 4 ticks per bit, plus two idle
; setup ticks per byte (PULL, LDI): 34 ticks/byte without FIFO pauses.
; CPHA0: active 2, idle 2 ticks; CPHA1: active 1, idle 3 ticks.
; Optional *_unrolled_* blocks use 32 words/128 bytes: 4 ticks/bit and
; 32 ticks/byte. The next PULL replaces the final idle instruction, so
; there are no extra byte ticks. This trades 104 program bytes for 2 ticks.
; No continuous-clock guarantee or external maximum frequency is claimed.
; Use integer CLKDIV >= 3, input synchronization enabled. The tests cover
; a digital peer launching MISO one/two clocks after its launch edge; board and
; pad delay budgets still require separate validation. spi_master_fast is
; the separate mode-0 program for the existing synchronizer-bypass study.

.program spi_master_cpha0
.side_set 1
.shift_out left
.shift_in left autopush 8
.wrap_target
frame:
    pull block      side CPOL
    ldi r0, 7       side CPOL
bit:
    out pins, 1     side CPOL           ; data before the leading edge
    nop             side (1-CPOL)      ; leading edge: peer samples MOSI
sample:
    in pins, 1      side (1-CPOL)      ; synchronized MISO from leading edge
    jmp r0--, bit   side CPOL          ; trailing edge, then next bit/frame
.wrap

.program spi_master_cpha1
.side_set 1
.shift_out left
.shift_in left autopush 8
.wrap_target
frame:
    pull block      side CPOL
    ldi r0, 7       side CPOL
bit:
    out pins, 1     side (1-CPOL)      ; leading edge: launch MOSI/MISO
    nop             side CPOL          ; trailing edge: peer samples MOSI
sample:
    in pins, 1      side CPOL          ; synchronized MISO from trailing edge
    jmp r0--, bit   side CPOL
.wrap

; Unrolled CPHA0: final idle slot is the next frame's PULL.
.program spi_master_unrolled_cpha0
.side_set 1
.shift_out left
.shift_in left autopush 8
.wrap_target
frame:
    pull block      side CPOL
bit:
    out pins, 1     side CPOL           ; bit 7
    nop             side (1-CPOL)
    in pins, 1      side (1-CPOL)
    nop             side CPOL
    out pins, 1     side CPOL           ; bit 6
    nop             side (1-CPOL)
    in pins, 1      side (1-CPOL)
    nop             side CPOL
    out pins, 1     side CPOL           ; bit 5
    nop             side (1-CPOL)
    in pins, 1      side (1-CPOL)
    nop             side CPOL
    out pins, 1     side CPOL           ; bit 4
    nop             side (1-CPOL)
    in pins, 1      side (1-CPOL)
    nop             side CPOL
    out pins, 1     side CPOL           ; bit 3
    nop             side (1-CPOL)
    in pins, 1      side (1-CPOL)
    nop             side CPOL
    out pins, 1     side CPOL           ; bit 2
    nop             side (1-CPOL)
    in pins, 1      side (1-CPOL)
    nop             side CPOL
    out pins, 1     side CPOL           ; bit 1
    nop             side (1-CPOL)
    in pins, 1      side (1-CPOL)
    nop             side CPOL
    out pins, 1     side CPOL           ; bit 0
    nop             side (1-CPOL)
sample:
    in pins, 1      side (1-CPOL)
.wrap


; Unrolled CPHA1: final idle slot is the next frame's PULL.
.program spi_master_unrolled_cpha1
.side_set 1
.shift_out left
.shift_in left autopush 8
.wrap_target
frame:
    pull block      side CPOL
bit:
    out pins, 1     side (1-CPOL)           ; bit 7
    nop             side CPOL
    in pins, 1      side CPOL
    nop             side CPOL
    out pins, 1     side (1-CPOL)           ; bit 6
    nop             side CPOL
    in pins, 1      side CPOL
    nop             side CPOL
    out pins, 1     side (1-CPOL)           ; bit 5
    nop             side CPOL
    in pins, 1      side CPOL
    nop             side CPOL
    out pins, 1     side (1-CPOL)           ; bit 4
    nop             side CPOL
    in pins, 1      side CPOL
    nop             side CPOL
    out pins, 1     side (1-CPOL)           ; bit 3
    nop             side CPOL
    in pins, 1      side CPOL
    nop             side CPOL
    out pins, 1     side (1-CPOL)           ; bit 2
    nop             side CPOL
    in pins, 1      side CPOL
    nop             side CPOL
    out pins, 1     side (1-CPOL)           ; bit 1
    nop             side CPOL
    in pins, 1      side CPOL
    nop             side CPOL
    out pins, 1     side (1-CPOL)           ; bit 0
    nop             side CPOL
sample:
    in pins, 1      side CPOL
.wrap
