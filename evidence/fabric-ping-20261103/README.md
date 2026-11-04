# Fabric ping on the KV260, 2026-11-03

**Status: verified.** The code went through verifier items 029, 031, 034 (each
`VERDICT: FAIL`, on the board not being handed back when a run is stopped at
the wrong moment, and on `--compare` ignoring the link pulses) and 037
(`VERDICT: PASS`). [Report 029](../verifier/029-fabric-ping.report.md) is in
`evidence/verifier/`; reports 031, 034 and 037 are not copied there yet. The
fixed code, branch `fabric-ping` at `1b3ff6b`, is merged into this branch.
Every report reproduced the board results from the files here.

The board run below used the first version of the code (`da82a51`). The
host-stack and FPGA-fabric paths it exercised did not change after it; what
changed is how the board is handed back on an early end, and `--compare`,
which now also requires the link pulse to match. The board file from that run
still gives SAME against a model file made with the final code (`1b3ff6b`),
which is the `fabric-ping-m.json` here. `smoke-1b3ff6b/` is a later probe run
of the final code on the board (2026-11-04).

## What ran

Backlog item D: the ARP/ICMP/UDP host stack of
[docs/eth10-network.md](../../docs/eth10-network.md) through the KV260 build
of the RTL instead of the cycle model. The orchestrator ran it on 2026-11-03,
20:04-20:06 UTC, with app `protoemu` active alone (RTL `d1aa46f`, clock
measured at 49.99916 MHz).

**Evidence level: RTL in FPGA fabric (KV260)**, one frame at a time, 64-byte
frames each way. The frame into the chip comes from the FPGA's pattern
generator on RXP; the chip's reply (`eth10_tx_cs`, on core 1) is read off TD
inside the FPGA by `usb_sampler` on core 0 (`ethhost.RX_CORE`); the host port
is driven by the fabric SPI master. No cable, no magnetics, no PMOD pin, no
comparator: this is not a network link, and it says nothing about the chip's
pads.

1. `eth_tap.py --backend fpga --probe`: ARP, ping and UDP each answered with a
   64-byte reply, FCS right; a bad FCS and another MAC get no reply.
2. `eth_tap.py --compare fabric-ping-b.json fabric-ping-m.json` (with the
   `1b3ff6b` code): each reply's link pulse and TD edges equal the model's at
   one of its divider phases (arp phase 1, ping phase 4, udp phase 0, one
   100 ns link pulse each), and the received records are equal. SAME.
3. `unshare --net -- eth_tap.py --backend fpga --selftest`: a real Linux
   network stack in a namespace, against the board: 5 of 5 pings (average
   round trip 52.3 ms), UDP echo of "wirelingo", no reply for an unknown IP,
   another IP, another MAC or a bad FCS, `"pass": true`.

The 2026-11-04 smoke run of `1b3ff6b` (`smoke-1b3ff6b/`): `protoemu` active
alone, 49.99922 MHz measured, probe PASS on all five frames; `--compare`
against the same `fabric-ping-m.json` gives SAME (arp, bad_fcs, other_mac
phase 4, ping phase 4, udp phase 2). The selftest was not repeated, and no
fault was injected on the board: the hand-back paths have run on the fake
board of `test/model/test_fabric_ping.py` only.

## Files

| File | What it is |
|---|---|
| `fabric-ping-b.json` | The 2026-11-03 board probe results (`rig: KV260`, its build identity and measured clock, every frame in and out) |
| `fabric-ping-m.json` | The same probe on the cycle model, made with `1b3ff6b` (link pulses per divider phase), for `--compare` |
| `selftest.log` | The ping output and the selftest's JSON result from the Linux namespace run |
| `archive.sha256` | The sha256 of `git archive da82a51 tools firmware fpga/kv260/host`, the code copied to the board on 2026-11-03 |
| `smoke-1b3ff6b/fabric-ping-b.json` | The 2026-11-04 board probe of `1b3ff6b` |
| `smoke-1b3ff6b/archive.sha256` | The sha256 of `git archive 1b3ff6b tools firmware fpga/kv260/host`, checked on both ends |

The 2026-11-03 files are verbatim copies of
`~/dev/protoemu-verify-reports/evidence/029/`, and the smoke files of
`evidence/037/` there. The model file made with `da82a51` (sha256
`c4385127…ecfc0`) was replaced, because the final `--compare` refuses a model
file without link pulses per phase. The redaction search (tailnet hostnames and
addresses, 192.168 addresses, email addresses, the provider hostname,
credentials, MAC addresses) found nothing. The address `10.77.0.2` in
`selftest.log` belongs to the test's own network namespace, not to any real
network, and is kept.
