Commit: aedf991aa8154d0024d09c726011d059191a3d0e  Base: 61af9b2
Item: fixes for 000's report (read it, inbox/000-eth10-match.report.md). Check every finding 1-9 is resolved, then re-attack.
Claims:
1. The firmware change is comments only: assembled output equals 61af9b2's.
2. Tests now fail for the delay mutations N >= 1 at both clocks, and for payload=2.
3. Reaction is 10-11 clocks on the model; rise is 38.2-38.6 us before a 64-byte frame's last edge.
4. Threshold wrap, FIFO loss, short preamble, HIT pulse and 40 MHz jitter limits are stated and, except 40 MHz, tested.
5. The KV260 section quotes the real log from the Kria (you cannot rerun it, check that it fits the demo's output format).
