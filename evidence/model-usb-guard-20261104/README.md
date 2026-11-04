# The got_in guard, before and after, 2026-11-04

What `usb_fs_dev`'s got_in guard (branch `usb-guard`, commit c4003a2) changes
in the turnarounds, at each level, against bc98a97 (the firmware before it).
Levels: **model** (the cycle model and the static checker) and **RTL**
(Icarus, cocotb). Not gate level, not the board, not silicon.

Run on runner1 (`run.sh`; `remote_test.sh model test lockstep` at c4003a2 for
the RTL module log), except the `pe.timing` outputs, run locally (static, no
simulation). Absolute paths in the logs are shortened: the runner's home to
`~`, the c4003a2 checkout to `<checkout c4003a2>`, the bc98a97 tree to
`<tree bc98a97>`; nothing else is changed.

| file | level | before (bc98a97) | with the guard (c4003a2) |
|---|---|---|---|
| `pe-timing-usb_fs_dev-*.txt` | model, static | in_data_ta [12, 17] clk, in_nak_ta [10, 15]; 141 words | [13, 18], [10, 16]; 142 words; every deadline PASS |
| `timing-check-*.log` | model, 8 sessions | IN → DATA bus 14.73..15.45 clk | 15.73..16.45 clk (30 passed) |
| `usbfs-model-*.log` | model, `test_usb_fs.py -s` | ACK 10.9, NAK 12.7, IN → DATA 14.7 clk; enumeration 2.60-3.84 bit times | 10.9, 12.7, **15.7**; 2.60-4.09 (19 passed) |
| `rtl-fsdev-bc98a97.log`, `rtl-test-module-c4003a2.log` | RTL, `test_usb_fs_device` | ACK 10.5, NAK 13.4, DATA1 14.9 clk | 10.5, 13.4, **15.9** clk; the whole `test` module 27/27 |
| `replay-*.log`, `compare-dev-replay.txt` | model, `usb_replay_demo` `ls-dev` / `fs-dev` | ALL PASS | ALL PASS; same requests, re-stage count 1, the same 74 wire packets; pad timing differs |

## sha256

```
d3818b61f2b748a27f9477d3f7f85da62e4a82c87ba967f8600a33110921949b  compare-dev-replay.txt
881bd2522de425eacadd651bc5b87cc0188ec0730c001d2182e2c1ab5a2fedf6  pe-timing-usb_fs_dev-bc98a97.txt
d798f4b6f97f4e5daf38c2b48ea86ba08c7fb9faea70b3a6f5ee6bfd460f870b  pe-timing-usb_fs_dev-c4003a2.txt
4836680a0d844fef2923b15fbf0b62252ab5f13be274ffada4056e06c919c324  replay-bc98a97.log
bf547029586ef58ed6430ed5c3067baf6d2c6b96bd99a8a782a9f40d114dd63b  replay-c4003a2.log
79e07aa0ffcea282f791863ff02fd35109df161f208f73fbf0e6b87e9a0a6f2b  rtl-fsdev-bc98a97.log
a5e2b3287b235e3a35a657bafddaac76a404046de2c1a82cac68a2eb2c197452  rtl-test-module-c4003a2.log
f21606d1b94233939b76b134a8f728926cf4ecfc7d0b9dea434f250fca88af21  run.sh
d821dcea756db17dfbbbf14f690dfc6ce7c1019d7ca7da427186ca3bb95a0d93  timing-check-bc98a97.log
eb61737cb1c45a3614dda2f72676f6cca22f639455cc6121a136626558eae544  timing-check-c4003a2.log
23d98447894470303a07a09ee69d995d9455afd9def462d71ce97f7fc073923a  usbfs-model-bc98a97.log
8f9c24487406632985aeba24407d1ac672654deb0afc0da5cca093f710bd6429  usbfs-model-c4003a2.log
```
