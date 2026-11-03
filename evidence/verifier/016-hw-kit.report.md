VERDICT: FAIL

Commit 49a2d89, base ab6880d. The launcher's main safety design is good: no route I tried enabled a pin without the typed phrase, and the pins always came off. But the runbook fails its own USB rule at step 16, every Pi command crashes as the runbook installs it, and the setup commands stop at their second line. The stated mutant result also cannot be reproduced. Nothing here was built or run on the Kria.

| Claim | Result |
|---|---|
| 1. Pins need wiring shown and a phrase; prerequisite enforced | Phrase: PASS. Prerequisite: **FAIL**, direct needs only (finding 4). `cut` and `ping` do not print wiring (note 12) |
| 2. Order right, isolated Pi cable before switch, receive, cut and ping last | PASS for the stated order; one risk reason is wrong (note 9) |
| 3. Pins sourced; USB removal before any Ethernet wiring | Pins PASS. USB removal **FAIL** (finding 1) |
| 4. Commands exist and take the arguments shown; eth-stack handled | **FAIL** (findings 2, 3). eth-stack refusal PASS |
| 5. Estimates labelled; pmod_test, eth0, reboot, two processes; hard rules stated | Estimates and reboot PASS. **FAIL** on two hard rules (finding 5) |
| 6. 29/29 mutants; model 1805 | Model PASS. Mutants **unproved** (finding 6) |

## Blocking

1. `docs/hardware-day.md:1011-1015`, safety: step 16 `rx` never removes step 15's USB wiring.
   - Step 15 (`:963-970`) puts on J2 silkscreen 1 to a30, 3 to a20, 9 to b24, 11 to i3, the black wire at a24, and the USB plug in the Pi. It ends by taping the black wire, and says nothing else.
   - Step 16's "Remove first" lists only the three 1 kohm. "Before you start" lists "the UART, SPI and I2C wires". USB is not mentioned, and "Must stay disconnected" does not list the plug either, though `:765` does for `spi`.
   - That breaks Rule 9 (`:52`) and the item's "USB wiring removal comes before any Ethernet wiring". Rule 9 exists because transmit uses the same pins as D+ and D-.
   - The launcher cannot catch it: `usb-off` stays confirmed after `usb-pi`. `test_the_usb_wiring_comes_off_before_any_wire_goes_on_j2` only checks `usb-off` is a prerequisite, so it passes.
   - J2 holds one wire per pin, so a careful person will probably move the jumpers. That is luck, not instruction. A beginner can pull the J2 end and leave the rest, with the USB plug live in the Pi.
   - Fix: add the full removal to step 16, USB plug out first, and make `hd run usb-pi` clear the `usb-off` confirmation so `usb-off` must be repeated.

2. `fpga/kv260/host/hw_day.py:49`: **every Pi command crashes as the runbook installs it.** `ROOT = HERE.parents[2]` runs at import. The runbook copies the file to the Pi's home (`docs/hardware-day.md:465`, `:480`). `/home/pi` has only two parents.
   - I ran the file's source with that path: `/home/pi/hw_day.py -> IndexError 2`, and the same for `/home/someone/hw_day.py`. `/home/pi/hwday/hw_day.py` loads.
   - `pi-watch` at step 8 runs under `nohup` with output to `watch.out` (`:602`), so the crash is silent. The person moves the cable and runs two minutes of `tx-pi` with no watch. `pi-send` is lost too, so `rx`, `cut` and `ping` cannot run.
   - No test runs the file from outside the tree. Fix: work out `ROOT` only where it is used, and add a test that loads the file from a two-level path.

3. `docs/hardware-day.md:109`: the setup fails at its second command. `rsync` creates only the last directory level, and nothing has made `hwday/fpga`.
   - Run locally, same arguments, rsync 3.2.7: `mkdir ".../hwday/fpga/kv260" failed: No such file or directory (2)`, rc 11. The first line had rc 0.
   - The launcher is what line 2 copies, so `hd` does not exist afterwards. The error is visible, but a person with no background has no fix. Use `--mkpath` if the Kria's rsync is 3.2.3 or later, otherwise `ssh ... mkdir -p hwday/fpga/kv260` first.
   - I could not check that the Kria has rsync at all.

