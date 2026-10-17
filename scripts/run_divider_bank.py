#!/usr/bin/env python3
"""Check packed signed divider data, exact completion timing and restart.

Two selected widths, independent integer arithmetic, all directed sign/zero
edges and random restarts. Actual data/completion mutations must fail. This
does not replace the CPU/GPU consumer comparisons or physical timing checks.
"""
import json
from pathlib import Path
import random
import re
import shutil
import subprocess
import tempfile

from prepare_divider_bank import DIVIDER_SHA
from prepare_resource_targets import digest, UPSTREAM
from tool_paths import ROOT, find_tool


def vectors(n, d):
    rng = random.Random(1317)
    edges_n = [0, 1, 2, (1 << (n-1))-1, -(1 << (n-1)), -1, -2]
    edges_d = [0, 1, 2, (1 << (d-1))-1, -(1 << (d-1)), -1, -2]
    pairs = [(a, b) for a in edges_n for b in edges_d]
    pairs += [(rng.randrange(-(1 << (n-1)), 1 << (n-1)),
               rng.randrange(-(1 << (d-1)), 1 << (d-1))) for _ in range(500)]
    text = []
    for i, (a, b) in enumerate(pairs):
        if b:
            q = abs(a) // abs(b)
            if (a < 0) != (b < 0): q = -q
            rem = a - q*b
        else:
            # Preserve the upstream narrow accumulator's zero-divisor result:
            # each numerator bit above its width clears the corresponding
            # quotient bit. For equal widths this is the usual all-ones value.
            q = (~(abs(a) >> d)) & ((1 << n)-1)
            if a < 0: q = -q
            rem = a
        abort = rng.randrange(1, 10) if i >= 49 and i % 7 == 0 else 0
        gap = rng.randrange(0, 8)
        text.append(f'{a & ((1<<n)-1):0{(n+3)//4}x} {b & ((1<<d)-1):0{(d+3)//4}x} '
                    f'{q & ((1<<n)-1):0{(n+3)//4}x} {rem & ((1<<d)-1):0{(d+3)//4}x} {abort} {gap}\n')
    return ''.join(text)


def main():
    parent = ROOT / 'build/sim/divider-bank'; parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='run-', dir=parent)); print(directory, flush=True)
    sources = [UPSTREAM / 'rtl/divider.vhd', ROOT / 'rtl/pocket/psx_divider_bank.vhd',
               ROOT / 'sim/reference/tb_divider_bank.vhd', Path(__file__)]
    if digest(sources[0]) != DIVIDER_SHA: raise ValueError('Pinned divider source differs')
    protected = {str(p): digest(p) for p in sources}
    for i, p in enumerate(sources):
        saved = directory / 'input-archive' / str(i) / p.name
        saved.parent.mkdir(parents=True); shutil.copyfile(p, saved)
        if digest(saved) != protected[str(p)]: raise ValueError('Divider input changed')
    raw = sources[1].read_text(); results = []
    faults = {
        'bad-bit': ("r_next(0) := work_q(work_q'left);", "r_next(0) := not work_q(work_q'left);", 'Reference result mismatch'),
        'bad-done': ('if digit = 0 then', 'if digit = 1 then', 'Done mismatch'),
    }
    for n, d in ((33, 33), (45, 25)):
        for mode in ('positive', *faults):
            folder = directory / f'{n}-{d}-{mode}'; folder.mkdir()
            text = raw; expected = ''
            if mode in faults:
                before, after, expected = faults[mode]
                if text.count(before) != 1: raise ValueError('Divider fault anchor changed')
                text = text.replace(before, after)
            candidate = folder / 'divider.vhd'; candidate.write_text(text)
            (folder / 'vectors.hex').write_text(vectors(n, d))
            ghdl = find_tool('ghdl'); log = ''
            for cmd in (['-a', '--std=08', str(sources[0])],
                        ['-a', '--std=08', '--work=candidate', str(candidate)],
                        ['-a', '--std=08', str(sources[2])], ['-e', '--std=08', 'tb']):
                r = subprocess.run([ghdl, *cmd], cwd=folder, capture_output=True, text=True, timeout=120)
                log += r.stdout + r.stderr
                if r.returncode:
                    (folder / 'build.log').write_text(log)
                    raise RuntimeError('Divider fixture did not compile: ' + str(folder))
            (folder / 'build.log').write_text(log)
            r = subprocess.run([ghdl, '-r', '--std=08', 'tb', f'-gN={n}', f'-gD={d}',
                                '--assert-level=error', '--ieee-asserts=disable-at-0'], cwd=folder,
                               capture_output=True, text=True, timeout=120)
            output = r.stdout + r.stderr; (folder / 'run.log').write_text(output)
            passed = r.returncode == 0 and 'PASS packed divider' in output
            good = passed if mode == 'positive' else r.returncode != 0 and expected in output
            results.append(dict(n=n, d=d, mode=mode, expected_verdict_matched=good,
                                exit_code=r.returncode, log=str(folder / 'run.log'),
                                log_sha256=digest(folder / 'run.log'),
                                metrics={k: int(v) for k, v in re.findall(r'(completed|aborted)=([0-9]+)', output)}))
            print(('PASS ' if good else 'FAIL ') + f'{n}/{d} {mode}', flush=True)
    unchanged = all(digest(Path(p)) == sha for p, sha in protected.items())
    good = unchanged and all(r['expected_verdict_matched'] for r in results)
    (directory / 'report.json').write_text(json.dumps(dict(passed=good, results=results,
        source_identity_verified=unchanged, protected_sha256=protected,
        ghdl_version=subprocess.check_output([find_tool('ghdl'), '--version'], text=True).splitlines()[0],
        partial_busy_quotient_equivalence=False, CPU_GPU_consumers_verified=False,
        physical_timing_verified=False, hardware_verified=False), indent=2) + '\n')
    raise SystemExit(0 if good else 1)


if __name__ == '__main__': main()
