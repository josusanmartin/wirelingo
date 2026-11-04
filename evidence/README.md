# Evidence

Raw records the docs cite, kept in the repository so that a reader can check
them. Each folder has a README with what it holds, its evidence level, its
source and the redactions made before it was committed.

| Folder | What | Level |
|---|---|---|
| [verifier/](verifier/README.md) | The independent verifier's items and reports, 000-041, 21 PASS and 21 FAIL, verbatim apart from the listed redactions | Review records |
| [kv260-usb-20261030/](kv260-usb-20261030/README.md) | Kernel and step logs of Linux enumerating `usb_enum` (246 words, before `fault-fixes`) at low and full speed | RTL in FPGA fabric |
| [fabric-ping-20261103/](fabric-ping-20261103/README.md) | Board run of the ARP/ping/UDP stack through the KV260 build, one 64-byte frame at a time, no cable. Verified ([report 037](verifier/037-fabric-ping-fix3.report.md), after FAILs 029, 031 and 034) | RTL in FPGA fabric |

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
replacements in the USB kernel logs, and 17 in verifier files 030-038 (none in 039-041), which
were also searched for the Kria's tailnet label, the local user's home
directory, MAC addresses and Wi-Fi network names. Runner names such as
`runner1` and paths under `~/protoemu-flow` are kept.
