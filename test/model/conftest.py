"""Say when sigrok's checks did not run: they skip without sigrok-cli, and a
line of dots with a skip count hides which ones (docs/bench-firmware.md)."""


def pytest_terminal_summary(terminalreporter):
    n = sum("sigrok-cli not found" in str(r.longrepr) for r in terminalreporter.stats.get("skipped", ()))
    if n:
        terminalreporter.write_line(f"SIGROK CHECKS SKIPPED: {n} tests: a pass here says nothing about them "
                                    "(set SIGROK_CLI, flow/install_sigrok.sh)")
