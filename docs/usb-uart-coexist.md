# Autonomous USB plus declared UART peer capture

This optional program allocates core1/bank1/F6 to a known-length UART capture
beside autonomous `usb_enum` on core0/bank0/F0..F5. The existing core0/F0
UART API stays unchanged. This allocation does not apply to FIFO-based USB,
which uses all eight flags, and it does not forward UART bytes through USB.

Load both images while both cores are disabled, using normal TAPEOUT loaders.
At 48MHz USB uses the low-speed line unit on logical pads0/1; UART receives
115200 baud, 8N1 on logical RX16 (ui0), with the unchanged eight-tick divider.
The peer image uses19 words/76B; selected USB uses246 words/984B. Together
1060B occupy both engines and banks (10/237 words spare), one line unit,
flags0..6 and three physical inputs/pads0/1/16. UART uses no virtual pins,
mailbox, shared data registers, or line unit. F7 is unallocated here; no spare
engine is demonstrated. RX16 remains input-only.

`pe.uartcapture_peer.capture_plan(N, enabled_before=current_mask)` supports
only core1 and integer N1..32. Before arm/rearm, ordinary reads must establish
that core1 is stopped/disabled and its RX/TX FIFOs are empty. Its five writes
disable only core1, pay R1=N/R2=Nmod4, clear only F6 with FCLR64, and
restart/enable only core1, preserving core0's current enable. A running USB
core must not be disabled/restarted or have its flags/FIFO cleared. Use the
unchanged strict `decode_capture` exported by this module after observing
HALT/no fault, expected RX occupancy and F6 through ordinary host reads.
Complete four-byte words are little endian; a final1..3-byte word is high
aligned. No implicit drain/flush or arbitrary-idle packet inference exists.

Bad STOP retains suspect bytes and sets sticky F6 before HALT. Completion is
after the final synchronized STOP sample, not necessarily physical STOP end.
Earlier complete words can be published before STOP checking. Application
must know N and arrange the start; N32 uses all eight RX FIFO words without
metadata. This finite mode is not an indefinite stream/FIFO-full guarantee.

## Bounded pin-level regression

Run `PYTHONPATH=tools python3 -m pytest -q test/model/test_usb_uart_coexist.py`
on a checkout containing the final autonomous USB and declared UART capture
prerequisites. The model is explicitly2cores/2banks/256words/depth8; inputs
use normal two-clock synchronization. The external USB controller is a pin
peer, distinct from the loading host. ModelHost bus service is paid in model
clocks and does not establish serialized SPI-host latency.

Two cases are predeclared. A32-byte UART burst starts at4096 and receives no
loading-host service until150000 while the selected first-attempt low-speed
USB enumeration reaches address5/configured. A fresh case requests a missing
string (expected USB STALL), captures bad-final-STOP39d381 starting24000,
services/drains at40000 and rearms core1 while preserving USB F0. A second
capture a65c27 starts65000; at66000 a normal USB device-descriptor request
clears STALL through SETUP. No loading-host service follows rearm until100000.
Raw resolved USB pads, packet bytes/CRC/toggles, UART source/sample/STOP/
publication/HALT, flags/enables and paid bus operations are retained. The
UART transmitter and service times never depend on DUT PC or readiness.

## C15 result: failed harness qualification, no concurrency acceptance

The only dynamic invocation failed case1 at the150000 service deadline:
core1 STATUS read returned0 (HALT0/fault0/RX0/TX0), so no UART drain or
byte comparison ran. The selected enumeration helper had returned before
the deadline, but the final configured-state read, complete saved-wire oracle
and overlap assertions were not reached. Case2 was not run (pytest stopped
on the first failure). This result does not qualify the peer image or error/
clear/rearm isolation, and does not imply that the resource allocation fails.

