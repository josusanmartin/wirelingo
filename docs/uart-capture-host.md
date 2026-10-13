# Declared UART capture through the serial host

This optional core0 firmware captures a declared 1–32-byte 8N1 message, packs
four bytes per RX word, retains suspect bytes and sets sticky F0 for a bad
STOP. It halts after the final STOP sample, possibly before physical STOP
ends. A partial final word is high aligned; `decode_capture` rejects wrong
counts or nonzero low padding. Decoder success alone does not validate
framing or data. F0 belongs to this capture workload.

The source dependency is commit `82ba9b24f88d7df04b68419aab956208e2e67bfd`:
`firmware/uart_rx_capture.pasm` and `tools/pe/uartcapture.py`. Integrate those
files before this test/docs change. The firmware is 19 words/76 bytes;
no generic host, model, RTL or runtime change is needed.

Load once with core0 disabled, RX16 mapped to `ui[0]`, synchronized inputs,
normal pin ownership and `host.clkdiv_for(50_000_000, 8 * 115200)`. This is a
50MHz system clock (20ns), not 48MHz. The test uses two cores/two banks256,
depth8 and core1 disabled. The helper preserves an unrelated enable bit,
but concurrent-core operation is not qualified by this experiment.

Before every arm/rearm, pay ordinary STATUS and CTRL reads. Require both
FIFOs empty, no fault, and core0 disabled or HALTed. Do not clear unexpected
leftovers to manufacture success. Apply `uartcapture.capture_plan(length,
enabled_before=ctrl & 3)` through normal serialized writes: disable, adjacent
R1/R2 count burst, FCLR1, restart/enable, in that order. No FIFO clear,
reset or reload belongs in normal rearm. The host must know the declared
length and arm before the independent source starts.

After the caller's fixed service time, read STATUS and F0, then one
fixed-address FIFO burst of exactly `ceil(length/4)` words, and empty STATUS.
Require HALT/no fault, exact RX count, TX0, expected raw data and framing
flag. Read F0 before the next ordinary FCLR1: a bad final STOP must survive
as data plus sticky F0. The three-byte example `39 d3 81` produces exactly
`0x81d33900`, with no phantom byte.

## Maintained native regression

With the capture dependency and cocotb/Icarus already installed:

```sh
python test/test_uart_capture_host.py --output /tmp/uart-capture-fresh
# Optional: --icarus-bin /path/to/icarus/bin
```

Output must be fresh/empty; defaults use a fresh temporary directory. The
unchanged SPI runner selects only `test_uart_capture_host.test_capture_rearm`,
uses actual top RTL/behavioral SRAM, host SCK8 system clocks, a 230000-clock
ledger and the existing 108-second inner timeout. It retains time0 runtime
maps, normal pygpi delegates, exact argv/test guards and `-none` waveform
suppression. No installation or bootstrap is performed.

The one declared test cold-loads once, then captures 32 bytes with a low
final STOP and three distinct bytes with valid STOPs, without reset/reload.
Its rational 115200-baud source begins at absolute cycles24000 and190000;
all input slots are predeclared and advance independently of host readiness.
The physical source shares the host UI shadow through `set_low_inputs`.
Host service begins at fixed cycles164000 and204100, never from DUT state,
FIFO occupancy, flags or peer completion. Both API calls and physical host
CS/SCK must remain idle from arm return through each service time.

First completion/drain plus second arm must return by180000; second full
completion must return by210000. Arming must precede each fixed source
START. All precondition/count/FCLR/start and STATUS/F0/drain costs, readback,
pin transitions, raw words, flags and failures are saved. Expected arm3168,
N32 completion3904, N3 completion2112 and drain+rearm7072 clocks are source
accounting predictions; actual run artifacts establish measured outcomes.
Cold load is charged separately. Deadline failures retain complete rows.

This is a finite nominal source/schedule/core0 qualification, not a general
baud envelope, indefinite stream, idle packet inference, timeout/flush,
live-core1, CPU overhead, pad/Fmax/PPA, silicon or board claim. Earlier full
words can appear before STOP validation; wait for whole-capture HALT/status
and read F0 before treating data as framed.

## Retained native outcomes

C14 failed at time0 before any clock or host transaction: the initial pin
observer read an unresolved UI value before its queued write settled. C15
adds only a1ps wait before that first observation; all later strict integer
conversions, source times, service times and oracles are unchanged. Actual
C15 pins first appear at1ps, before the first10000ps clock edge.

The single C15 native attempt then failed during cold configuration
readback at cycle11487 (229740ns), returning six zero words instead of the
written configuration values. The selected RTL intentionally exposes
core configuration offsets0x00–0x06 as write-only, returning zero on reads;
this test's readback expectation is incompatible with that interface. No
assertion was removed or changed under the settlement ticket. Nine cold
host frames paid11472 serial clocks plus15 reset/setup wait clocks before
the failure. Neither capture was armed and no UART frame started, so
3168/3904/2112/7072 remain unmeasured predictions. This is a retained test
setup failure, not a UART/RTL counterexample or passing qualification.
The actual runtime identity, exact single-test collection and waveform
suppression succeeded; there was no retry.

## C16 measured qualification

C16 retains the same paid six-word configuration read and checks the
documented six zero words. It records the intended configuration values
separately and validates the already-submitted ordinary six-word write
frame. Submitted-frame evidence is not an internal configuration read or
a separate proof of pad delivery. No timed transaction or wait was removed.
The C14 startup failure and C15 unsupported-readback failure remain retained.

The one native group passed both exact captures: the32-byte bad-final-STOP
message retained every byte and F0=1; after ordinary paid FCLR1/rearm,
`39 d3 81` returned exactly `0x81d33900` with F0=0. Both have HALT/no fault,
exact RX8/RX1 with TX0 before draining, empty final FIFOs and zero host API
transactions or physical host CS/SCK activity inside their quiet windows.

| Paid measurement | First32B | Second3B |
|---|---:|---:|
| Arm, including fresh STATUS/CTRL reads |3168 clocks|3168 clocks|
| Completion STATUS/F0/drain/empty STATUS |3904 clocks|2112 clocks|
| Arm return, absolute cycle |15183|171072|
| Margin before fixed source START |8817 clocks|18928 clocks|
| Scheduled service, absolute cycle |164000|204100|
| Final returned empty STATUS, absolute cycle |167904|206212|

First drain plus second arm costs7072 clocks, returning at171072 with8928
clocks of slack against180000. Final completion has3788 clocks of slack
against210000. Cold load is separate:12015 clocks (12000 serialized host
transaction clocks plus15 reset/setup-wait clocks), ten frames and185 serial
bytes, including76 program bytes. The entire run uses30 host frames,2984
host SCK bits,24352 serialized transaction clocks and206212 total RTL
clocks, including the predeclared idle waits, below the230000 cap.
The measured3168/3904/2112/7072 costs match the prior predictions.

This acceptance applies only to the declared50MHz/115200 two-message source
and fixed service schedule on core0 with core1 disabled and behavioral
SRAM. Source timing never follows host or DUT readiness. It does not extend
the baud envelope, prove indefinite-stream capture, live-core1 concurrency,
CPU cost, pad/Fmax/PPA, silicon or board operation.
