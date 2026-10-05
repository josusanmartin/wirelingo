"""Bounded, read-only board transport measurement. Does not run on the host.

Record returned data with actual board/SDK revisions and measured clocks.
Repeated READ_FIXED of ID has no FIFO/core side effects. These transaction
latencies do not establish a bound on a concurrent USB/Ethernet service loop.
"""


def run(pe, board_revision, sdk_revision, repeats=100, sizes=(1, 8, 64, 256)):
    import sys
    import time
    if not board_revision or not sdk_revision:
        raise ValueError('record board and SDK revisions')
    sizes = tuple(sizes)
    if not isinstance(repeats, int) or not 1 <= repeats <= 1000 or not sizes or len(sizes) > 16:
        raise ValueError('benchmark must be bounded')
    if any(not isinstance(n, int) or not 1 <= n <= 4096 for n in sizes):
        raise ValueError('burst sizes must be in 1..4096')
    rows = []
    for n in sizes:
        latencies = []
        for _ in range(repeats):
            start = time.ticks_us()
            words = pe.read(0x800, n, fixed=True)
            elapsed = time.ticks_diff(time.ticks_us(), start)
            words = [words] if n == 1 else words
            if len(words) != n or any(w != 0x50450002 for w in words):
                raise RuntimeError('ID burst corrupted')
            if elapsed <= 0:
                raise RuntimeError('timer resolution insufficient')
            latencies.append(elapsed)
        ordered = sorted(latencies)
        rows.append(dict(words=n, samples_us=latencies,
                         median_us=ordered[len(ordered) // 2],
                         p99_us=ordered[(99 * (len(ordered) - 1) + 99) // 100],
                         max_us=ordered[-1],
                         payload_bytes_per_second=4 * n * repeats * 1000000 // sum(latencies)))
    bus = pe.transport
    return dict(kind='board READ_FIXED ID benchmark', board_revision=board_revision,
                sdk_revision=sdk_revision, micropython=str(sys.implementation),
                configured_project_hz=bus.project_hz if bus else None,
                configured_sck_hz=bus.sck_hz if bus else None,
                system_hz=bus.system_hz if bus else None,
                pio_divider_q8=bus.divider if bus else None,
                pio_gpio_base=bus.gpio_base if bus else None,
                out_gpio=bus.out_gpio if bus else None,
                in_gpio=bus.in_gpio if bus else None,
                sck_gpio=bus.sck_gpio if bus else None,
                cs_gpio=bus.cs_gpio if bus else None,
                measured_project_hz=None, measured_sck_hz=None,
                input_sync_and_pad_margin='unmeasured', rows=rows)
