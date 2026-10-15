#!/usr/bin/env python3
"""Summarize CPU-edge conditions between observed 640x1 upload headers.

This consumes the read-only observer's same-model proof. Conditions are not
instruction retirement, accepted GPU commands or physical bus utilization.
"""
import argparse
import bisect
import csv
import json
from pathlib import Path
import statistics

from prepare_resource_targets import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    proof_path = args.observer / 'report.json'
    proof = json.loads(proof_path.read_text())
    if not proof['passed'] or not proof.get('cpu_pipeline_observer'):
        raise ValueError('Require a passing same-model CPU observer proof')
    event_path = args.observer / 'observed/hardware-events.csv'
    cpu_path = args.observer / 'observed/cpu-conditions.csv'
    for path in (event_path, cpu_path):
        if digest(path) != proof.get('observer_trace_sha256', {}).get(path.name):
            raise ValueError('Observer trace identity differs: '+path.name)
    with event_path.open() as file:
        events = list(csv.DictReader(file))
    words = [row for row in events if row['event'] == 'CPU_GP0_LATCH']
    headers = []
    for i, row in enumerate(words[:-2]):
        if int(row['value_hex'], 16) != 0xa0000000:
            continue
        xy, wh = (int(words[j]['value_hex'], 16) for j in (i+1, i+2))
        if wh != 0x00010280 or xy & 0xffff:
            continue
        headers.append({'cycle': int(row['cycles']), 'row': xy >> 16})
    with cpu_path.open() as file:
        cpu = list(csv.DictReader(file))
    cycles = [int(row['cycles']) for row in cpu]
    if not cycles or any(b != a+1 for a, b in zip(cycles, cycles[1:])):
        raise ValueError('CPU conditions must cover contiguous CPU edges')
    intervals = []
    for a, b in zip(headers, headers[1:]):
        if b['row'] != a['row']+2 or b['cycle'] > cycles[-1]+1:
            continue  # omit sweep boundaries and incomplete observation edges
        ticks = b['cycle']-a['cycle']
        rows = cpu[bisect.bisect_left(cycles, a['cycle']):bisect.bisect_left(cycles, b['cycle'])]
        if len(rows) != ticks:
            raise ValueError('Incomplete upload interval')
        counts = {key: 0 for key in ('unstalled','stage1','stage2','stage3','stage4','dma',
            'paused','memory_busy','memory_busy_data','memory_busy_fetch','cache_hit',
            'request_not_done','gpu_dma_active','dma_interval_ticks','outside_dma_ticks',
            'outside_dma_unstalled','outside_dma_stage1','outside_dma_stage2',
            'outside_dma_stage3','outside_dma_stage4','outside_dma_dma')}
        for row in rows:
            if row['enabled'] != '1':
                raise ValueError('Interval includes disabled/reset CPU')
            stall = int(row['stall_hex'], 16)
            # Same priority as the upstream debug counters; retain overlap
            # information separately in the source CSV rather than summing it.
            priority = next((name for mask, name in ((16,'dma'),(8,'stage4'),(4,'stage3'),
                (2,'stage2'),(1,'stage1')) if stall & mask), 'unstalled')
            counts[priority] += 1
            counts['paused'] += int(row['paused'])
            busy = int(row['memory_busy'])
            counts['memory_busy'] += busy
            counts['memory_busy_data' if row['memory_is_data']=='1' else 'memory_busy_fetch'] += busy
            counts['cache_hit'] += int(row['cache_hit'])
            counts['request_not_done'] += row['request']=='1' and row['done']=='0'
            active = row['dma_gpu_active']=='1'
            counts['gpu_dma_active'] += active
            counts['dma_interval_ticks' if active else 'outside_dma_ticks'] += 1
            if not active and 'outside_dma_'+priority in counts:
                counts['outside_dma_'+priority] += 1
        intervals.append({'from_cycle':a['cycle'], 'row':a['row'], 'ticks':ticks, **counts})
    if not intervals:
        raise ValueError('No complete within-sweep 640x1 intervals')
    keys = [key for key in intervals[0] if key not in ('from_cycle', 'row')]
    result = {'observer_report_sha256':digest(proof_path),
        'input_sha256':{str(p):digest(p) for p in (event_path,cpu_path,Path(__file__))},
        'intervals':intervals,
        'medians':{key:statistics.median(row[key] for row in intervals) for key in keys},
        'interval_count':len(intervals),
        'condition_semantics':[
            'Priority partition of CPU stall bits: DMA, stage4, stage3, stage2, stage1, unstalled',
            'memory_busy covers latched CPU reads while request may be deasserted',
            'gpu_dma_active indicates channel activity, not GPU backpressure',
            'unstalled samples are not retired-instruction counts',
            'GPU headers are CPU input-latch observations, not decoder acceptance'],
        'game_correctness_verified':False, 'hardware_verified':False}
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'interval_count':len(intervals),'medians':result['medians']},indent=2))


if __name__ == '__main__':
    main()