4. `fpga/kv260/host/hw_day.py:284`: a prerequisite is checked one level deep, so claim 1 and the runbook's use of `hd forget` (`:122`, "when wiring changed") do not hold.

   | Confirmed in the state file | Step | Result |
   |---|---|---|
   | `tx-bench`, `pi-prep` only: no `usb-off`, `rails`, `preflight` | `tx-pi` | Pins enabled, rc 0 |
   | `tx-pi` only | `rx` | Pins enabled, rc 0 |

   So someone who puts USB back and honestly types `hd forget usb-off` is stopped at `rails` and nowhere else. Confirmations also never expire: `/var/tmp` survives between days, and the timestamp is written but never read. Fix: check the closure of `needs`, which the test file already has as `closure()`.

5. `docs/hardware-day.md:39`, `:42`: two hard rules are weakened, not stated.
   - RULES.md says "Never run pmod_test.py." Rule 5 says never run it "with any wiring in J2 but its own loopback jumpers", which permits it. The launcher itself never runs it, and refuses if it sees it.
   - RULES.md says "Never touch eth0." Rule 6 adds "Moving the far end of its cable is the one exception today", and step 4 does that, which drops the Kria's link while it is the only way in. The runbook gives no reason the Kria must move: `tx-switch` needs the chip's wires and the Pi on the switch, not the Kria.
   - That exception is the author's to grant, not the runbook's. Fix: get the author's decision. Otherwise leave the Kria's cable alone and take the step out.
   - `test_runbook_states_the_rules` only looks for keywords such as `eth0`, so it passes with both changes.

6. Claim 6, mutants: "29/29" has no artifact. The commit adds three files and none is a mutant script. `test/mutants.py` has no `hw_day` entry, and I found none in `protoemu-agent-tools` or the builder's runner1 checkout.

   I ran 33 of my own against `test/model/test_hw_day.py`: **27 killed, 6 survived.**

   | Survivor | What it shows |
   |---|---|
   | `hd off` writes nothing but still prints "every PMOD pin is off" | **The safety command in Rules 4, 11 and 12 has no test** |
   | `hd off` always refuses | The same |
   | `i2c-capture` drives both lines (`poe=0b11`) | The "input only" property is tested for `spi`, not here |
   | `tx-bench` enables pins before chip setup | Order not asserted |
   | A second signal raises during the turn-off | The Signals docstring's promise is untested |
   | `hd forget` does nothing | `forget` is untested |

   Commit the mutants, and add tests for `off` and `forget`.

## Should fix

7. `fpga/kv260/host/hw_day.py:354`, `:929`: `--active-app` overrides a readable listing. With the listing showing `k26-starter-kits` and `--active-app protoemu`, `run` opened the rig and `off` opened `Spim`. The runbook says reading those addresses can hang the board (`:343`), and a hung board ends the day. Use the flag only when the listing cannot be read, and refuse when it disagrees.

   Added after filing: RULES.md now says the Kria stopped responding on 2026-11-03 after a failed app load followed by a host-port measurement. I do not know the cause. It does bear on this runbook: every `hd run` with an action opens the rig and starts a host-port access once the listing shows a protoemu app, and `budget` is a host-port measurement. The launcher trusts `xmutil listapps`, so check what the listing shows after a failed load before this runbook is used. Treat this finding as blocking until that is known. The Kria is off limits, so I did not test it.

8. `fpga/kv260/host/hw_day.py:931`: if the listing can't be parsed, `hd off` says "no protoemu app is active, so PMOD isn't either: nothing to do" and writes nothing. That is a false reassurance at the moment of Rule 12, and the listing format is admitted to be from memory. It should say the app is unknown and name `--active-app`.

9. `docs/hardware-day.md:576`: "the pins are driven at the time" is wrong for the case that matters.
   - The Pi is connected before the launcher starts and stays connected after it ends (`:574`, `:616`), with the pins disabled. As I recall, Auto-MDIX hunts when there is no link, which is exactly then.
   - My estimate is that the risk is small: the pads are isolated and limited by 220 ohm per line, about 1 mA for under 100 ns if they clamp. But the reasoning is wrong, and Rule 2 requires it worked out. Redo it for disabled pins.

