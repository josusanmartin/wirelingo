# Real-host USB logs, KV260, 2026-10-30

Raw logs from the day Linux first enumerated the chip's autonomous USB
firmware (`usb_enum`) on the Kria KV260: the step log of each command and the
kernel log, at low speed (about 18:10-18:16 UTC) and at full speed (about
18:50-18:52 UTC). The results built from them are in
[fpga/kv260/README.md](../../fpga/kv260/README.md#result-against-linux-low-speed-2026-10-30).

**Evidence level: RTL in FPGA fabric.** The chip's RTL (revision `60b0692`,
48 MHz build) on the KV260's FPGA, its USB lines on PMOD pins through 30 ohm
resistors, with the Kria's own Linux 6.8 xHCI, through the board's USB5744
hub, as the host. Not the chip, not silicon; one host, one hub, no scope.
The firmware is the **246-word `usb_enum` from before `fault-fixes`** (every
load line says "246 words"); the current 253-word one has not run here.

Source: `~/wirelingo-usb-real-host-20261030.tar.gz`, 44,637 bytes, sha256
`7d3a6160c8de411cfbfb9051285495d62f1c33a1e281cf50b2bc09b0f89d01f2`. Every file
in it is here, at the same path, verbatim apart from the redactions below.

## Files

| File | What it is |
|---|---|
| `ls/wl_steps.log` | Step log of the low-speed session: each command's start, output and end, timestamped. `usb_demo.py` (load, attach, wait for the kernel, check), `usb_stress.py 5000 30`, then six more load-and-attach cycles |
| `ls/wl_steps-tail.log` | The end of that log, from the sixth cycle on, including the run of the committed `usb_stress.py` (2000 reads, 10 resets) |
| `ls/timeline.txt` | The USB lines of the kernel log during the low-speed session |
| `ls/dmesg-final.txt` | The whole kernel log after the low-speed session |
| `ls/usb_stress-first-version.py` | `usb_stress.py` as first run (`usb_stress.py 5000 30`) |
| `ls/usb_stress-repo.py` | `usb_stress.py` as committed (`fpga/kv260/host/usb_stress.py`), run as `usb_stress_repo` |
| `fs/wl_steps.log` | The same step log from the start of the low-speed session, continued through the full-speed session: `usb_demo.py --speed full`, `usb_stress.py 50000 100`, six more cycles |
| `fs/dmesg.txt` | The whole kernel log after the full-speed session |

## What these files show, and what they do not

Counted from the files themselves:

| | Low speed | Full speed |
|---|---|---|
| Enumerated and configured, after a fresh load (`PASS: Linux enumerated` lines) | 7 | 7 |
| Descriptor reads by `usb_stress.py`, all right | 5,000 + 2,000 | 50,000 |
| Bus resets by `usb_stress.py`, each re-enumerated | 30 + 10 | 100 |
| `reset ... USB device` lines in the kernel log | 68 (low speed) | 130 (full speed) |

`fpga/kv260/README.md` reports more than these files hold: 18 low-speed and
14 full-speed attaches, 10,000 and 56,000 descriptor reads, 58 and 130 bus
resets. The rest of those runs are transcripts in that doc, not raw logs here.
The full-speed data demo of the same day (`usb_fs_data_demo.py`, 350 round
trips) is not in this archive either.

The kernel logs also show what fpga/kv260/README.md describes as expected
noise: `device descriptor read/64, error -32` and `device not accepting
address` bursts when the app is reloaded with the pull-up attached and no
firmware running yet.

## Redactions

Before committing, the files were searched for tailnet hostnames (`*.ts.net`),
tailnet addresses (100.64.0.0/10), private LAN addresses (192.168.x.x), email
addresses, the build machine's provider hostname, and anything like a
credential or key. Found and replaced:

| File | Line | What | Replaced with |
|---|---|---|---|
| `ls/dmesg-final.txt` | 149 | an email address in the kernel's `pps_core` copyright banner | `[email redacted]` |
| `ls/dmesg-final.txt` | 239 | an email address in the kernel's `device-mapper` banner | `[email redacted]` |
| `ls/dmesg-final.txt` | 319 | the Kria's Ethernet MAC address (not in the list above; removed as a device identifier) | `[MAC redacted]` |
| `fs/dmesg.txt` | 149 | as above, `pps_core` | `[email redacted]` |
| `fs/dmesg.txt` | 240 | as above, `device-mapper` | `[email redacted]` |
| `fs/dmesg.txt` | 319 | as above, the MAC address | `[MAC redacted]` |

No tailnet name or address, LAN address, provider hostname or credential was
found. The kernel's own build string (`buildd@...`, Ubuntu's builder) and
systemd unit names such as `modprobe@loop.service` are not email addresses
and are kept, as is the root filesystem's UUID.