The setup contains a concrete enable-mask race. CTRL is registered and
applies on the next model clock. Immediately after the peer plan's final
write514 (enable2/restart2), core enable still reads0. ModelHost.start(1)
therefore submits257 (enable1/restart1), replacing the peer enable on the
following clock. Source-derived timing places these writes at289/290;
both-start return is291. The UART wire does not start until4096. These are
an offline reconstruction from the fixed write sequence and documented model
semantics, not a recovered raw execution trace. The model's explicit geometry
was2/2/256/depth8; status0 is the saved failure observation.

A second harness bug prevented the failure record from being serialized:
USB transaction log entries contain bytes, and json.dumps without a bytes
normalizer raised TypeError on an ACK byte. No case1 raw trace file was
written. Preserve the traceback, JUnit and exact step counter; raw UART/USB
samples, overlap count, flag transitions, service history and overflow/stall
counts are missing. They must not be inferred from eventual descriptor equality.
The original harness is frozen unchanged, including both failures.

The zero-clock preflight passed19/246 words, identical UART timing/shift/wrap
with only flag immediates at indices11/15 changed0 to6; relocated branches
stay256..274.128 valid plans,16 invalid inputs and three rejecting pure mocks
(payload corruption, unauthorized bus strobe, nominal USB error) passed.
Preflight exit0/0.292s; dynamic exit1/11.495s, one failed case/one unrun case,
150004 counted Chip.step calls including cold loads/setup/final reads, below
500000. No second invocation or candidate correction was attempted.

Execution reference was disposable Git tree
f8772208d86e21ca1cec573bd33b353b8dbb5b72, with only these four new files
added, while the worker branch stayed on82ba9b2. Source/dependency hashes,
fixed offered-waveform plan, commands/exits and original candidate are in
`evidence/sol-kepler/C15-KEPLER-USB-UART-PEER/` under the team root.
For the frozen C15 version, the test command above failed and was not
merge-ready as a passing qualification; the C16 outcome below is separate.

The C15 diagnosis proposed the separately authorized two-fix C16 experiment:
use an explicit shared cold-start mask after paid R1/R2 setup (do not sample
a pending enable mask), and serialize raw transaction bytes as hex before
saving failures. Keep source images, absolute wire/service times and strict
oracles fixed. No firmware, extra engine/SRAM, clock/synchronization bypass
or hardware change is justified by this setup failure.
No concurrent native/RTL/pad/electrical, full-speed, CPU, PPA, or real SPI-host
claim follows from this model study.


## C16 narrow startup and evidence correction

The C15 failure packet remains unchanged. C16 replaces only case1's immediate
start with `mh.apply(host.start_writes(1, enabled_before=2))`: one paid CTRL
write0x103, enabling both cores while restarting only core0. The preceding
peer CTRL0x202 restart remains unchanged. There is no extra delay/read,
internal pending-enable peek, shared helper change or stimulus/oracle retune.
Case2's cold core0 start and all absolute service/deadline values stay fixed.

Saved nested bytes use explicit tagged hex, including empty bytes and
bytearray; unsupported object types still raise TypeError. For example,
`b"\xd2"` becomes `{"__bytes_type__": "bytes", "hex": "d2"}`.
To restore these values when reading a saved case:

```python
import json

def restore_bytes(value):
    if set(value) == {"__bytes_type__", "hex"}:
        kind = value["__bytes_type__"]
        if kind == "bytes":
            return bytes.fromhex(value["hex"])
        if kind == "bytearray":
            return bytearray.fromhex(value["hex"])
    return value

case = json.loads(saved_text, object_hook=restore_bytes)
```

This preserves raw arrays, original exception strings and USB ACK/empty/DATA
bytes instead of dropping or stringifying them. Candidate sources freeze
before C16's single bounded model invocation; its outcome is recorded below.


## C16 bounded concurrency result

Both unchanged dynamic cases pass on the pinned f877 execution tree. The
single invocation exited0 in20.948s (pytest20.52s):2passed/1pure-test deselected,
250047/500000 actual Chip.step calls, including cold loads, setup, reads and
drains. Case1 uses150034 clocks; case2 uses100013. The one zero-clock
preflight exited0 in0.356s, verifying the narrow whole-file AST delta,
22 unchanged dependencies, deferred-mask arithmetic and lossless tagged
round trips, while preserving the payload/strobe negative oracles. No second
invocation, firmware correction, default API edit or timing retune occurred.