10. `docs/hardware-day.md:1033`, `:1036`: two resistors share row e over columns 21 to 25, 1 kohm `e19`-`e25` and 220 ohm `e21`-`e29`. No other step overlaps like this (checked by script). Their bare leads can touch. Pad lead on column 21 removes the divider, which the runbook says must not happen. 3.3 V lead on column 21 shorts 3.3 V towards GND through the Pi's transformer winding. This is from the geometry, not built. For a beginner, move one part so nothing overlaps.

11. `docs/hardware-day.md:296`, `:1166`:
    - The -0.57 V row is not compared with the absolute minimum. I recall that as -0.55 V for this bank, from memory, so it would be past it.
    - The end of the day says `hd off`, then pull J2 wires in any order. Unplugging the Pi's cable should come first: with J2 11 out and J2 7 still in, the bias is gone and the pad follows the pair through 1 kohm.

## Notes

12. Launcher odds and ends:
    - `cut` and `ping` print "Same as step rx -> nothing moves", not the wiring, so claim 1's "prints the wiring" is not literally true there.
    - A corrupt state file gives a raw traceback (`JSONDecodeError`, `TypeError`). No pin is reached, which is safe but unfriendly. A JSON list or string naming steps does pass the check; that is contrived.
    - `hd off` takes no lock while the runbook says one process. It writes only PMOD0 and PMOD1, so this looks harmless.
    - `ETH_STACK` omits `tools/pe/ethsim.py`, though `eth_tap` needs it. The setup line copies it. If it is missing, the step fails safely as FAILED.

## What held

- **Pin routes tried (claim 1):** a wrong or empty phrase, EOF, a closed stdin, invalid arguments (exit 2), a repeated step, a wrong app, a wrong clock divider, another board script, a second launcher, and a crash, Refused or signal in the action. None enabled a pin without the phrase, and pins always ended off.
- **Signal during the turn-off:** Stopped escapes, but `HwRig.close()` writes PMOD off again, and my probe confirms the pins end off. Not a defect.
- **No bypass of the guard:** nothing calls `rig.spim.pmod` with a mapping. `usb_demo` gets the guard. `Eth10Host` and `eth.py` never touch PMOD.
- **`cut` does not drive the divider:** on the model `uio_oe` is 0x2 at each burst, so uio0 stays an input. My `poe=0b11` mutant was killed.
- **Pins (claim 3):** silkscreen, `pmod[]` and package pins match `kv260.xdc` and the README. The README's 2026-10-30 log shows `columns normal`, so the column evidence is real. Rails are marked as community-sourced and checked by `rails`. Pi header figures agree with what I know of it. There are no pull-ups in the xdc.
- **Breadboard nets:** a script over every step's wiring found no column holding two nets. The `rx` divider gives 1.1 V and one third gain.
- **Arithmetic:** drive table, 48 V / 220 ohm, 1.5 kohm from three 1 kohm, and 12 mA as a legal LVCMOS33 drive.
- **eth-stack (claim 4):** refusal works. In a tree laid out like the Kria's, with `eth-stack`'s five files, every module imports and `load_writes` works at 40 and 50 MHz. The stand-ins in `test_ping_step_runs_the_station` match the real names: `configure`, `service`, `send`, `stats`, `icmp`, `arp`.
- **Evidence level:** states FPGA, not silicon, and nothing run on hardware. No first or only language. No SWD. Reboot rule stated. Commit trailer right.
- **Orchestrator's facts:** 2026-10-30 and 12 mA match and are marked as project notes absent from the repo. The doc does not give 39.9997 MHz. It is 7.5 ppm from nominal, inside the launcher's 200 ppm window, so `f_clk` is 40 MHz as `line_period` needs.

## Not checked

I built nothing and did not touch the Kria. DS925, UG1089 and the LAN datasheet figures were not rechecked against the sources. Python compatibility with the Kria's version was not tested.

## What I ran

- `remote_test.sh model`: `1805 passed, 28 skipped in 326.42s`, rc 0.
- My 33 mutants on runner1, in `verifier-016/m`. Two were rerun without `nohup`, which had been masking SIGHUP: `no-sighup` was then killed, so the 27 counts it.
- 17 launcher probes in a separate copy, `verifier-016/p`, using the commit's own fakes.
- Local: the setup's rsync lines against a scratch directory, the source run with `/home/pi/hw_day.py` as its path, a Kria-shaped tree with `eth-stack`'s five files, and the net script.
