# 10BASE-T cut-through match

`firmware/eth10_match.pasm` acts on a frame while it is still arriving. The core
checks the EtherType, compares a 16-bit field with a threshold the host set, and
raises a pin right after the field's last bit, then passes the field to the RX
FIFO. The frame's remaining bytes and its FCS have not arrived yet.

This is the reaction path of a low-latency packet filter on a 10 Mbit/s link. It
shows timing that is short and repeatable, not bandwidth: a bit takes 100 ns here.

## Measured

Reaction = from the field's last mid-bit transition at the pad to the pin. A
Manchester bit is known at that transition, 50 ns before its cell ends.

| Where | Clock | Result |
|---|---|---|
| Cycle model, no jitter, 60 frames | 50 MHz | 10-11 clocks, 200-220 ns |
| Cycle model, no jitter, 60 frames | 40 MHz | 10-11 clocks, 250-275 ns |
| Cycle model, 802.3's +-13.5 ns edge jitter, 24 frames | 50 MHz | 200-219 ns |
| KV260, the chip's RTL in fabric, 5 frames | 50 MHz | 10 clocks (200 ns) every time, 38.2-38.5 us before the frame's last edge |

The spread of one clock is the sampling phase: an edge lands up to a clock before
the chip's next sampling edge. The test asserts it at both clocks. The FPGA row
can't show that spread, because its sampler sees the edge on a clock too: the model
gives exactly 10 clocks measured that way. So the row confirms the path's length,
not the 10-11 range. The run and its log are in
[fpga/kv260/README.md](../fpga/kv260/README.md#cut-through-match-on-hardware-2026-10-30):
3 generator frames on the chip's clock and 3 on an unrelated clock, one of them
below the threshold, which raised nothing. The sampler is a second core reading both
pads every clock.

`test/model/test_eth10_match.py` also checks a field equal to the threshold, a
field below it and another EtherType. It bounds the rise against the frame's end:
384 bits (44 payload bytes and the FCS) minus 10-11 clocks, plus 250 ns of the test
sender's TP_IDL if the frame ends in 1. On a 64-byte frame from a sender 100 ppm fast, that
is 38.18-38.21 us (ends in 0) or 38.43-38.46 us (ends in 1) at 50 MHz, and 38.13-38.16
us or 38.38-38.41 us at 40 MHz. HIT is a pulse: it falls within 1 us of the frame's
last edge.

## Limits

* No real network yet: the FPGA's frames come from pe_lab's pattern generator.
  Digital levels on fabric pads, with no magnetics, comparator or cable.
* In the chip the pad and comparator delays come on top. They are unmeasured.
* Speculative by design: the decision comes before the FCS, and this firmware
  can't check it (no CRC-32), so a corrupted frame can raise the pin.
* One field in a fixed place, compared with one threshold. Fields are 16 bits and
  little-endian on the wire. The threshold is set once, at start, and wraps at 16
  bits (0x10005 behaves as 5).
* The pin and the records can disagree: `push noblock` drops the record when the
  8-word RX FIFO is full, while HIT still rises. With 12 hits and no host pops, 8
  records survive.
* A preamble shorter than 31 bits before the SFD is missed with no record. 802.3's
  preamble is 62 bits.
* 40 MHz has no margin for 802.3's jitter: at +-13.5 ns, 1 of 160 frames lost its HIT
  and record on the model (one independent sample, not a rate). See
  [guide.md](guide.md#known-limits).
* Only pin output here. `eth10_reply` adds a reply frame after the frame and the
  96-bit gap: [eth10-reaction.md](eth10-reaction.md).
