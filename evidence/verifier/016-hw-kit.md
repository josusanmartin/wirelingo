Commit: 49a2d89  Base: ab6880d  Branch: hw-kit
Item: docs/hardware-day.md, fpga/kv260/host/hw_day.py, test/model/test_hw_day.py. Do not touch the Kria.
A person with no electronics background will follow this at real hardware, so treat a safety error as blocking.
Claims:
1. Every step that enables a pin prints the wiring and needs a typed confirmation, and a step refuses to run before its prerequisite is confirmed. Try to reach a pin enable by any route, including a crash, a repeated step, a stale state file and an invalid argument.
2. The runbook's order is right: the isolated Pi cable before the switch, with receive, cut and ping last. Attack this: is any earlier step able to damage the board?
3. Every pin number and breadboard location is checked against fpga/kv260/kv260.xdc and the README's J2 numbering, or marked CHECK BEFORE WIRING. Look for a pin stated as fact with no source. The USB wiring removal comes before any Ethernet wiring.
4. Commands exist and take the arguments shown, against the repo. eth-stack files are not on this branch: check that this is handled.
5. Estimates are labelled, including drive levels against squelch. Nothing runs pmod_test.py or touches eth0, reboots, or runs two processes. The hard rules in RULES.md are all stated.
6. 29/29 mutants killed; model suite 1805 passed.
Known facts from the orchestrator, for you to compare: protoemu40 was installed on the Kria on 2026-10-30, 12 mA PMOD drive, 39.9997 MHz.
