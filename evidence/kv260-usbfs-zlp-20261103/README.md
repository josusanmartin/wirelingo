# KV260 runs of the full-speed data device, 2026-11-03 (orchestrator)

Evidence level: **RTL in FPGA fabric**. KV260, app protoemu48 (RTL `60b0692`,
47.998 MHz), `firmware/usb_fs_dev.pasm` on core 0, `pe.usbfsdata` on the SPI
port, one host (the ZynqMP's xHCI under Linux), one hub (USB5744, the device
behind its transaction translator at full speed). Not silicon.

Run by the orchestrator on the Kria; copied here verbatim from the verifier's
evidence folder (`protoemu-verify-reports/evidence/032/`: `nodata/` and
`fstall-fix-20261103.tar.gz`, sha256 `f88fe590…a9400d`, which also holds the
xHCI traces, usbmon text and kernel logs of the stalled runs).

## With the fixed software (branch `usbfs-zlp` at 06b0dd4, in `~/usbfix` on the Kria)

| file | run | result |
|---|---|---|
| `fixnodata.log` | `nodata2.py`, 1000 zero-length vendor writes (0x40, 0x10, wLength 0), 1 s timeout each, on a configured device | 1000 ok, 0 timed out; latency min 0.69, median 0.85, max 1.87 ms |
| `fixread10.log` | `nodata2.py`, 10 zero-length vendor reads | 10 ok; median 0.81 ms |
| `fixread200.log` | `nodata2.py`, 200 zero-length vendor reads | 200 ok; min 0.56, median 0.74, max 1.46 ms |
| `fix200.log` | 200 attaches, `usb_fs_data_demo.py --sizes 8 --rounds 1 --attaches 1 --timeout 7` | 6 not configured (-110): runs 19, 20, 21, 80, 133, 161 |
| `quirkm200.log` | the same 200 attaches with the usbcore quirk `1209:5634:m` | 2 not configured: runs 40, 146 |
| `stalls/fix-demo-*.log`, `stalls/quirkm-demo-*.log` | the demo log of each of those 8 runs | all the same: requests `…0900`, `…1200`, `0009010000000000`; `F_SET 0x10`; core 0 TX FIFO 2 words, RX FIFO 0, PC 4; the software staged `(11, b'')` (the status ZLP, DATA1); 0 packets re-staged |

The read runs were started with `NODATA_RT=0xC0 NODATA_RQ=0x11` (as the
orchestrator recorded); `nodata2.py` does not print RT/RQ, so their logs do not
show the direction themselves.

## Before the fix

| file | run | result |
|---|---|---|
| `nodata1000.log`, `kernel-19h05-19h25.txt` | 19:10 UTC, zero-length writes with the unpatched software (the script variant used is not kept) | request 1 EPIPE, request 3 timed out; then "xHCI host not responding to stop endpoint command … HC died" (19:10:27), every later request errno 19 |
| `nodata300.log` | 19:41 UTC, after the xhci-hcd unbind/bind, `nodata.py` (patches the double ZLP at run time) with `NODATA_N=300` | 300 ok; min 0.58, median 0.74, max 1.90 ms |

## sha256

```
0d54945f5b35b7b1f3bc40f48057cff5d13d674c91a1a998551b53c7d06d1af6  fix200.log
f8c485134808a315f81110cbdd7a4cc58dfc3a71ad1fb1705120ba7c5fb0b446  fixnodata.log
65dba562dcf89c398a1fa2caea954fa7cc2a0ee3c1a9a3a401042c8274659d5d  fixread10.log
77feed374c87f250ff114f46d1345c79719db314c0033b052332a37939d9775d  fixread200.log
410b9102d48281b680a12aa29995e498fd51de5645c96ff960911df8dd9494df  quirkm200.log
7b51ab063a7f7f6ce7d69dc89981e06dd8f42c3582c3270015e3b0ca2ed10949  nodata2.py
14bd105f0e420a0d1d8f380d94f8e1c4d2292abfceeb43017174b9d191138010  nodata1000.log
03755aba33f8f4c632b25464e7fa877a56f6ad63fad9fef24c6a1f70358c9797  kernel-19h05-19h25.txt
be92d1c8558618e63b06e142f6b4aa0678125051841c1c7ede3ee360d0531318  nodata300.log
5dbbdafc0a239173aa6e9ab5db072ec53576bd76f5fe0d53fe6f1cc825f8a352  nodata.py
5f42d70130f95ab3e1cb5a1c80d1806aa9badb648a4d3dec4c6ac3cc62401c59  stalls/fix-demo-19.log
5f42d70130f95ab3e1cb5a1c80d1806aa9badb648a4d3dec4c6ac3cc62401c59  stalls/fix-demo-20.log
b5d97049d4550326473701197724d305835c4aa5fe7d62330716b36b1a0ed22b  stalls/fix-demo-21.log
2c8b63a02d0cc9a41ee8d85f68b0bf5a9f974289f2b493c722ad9a5659a78f95  stalls/fix-demo-80.log
32b87c2d3bdb7848dd37a4d2fd5b22a0ed174b585e3dcda2f15df3856cd1a83b  stalls/fix-demo-133.log
2c8b63a02d0cc9a41ee8d85f68b0bf5a9f974289f2b493c722ad9a5659a78f95  stalls/fix-demo-161.log
269f2f3f9a56bb435e3cd98c113381020e28045ca23f428bb9a106991be4a4a9  stalls/quirkm-demo-40.log
045c41d5e331c5c6d1a74df0777386e5b9c2a102ce34f21b5ca8770bc5b24f2b  stalls/quirkm-demo-146.log
```

(Identical hashes are identical logs: those runs printed the same clock.)
