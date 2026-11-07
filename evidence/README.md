# Evidence

Raw records the docs cite, kept in the repository so that a reader can check
them. Each folder has a README with what it holds, its evidence level, its
source and the redactions made before it was committed.

| Folder | What | Level |
|---|---|---|
| [verifier/](verifier/README.md) | The independent verifier's items and reports, 000-050, 27 PASS and 24 FAIL, verbatim apart from the listed redactions | Review records |
| [kv260-usb-20261030/](kv260-usb-20261030/README.md) | Kernel and step logs of Linux enumerating `usb_enum` (246 words, before `fault-fixes`) at low and full speed | RTL in FPGA fabric |
| [fabric-ping-20261103/](fabric-ping-20261103/README.md) | Board run of the ARP/ping/UDP stack through the KV260 build, one 64-byte frame at a time, no cable. Verified ([report 037](verifier/037-fabric-ping-fix3.report.md), after FAILs 029, 031 and 034) | RTL in FPGA fabric |
| [kv260-usbfs-zlp-20261103/](kv260-usbfs-zlp-20261103/README.md) | Logs of the full-speed data device's zero-length transfers and 200-attach runs, before and after the `usbfs-zlp` software fixes, with the `SET_CONFIGURATION` stall still open | RTL in FPGA fabric (KV260, protoemu48 `60b0692`) |
| [model-usb-fs-enum-sweep-20261104/](model-usb-fs-enum-sweep-20261104/README.md) | Output of `test/model/usb_fs_enum_sweep.py` at three commits of the device software: 40 enumerations at each of five SPI timings | Cycle model |
| [model-usb-guard-20261104/](model-usb-guard-20261104/README.md) | The `got_in` guard in `usb_fs_dev` and `usb_ls_dev` before (`c5ee980`) and after (`ac382d2`): `pe.timing` turnarounds, the RTL test module's log, the replay comparison. Verified by verifier report 046, after FAILs 044 and 045 (reports after 050 are not yet copied into `verifier/`) | Cycle model and RTL simulation |
| [ocaml-parity-20261104/](ocaml-parity-20261104/README.md) | `summary.txt` and `rc.txt` of `ocaml/scripts/evidence.sh` at a167455 (base 8c9599c): the Hardcaml and OCaml port's every gate, all passed. The numbers of [docs/hardcaml-port.md](../docs/hardcaml-port.md) | Simulation and formal (not gate level, board or silicon) |
| [model-usb-ls-restage-20261104/](model-usb-ls-restage-20261104/README.md) | Per-access tables of the low-speed two-core re-stage and of a stage abandoned by a new SETUP at both speeds, before and after the `usb_ls_tx` flush and guard and `pe.usbdev`'s checks on every stage; turnaround runs; the scripts. Verified by verifier report 047 (reports after 050 are not yet copied into `verifier/`) | Cycle model |

[history-map.txt](history-map.txt) maps each commit hash of the history before its
rewrite for publication, which the records here cite, to its hash in this repository.

Not in git: the release receipts' archive,
`wirelingo-release-evidence-7685aba-fo6.tar.gz` (55,515,939 bytes, sha256
`868c5d3ab8010cac27040617a869377076df6067e9fa1f28203ba5cf357409bf`), every
hashed input and result of the fo6 release gate with software `5457d8e`
([docs/release-evidence.md](../docs/release-evidence.md)). It is to be
attached as a release asset when the repository is published.

Redactions, in every folder: tailnet hostnames (`*.ts.net`) and addresses
(100.64.0.0/10), private LAN addresses (192.168.x.x), email addresses, the
build machine's provider hostname, and anything that looks like a credential
or key were searched for and would be replaced by a bracketed `[... redacted]`
note. What was found and replaced is listed in each folder's README: six
replacements in the USB kernel logs, and 17 in verifier files 030-038 (none
in 039-041, and 2 in 042-050), which were also searched for the Kria's tailnet label, the local
user's home directory, MAC addresses and Wi-Fi network names. The READMEs of
`kv260-usbfs-zlp-20261103/` and `model-usb-fs-enum-sweep-20261104/` have no
redaction section: a search of both for all of these classes on 2026-11-04
found only `/home/ubuntu` paths on the Kria, and nothing was replaced. Runner
names such as `runner1` and paths under `~/protoemu-flow` are kept.
