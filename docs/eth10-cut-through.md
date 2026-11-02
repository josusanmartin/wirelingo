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

The spread of one clock is the sampling phase, and the test asserts it at both
clocks. On the 5 FPGA frames the phase happened to give 10 clocks each time. The
KV260 run and its log are in
[fpga/kv260/README.md](../fpga/kv260/README.md#cut-through-match-on-hardware-2026-10-30);
the run had 3 generator frames on the chip's clock and 3 on an unrelated clock, one of them below the threshold, which raised nothing. The sampler is a
second core reading both pads every clock.

`test/model/test_eth10_match.py` also checks a field equal to the threshold, a
field below it, another EtherType, and the rise 38.2-38.6 us before the last edge of
a 64-byte frame on the model. HIT is a pulse: it falls within 1 us of the frame's
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
* Only pin output so far: no reply frame.
