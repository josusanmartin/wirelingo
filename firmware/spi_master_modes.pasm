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
; Both programs use 6 words. Within a byte each bit takes 5 ticks: SCK is
; active for 2 ticks and idle for 3. Two extra idle ticks (PULL, LDI) separate
; bytes; no continuous-clock or fastest-SPI claim is made.
; Use integer CLKDIV >= 3, input synchronization enabled. The tests cover
; a digital peer launching MISO one clock after its launch edge; board and
; pad delay budgets still require separate validation. spi_master_fast is
; the separate mode-0 program for the existing synchronizer-bypass study.

.program spi_master_cpha0
.side_set 1
.shift_out left
.shift_in left autopush 8
.wrap_target
    pull block      side CPOL
    ldi r0, 7       side CPOL
bit:
    out pins, 1     side CPOL [1]       ; data before the leading edge
    nop             side (1-CPOL)      ; leading edge: peer samples MOSI
    in pins, 1      side (1-CPOL)      ; synchronized MISO from leading edge
    jmp r0--, bit   side CPOL          ; trailing edge, then next bit/frame
.wrap

.program spi_master_cpha1
.side_set 1
.shift_out left
.shift_in left autopush 8
.wrap_target
    pull block      side CPOL
    ldi r0, 7       side CPOL
bit:
    out pins, 1     side (1-CPOL) [1]  ; leading edge: launch MOSI/MISO
    nop             side CPOL          ; trailing edge: peer samples MOSI
    in pins, 1      side CPOL          ; synchronized MISO from trailing edge
    jmp r0--, bit   side CPOL
.wrap
