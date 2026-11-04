# Evidence

Raw records the docs cite, kept in the repository so that a reader can check
them. Each folder has a README with what it holds, its evidence level, its
source and the redactions made before it was committed.

| Folder | What | Level |
|---|---|---|
| [verifier/](verifier/README.md) | The independent verifier's items and reports, 000-029, 15 PASS and 15 FAIL, verbatim | Review records |
| [kv260-usb-20261030/](kv260-usb-20261030/README.md) | Kernel and step logs of Linux enumerating `usb_enum` (246 words, before `fault-fixes`) at low and full speed | RTL in FPGA fabric |
| [fabric-ping-20261103/](fabric-ping-20261103/README.md) | Board run of the ARP/ping/UDP stack through the KV260 build, one 64-byte frame at a time, no cable. **Pending verification** (item 029) | RTL in FPGA fabric |

Not in git: the release receipts' archive,
`wirelingo-release-evidence-7685aba-fo6.tar.gz` (55,515,939 bytes, sha256
`868c5d3ab8010cac27040617a869377076df6067e9fa1f28203ba5cf357409bf`), every
hashed input and result of the fo6 release gate with software `7685aba`
([docs/release-evidence.md](../docs/release-evidence.md)). It is to be
attached as a release asset when the repository is published.

Redactions, in every folder: tailnet hostnames (`*.ts.net`) and addresses
(100.64.0.0/10), private LAN addresses (192.168.x.x), email addresses, the
build machine's provider hostname, and anything that looks like a credential
or key were searched for and would be replaced by a bracketed `[... redacted]`
note. What was found and replaced is listed in each folder's README: six
replacements, all in the USB kernel logs. Runner names such as `runner1` and
paths under `~/protoemu-flow` are kept.
