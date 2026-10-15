#!/usr/bin/env python3
"""Rerun the GTE adapter and CPU/GTE probes with the gte-multicycle enable.

The adapter probe's reference engine ticks on the same strict schedule, so
reads, busy timing and command results must still match exactly; the CPU
probe checks executed GTE programs end to end.
"""
from pathlib import Path

from prepare_gte_multicycle import adapter_text, STRICT
from prepare_resource_targets import digest
import run_cpu_gte_adapter
import run_gte_adapter


def transform(sources):
    adapters = [s for s in sources if Path(s['path']).name == 'laxer_mister_adapter.sv']
    if len(adapters) != 1: raise SystemExit('Expected one GTE adapter source')
    path = Path(adapters[0]['path']); path.write_text(adapter_text(path.read_text()))
    if STRICT not in path.read_text(): raise SystemExit('Strict enable missing')
    adapters[0]['sha256'] = digest(path)


def main():
    run_gte_adapter.main('gte-multicycle', transform, ('-DSTRICT_ENABLE',))
    run_cpu_gte_adapter.main('cpu-gte-multicycle', transform)
    print('GTE multicycle enable: adapter and CPU/GTE probes pass')


if __name__ == '__main__': main()