| Measured outcome | Case1: enumeration plus32B | Case2: error, clear and rearm |
| --- | --- | --- |
| USB completion |109449, configured/address5/noSTALL |88137, address0/noSTALL |
| UART HALT clocks |137370 |36551 bad /77471 good |
| Delivered words |8words /32 exact bytes |0x81d33900 /0x275ca600 |
| UART RX peak |8words |1word |
| USB wire inventory |77packets (51peer/26device) |20packets (13peer/7device) |
| Data/STOP source margins |161/161 clocks |158/158 clocks |
| Loading-host quiet clocks |291..150000 |24000..40000 and40024..100000 |

Case1's UART source spans clocks 4096 through 137429. A total of 76 USB
packets, including all 26 device responses, overlap that interval. Both cores remain enabled
through the exchange; core1 HALTs after its final STOP sample and retains
all32 exact bytes without loading-host refill/service. The fixed150000 drain
returns the eight little-endian words and leaves the target FIFO empty.
USB has exactly its standard one intentional missing-string STALL; retries0,
first-response DATA toggles/CRCs and2..6.5-bit turnaround oracle all pass.
All measured response turnarounds are2.15625 low-speed bit times.

Case2's normal unsupported string sets USB F0 at4815. UART bad-final STOP
sets F6 at36395 (flags1->65), before HALT36551. The ordinary40000 service
returns39d381, retaining the suspect byte. Rearm's FCLR64 at40018 changes
65->1, preserving USB STALL/core0 enable; five plan writes and paid
inspection reads finish at40024, before the fixed65000 source. The request
started at66000 clears F0 through normal SETUP at70390. The second UART
capture returnsa65c27 with F6clear/HALT/no fault and no residual words.
Core0 is never disabled/restarted after its cold start, and neither image
is reloaded/reset or has its FIFO cleared. All380 UART START/data/STOP
observations across38 bytes are within the independent source windows.

Each case pays281 cold image/configuration writes (USB256 +UART25).
Case1's arm pays9 model clocks (two precondition reads/five writes), then
one shared start write. Case2's inspected arms each pay15 clocks; first drain
is9 and drain+rearm is24. Case1 completion/drain/state reads take34 clocks;
case2's last completion/drain/state reads take13. These are direct ModelHost
bus costs, not serialized SPI-host timings. Raw paid operations/strobes,
flag/enable changes, per-core FIFO peaks, pin windows/publications/HALTs,
resolved USB pad edges and complete direction-tagged packet/CRC inventories
are saved losslessly. No missing C16 case record and zero active loading-host
API/strobes, UART PUSH/autopush stalls, host FIFO drops or nonempty clears.
Normal WAIT/line blocking remains observable (core stall clocks141916/8645
and97630/42185); these include idle time, not loss of captured bytes.

The existing line unit reports276/92 RX overflow events in the two cases,
all recorded while its own transmitter was active. That observation does
not establish the cause. Neither line unit nor USB code changed;
passing the finite packet oracle does not establish overflow robustness.
No such claim, full-speed envelope, live serialized-host concurrency,
UART-to-USB forwarding, indefinite input stream, CPU/PPA/electrical or
concurrent native/RTL qualification is provided. Both engines are occupied;
this result uses the existing1060B program allocation and8-word UART FIFO,
without extra SRAM/cores/flags. F7 spare does not imply a spare engine.

C15's failed STATUS0/saver packet and missing raw evidence remain history,
not a passed experiment. C16's fresh complete evidence is in
`evidence/sol-kepler/C16-KEPLER-START-SAVE/` under the team root, including
preflight/dynamic exact commands/exits, dependency/source hashes, raw cases,
per-test counters, final four-file patch and two-file delta from C15. Worker
baseline remains82ba9b2. Current-main alignment/regression on34de7a0 is a
separate lead-coordinated integration step; this pinned experiment performs
no worker rebase or broader replay.
