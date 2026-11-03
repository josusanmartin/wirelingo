# Fabric ping on the KV260, 2026-11-03 (pending verification)

**Status: pending verification.** Verifier item 029 checks the code and this
run. Its first report, [029-fabric-ping.report.md](../verifier/029-fabric-ping.report.md),
is `VERDICT: FAIL` on two claims about the code: the board is not handed back
if a run ends while the board rig is being built, and `--compare` compares the
frame's TD edges but not the link pulses. The same report reproduced the board
results from these files (the archive hash, `--compare` giving SAME, the
selftest's numbers). Until item 029 passes, treat everything here as
unverified.

The code that ran is on branch `fabric-ping` (`da82a51`), which is **not
merged** into this branch: `fpga/kv260/host/fabric_eth.py` and the FPGA
backend of `eth_tap.py` are not in this tree.

## What ran

Backlog item D: the ARP/ICMP/UDP host stack of
[docs/eth10-network.md](../../docs/eth10-network.md) through the KV260 build
of the RTL instead of the cycle model. The orchestrator ran it on 2026-11-03,
20:04-20:06 UTC, with app `protoemu` active alone (RTL `d1aa46f`, clock
measured at 49.99916 MHz).

**Evidence level: RTL in FPGA fabric (KV260)**, one frame at a time, 64-byte
frames each way. The frame into the chip comes from the FPGA's pattern
generator on RXP; the chip's reply (`eth10_tx_cs`) is read off TD inside the
FPGA by a sampler on core 1; the host port is driven by the fabric SPI master.
No cable, no magnetics, no PMOD pin, no comparator: this is not a network
link, and it says nothing about the chip's pads.

1. `eth_tap.py --backend fpga --probe`: ARP, ping and UDP each answered with a
   64-byte reply, FCS right; a bad FCS and another MAC get no reply.
2. `eth_tap.py --compare fabric-ping-b.json fabric-ping-m.json`: the board's
   and the cycle model's runs are the same: each reply frame's TD edges equal
   the model's at one of its divider phases, and the received records are
   equal. SAME. The link pulses are not compared ([report 029](../verifier/029-fabric-ping.report.md), finding 2).
3. `unshare --net -- eth_tap.py --backend fpga --selftest`: a real Linux
   network stack in a namespace, against the board: 5 of 5 pings (average
   round trip 52.3 ms), UDP echo of "wirelingo", no reply for an unknown IP,
   another IP, another MAC or a bad FCS, `"pass": true`.

## Files

| File | What it is |
|---|---|
| `fabric-ping-b.json` | The board's probe results (`rig: KV260`, its build identity and measured clock, every frame in and out) |
| `fabric-ping-m.json` | The same probe on the cycle model, for `--compare` |
| `selftest.log` | The ping output and the selftest's JSON result from the Linux namespace run |
| `archive.sha256` | The sha256 of `git archive da82a51 tools firmware fpga/kv260/host`, the code copied to the board |

The files are verbatim copies of `~/dev/protoemu-verify-reports/evidence/029/`.
The redaction search (tailnet hostnames and addresses, 192.168 addresses,
email addresses, the provider hostname, credentials, MAC addresses) found
nothing. The address `10.77.0.2` in `selftest.log` belongs to the test's own network
namespace, not to any real network, and is kept.
