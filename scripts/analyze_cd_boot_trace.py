#!/usr/bin/env python3
"""Summarize private CD boot-event CSVs and manually reviewed frame milestones.

Consumes the existing instrumented reference's CSV format. This does not run
RTL, discover screen contents or certify hardware timing. No media are copied.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path


CLOCK_HZ = 33_868_800
FILES = {'commands': 'boot-commands.csv', 'requests': 'ffvii-cd-requests.csv',
         'sectors': 'ffvii-sector-trace.csv', 'frames': 'ffvii-frame-trace.csv'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path, minimum):
    data = list(csv.reader(path.open()))
    if not data or any(len(row) < minimum for row in data):
        raise ValueError(f'incomplete trace: {path}')
    times = [int(row[0]) for row in data]
    if times != sorted(times):
        raise ValueError(f'non-monotonic trace: {path}')
    return data


def analyze(directory, milestones, clock_hz=CLOCK_HZ):
    if clock_hz <= 0:
        raise ValueError('clock must be positive')
    inputs = {name: directory / filename for name, filename in FILES.items()}
    commands = rows(inputs['commands'], 3)
    requests = rows(inputs['requests'], 3)
    sectors = rows(inputs['sectors'], 3)
    frames = rows(inputs['frames'], 7)
    by_frame = {int(row[0]): row for row in frames}
    if len(by_frame) != len(frames):
        raise ValueError('duplicate frame number')
    points = []
    for name, frame in milestones:
        if frame not in by_frame:
            raise ValueError(f'milestone {name} missing frame {frame}')
        tick = int(by_frame[frame][1])
        points.append(dict(label=name, frame=frame, tick=tick, seconds=tick / clock_hz))
    if len(points) < 2 or any(a['tick'] >= b['tick'] for a, b in zip(points, points[1:])):
        raise ValueError('at least two strictly ordered milestones required')
    start, end = points[0]['tick'], points[-1]['tick']
    selected = lambda data: [r for r in data if start <= int(r[0]) <= end]
    req_window = selected(requests)
    cuts = [start] + [int(row[0]) for row in req_window] + [end]
    gaps = sorted((dict(start_tick=a, end_tick=b, seconds=(b-a) / clock_hz)
                   for a, b in zip(cuts, cuts[1:]) if b > a), key=lambda g: g['seconds'], reverse=True)
    report = dict(scope='instrumented software-reference boot only', hardware_verified=False,
                  matched_rtl_boot=False, clock_hz=clock_hz,
                  input_sha256={name: digest(path) for name, path in inputs.items()},
                  event_counts={k: len(v) for k, v in [('commands', commands), ('requests', requests), ('sectors', sectors)]},
                  milestones=points,
                  interval_seconds=(end-start) / clock_hz,
                  interval_event_counts=dict(commands=len(selected(commands)), requests=len(req_window), sectors=len(selected(sectors))),
                  largest_request_gaps=gaps[:5],
                  command_counts=dict(sorted(Counter(f'{int(r[2], 16):02x}' for r in selected(commands)).items())),
                  sampled_cpu_pcs=Counter(r[2] for r in frames if start <= int(r[1]) <= end).most_common(8))
    wait_path = directory / 'guest-wait.csv'
    if wait_path.exists():
        waits = rows(wait_path, 6)
        grouped = {}
        for row in waits:
            frame, tick, pc, counter, target, ra = row
            if start <= int(tick) <= end and 0x801b7f64 <= int(pc, 16) < 0x801b7fec and int(counter) < int(target):
                grouped.setdefault((counter, target, ra), []).append(row)
        report['input_sha256']['wait_observer'] = digest(wait_path)
        report['rayman_wait_samples'] = [dict(counter=int(key[0]), target=int(key[1]), return_pc=key[2],
                                                  first_frame=int(data[0][0]), last_frame=int(data[-1][0]),
                                                  samples=len(data), first_last_span_seconds=(int(data[-1][1])-int(data[0][1])) / clock_hz)
                                            for key, data in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)[:5]]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--milestone', action='append', required=True, help='Reviewed screen label=frame; at least two')
    parser.add_argument('--clock-hz', type=int, default=CLOCK_HZ)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    points = [(name, int(frame)) for name, frame in (value.rsplit('=', 1) for value in args.milestone)]
    report = analyze(args.directory.resolve(), points, args.clock_hz)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(f"{points[0][0]} -> {points[-1][0]}: {report['interval_seconds']:.3f} emulated seconds")
    print(f"events: {report['event_counts']}; largest request gap: {report['largest_request_gaps'][0]['seconds']:.3f}s")


if __name__ == '__main__':
    main()
