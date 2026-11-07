# The re-stage and abandoned-stage races, per SPI access, 2026-11-04

Evidence level: **model** (the cycle model, with `pe.usbdev.DeviceSoftware`
running over a modelled SPI port concurrently with the bus). Not RTL, not the
board, not silicon. Run on runner1 from `git archive` copies of each commit.

Each line is one case: the software held at the n-th SPI access, the host's
SETUP (SET_CONFIGURATION) landing while it is held, whole ("after the SETUP")
or token first ("after its token"), then the status stage classified:
**right** (one zero-length DATA1 after NAKs only, then a whole descriptor
read), **silent** (unanswered INs), **stale** (the abandoned DATA), garbage
or ERROR. `held` is the access held (2050 = A_FSET, 2051 = A_FCLR, 1033 =
core 0's STATUS, 1040 / 1072 = core 0 / core 1's TX FIFO). `overhead` is the
host software's clocks per SPI transaction (48 = 1 us; 2400 = 50 us, the
board's order).

| file | commit | what | result |
|---|---|---|---|
| `table-aacdc4c.txt` | aacdc4c | low-speed two-core re-stage (`test_usb_ls_restage`), n = 0-25 x both resumes, 1 us per access (the earlier `ls_case.py`, no overhead argument) | 52 of 52 right: at 1 us the status IN never lands between the FSET and the check after it |
| `table-restage-dc51cc8.txt` | dc51cc8 (before the fix) | the same, at 1 us and 50 us | 101 of 104 right; **stale** at 50 us: n = 8 (the pre-FSET check's RX-level read) token first, n = 9 (the FSET) both resumes. Claim 8 reproduced |
| `table-abort-dc51cc8.txt` | dc51cc8 | a normal stage abandoned by a new SETUP (`test_usb_stage_abort`), n = 0-15 x both speeds x both resumes x 1 and 50 us | full speed: **stale** n = 0-1, partial packet (F_UNDERRUN) n = 2-4, at both costs; low speed: **stale** n = 0-5 at 50 us, bad CRC n = 0-2 (after the SETUP) at 1 us. Verifier 044 note 3 reproduced |
| `table-8fdda85.txt` | 8fdda85 (the fix) | both tables | **232 of 232 right**, no unanswered IN |
| `turnaround-dc51cc8.txt`, `turnaround-8fdda85.txt` | dc51cc8, 8fdda85 | `measure_ls_ta.py`: the two-core device's ACK / NAK / DATA turnarounds, 8 each | 2.312 bit times for all three, before and after (identical files) |

**Commit names.** The tables ran on the branch before it was rebased onto
usb-guard's 87d72ae. After the rebase: 7c3a039 → 8be2fa6, aacdc4c → 38233ee,
dc51cc8 → e59e368, 8fdda85 → ee15b1a, d5b8944 → 6f8562a. `git diff` between
each pair shows only 87d72ae's own changes (`fpga/kv260/host/usb_guard_run.py`,
`test_usb_guard_run.py`, `test_doc_guard_labels.py`, one mutant entry), none
in `tools/`, `firmware/` or the test modules these tables ran.

Scripts: `ls_case.py` runs one case (`<tree> <resume 0|1> <n> <module> <function> <overhead>`);
`jobs.sh` (dc51cc8: the abort table, and the re-stage step that named
`ls_case.py` by a wrong path), `jobs2.sh` (dc51cc8: the re-stage table, rerun),
`jobs3.sh` (8fdda85: both tables) ran them with `xargs -P`. The test module
runs one abort case itself: `python test/model/test_usb_stage_abort.py <speed> <resume> <n> <overhead>`.

## sha256

```
24b8f118d3bd806dce9f7d72c5121600023e6488e8ab92b2b58f17b40688623b  table-aacdc4c.txt
5d6d86c53ab57274f6e63287ce4556cd868997a1a25fc5ed96fe00e0355ac250  table-restage-dc51cc8.txt
f7fd80fb4ecac87b481401e965f5c2ef53ed386174c5013afd4fcd37d934bbb8  table-abort-dc51cc8.txt
93200fddd4620b2f138d32cfc5949efd34b58d3a24fd97ee34b63e8a2916dbf5  table-8fdda85.txt
3b7394eb8105a9de050958cf193ed7b70259a97f996c21b5c1c50198b3adcac5  turnaround-dc51cc8.txt
3b7394eb8105a9de050958cf193ed7b70259a97f996c21b5c1c50198b3adcac5  turnaround-8fdda85.txt
c01a3ce87fe38d84b3b29643c18687da5d14a2193b1994999b4b57eca9bbf5b4  ls_case.py
3b8bce99befc2e6bba12649b2a0b9f56193284b0edde366099f474e99ce7e763  measure_ls_ta.py
26643cde47c38a41634eae6ad938eb5982a23029b5a52e5ef47426dbcce78952  jobs.sh
0554fce35f9bc19cd8964b294abd23f77c285db9d5ccf9365c958745727d8f31  jobs2.sh
2055e92b49e50a0f5047476d8e3679f7ba998a1622ef7d3f696ea1bf44c20af6  jobs3.sh
```
