"""Mode-0 nibble stream, four PIO instructions per SCK period.

The first TX word contains (sample_count - 1) in bits 31:16 and a final
half-word flag in bits 15:0. Every subsequent word contains eight MSB-first
nibbles. Command bits are encoded on IO3; address/data may be quad or serial.
RX includes header samples and a final completion marker, emitted with SCK
low only after the final falling edge and hold interval. Both FIFOs use DMA.
"""
import rp2


@rp2.asm_pio(out_init=(rp2.PIO.OUT_LOW,) * 4, sideset_init=rp2.PIO.OUT_LOW,
             out_shiftdir=rp2.PIO.SHIFT_LEFT, in_shiftdir=rp2.PIO.SHIFT_LEFT,
             autopull=True, autopush=True, pull_thresh=32, push_thresh=32)
def spi_stream():
    wrap_target()
    pull(block).side(0)
    out(x, 16).side(0)
    out(y, 16).side(0)
    mov(isr, null).side(0)[7]
    label("samples")
    out(pins, 4).side(0)
    nop().side(1)
    in_(pins, 4).side(1)
    jmp(x_dec, "samples").side(0)
    jmp(not_y, "complete").side(0)
    in_(null, 16).side(0)
    out(null, 16).side(0)  # Discard TX padding before the next control word.
    label("complete")
    nop().side(0)[7]
    mov(isr, invert(null)).side(0)
    push(block).side(0)
    wrap()
