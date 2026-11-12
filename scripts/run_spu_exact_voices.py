#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Restore gate for the exact-voices SPU save port (state format PXS9).

Bench: sim/pocket/tb_spu_exact_voices.vhd (derived from Codex's restore fixture
tb_spu_global_ring.vhd). Two SPUs with different histories are held at the quiet
point, A's state goes through the guest bus and the save port into B, both are
released on the same clock and compared on every clk1x cycle from the first one.

Hard on every cycle of every case (a mismatch fails the case):
  ctrl          spu_tick, sound_out_valid, sound_timeout, sampleticks, cdProcStep, CNT, mBASE
  irq, endx     irqOut, ENDX
  dma           spu_dmaRequest, STAT, dma_readdata
  ram_ctrl      every RAM access: ram_request/ram_Adr/ram_rnw/ram_done and the SDRAM pins
  ram_data      write data outside the reverb work area (mBASE*8 .. 0x7FFFF)
  capture_data  write data of the capture buffers (RAM 0x000-0xFFF)
  ram_read      read data outside the reverb work area
  state         every scalar the 8-word ring carries (cd_next, CURVOL, noise, ...)
  rec           voice record / volume / envelope counter RAM writes (strobe, address,
                data), decoded-sample RAM write strobes and address
  dry           soundleft/right and the reverb input sums before the reverb add
  bus_read      guest read data after release (transfer case)
  end state     ring, port words, decoded-sample pages, all guest registers, capture
                RAM read back, SPU RAM model outside the reverb work area
Not a mismatch: write and read data inside the reverb work area (counted), and PCM
before the first sample tick after release (the DAC latch is not restored).

Cases (each per seed):
  off          REVERBOFF = 1: PCM identical from the first sample
  cnt7off      SPUCNT bit 7 = 0 (no reverb RAM writes): see PCM_STALE_SAMPLES
  reverb       reverb on, short stable preset: dry path exact, |outA - outB| per
               64-sample window must shrink and end at or below --decay-threshold
  reverb-wild  reverb on, random reverb registers: hard checks only
  transfer     REVERBOFF = 1, guest traffic after release with manual FIFO writes,
               DMA writes and DMA reads
  abort        as off, with 3 abandoned ring reads on B before release (first seed)
Planted faults (first seed, configuration of case off), each must be caught:
  sample-word-dropped   one decoded-sample bank word not written to B
  cd-next-not-restored  B keeps its own cd_next_left/right (ring bits 210-243)
  voice-record-shifted  the records of two neighbouring voices swapped
  ring-wrap-mutation    SPU text mutation in B only: the ring index wraps at 6, not 7

Usage: run_spu_exact_voices.py [--spu build/ss/a4/save-states/spu.vhd]
       [--output DIR] [--seeds 1 2] [--samples N] [--decay-samples N] [--quick]
Run on the Laptop (one simulation at a time; this script runs them in sequence).
"""
import argparse
import hashlib
import json
import re
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_paths import ROOT, find_tool
from run_spu_gaussian_sum import MODEL as GAUSSIAN_MODEL

BENCH = ROOT / 'sim/pocket/tb_spu_exact_voices.vhd'
MODELS = ROOT / 'sim/reference/spu_base_trim_models.vhd'
UPSTREAM = ROOT / 'references/PSX_MiSTer/rtl'
TOP = 'tb_spu_exact_voices'
# With SPUCNT bit 7 = 0 and REVERBOFF = 0 the reverb output word of the side that the
# first sample does not compute (work RAM word 11 or 12, not carried by this port) is
# still mixed in: PCM may differ in the first sample only.
PCM_STALE_SAMPLES = 1
HARD = ['ctrl', 'irq', 'endx', 'dma', 'ram_ctrl', 'ram_data', 'capture_data', 'ram_read',
        'state', 'rec', 'dry', 'bus_read']
END = ['end_ring_diffs', 'end_port_diffs', 'end_sample_page_diffs', 'end_register_diffs',
       'end_capture_ram_diffs', 'end_ram_outside_diffs']
CASES = {
    'off':         dict(REVERB_OFF=1, CNT7=2, PRESET=0, TRAFFIC=0),
    'cnt7off':     dict(REVERB_OFF=0, CNT7=0, PRESET=0, TRAFFIC=0),
    'reverb':      dict(REVERB_OFF=0, CNT7=1, PRESET=1, TRAFFIC=0),
    'reverb-wild': dict(REVERB_OFF=0, CNT7=1, PRESET=0, TRAFFIC=0),
    'transfer':    dict(REVERB_OFF=1, CNT7=2, PRESET=0, TRAFFIC=1),
    'abort':       dict(REVERB_OFF=1, CNT7=2, PRESET=0, TRAFFIC=0, ABORT_WORDS=3),
}
FAULTS = {
    'sample-word-dropped':  dict(FAULT=1),
    'cd-next-not-restored': dict(FAULT=2),
    'voice-record-shifted': dict(FAULT=3),
    'ring-wrap-mutation':   dict(variant='ringwrap'),
}
# spu_ram.vhd evaluates the 24-voice hit compare of its DDR3 line cache on every clock.
# The result is only read in the useSDRAM = '0' state machine (CHECK_CACHE, and
# cache_invalidate is only set there), and the bench ties useSDRAM to '1'. In GHDL that
# dead compare is half of the run time, so the simulation copy skips it;
# --unmodified-ram runs the file as it is.
CACHE_OLD = '''         cache_voice_hit <= (others => '0');
         for i in 0 to 23 loop
            if (ram_Adr64 >= cache_voice_starts(i) and ram_Adr64 <= (cache_voice_starts(i) + 7)) then
               cache_voice_hit(i) <= cache_voice_valid(i);
            end if;
         end loop;
'''
CACHE_NEW = '''         cache_voice_hit <= (others => '0');
         if (useSDRAM = '0') then   -- simulation copy: see run_spu_exact_voices.py
         for i in 0 to 23 loop
            if (ram_Adr64 >= cache_voice_starts(i) and ram_Adr64 <= (cache_voice_starts(i) + 7)) then
               cache_voice_hit(i) <= cache_voice_valid(i);
            end if;
         end loop;
         end if;
'''
MUTATIONS = {
    'ringwrap': ('if global_scan_index = 7 then global_scan_index <= 0;',
                 'if global_scan_index = 6 then global_scan_index <= 0;'),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def without_debug_writer(text):
    """Drop the upstream debug file writer (the translate_off block with the goutput
    generate); the feed and restore assertions in the other blocks stay."""
    parts = re.split(r'(?m)^(\s*--\s*synthesis\s+translate_off\s*)$', text)
    # parts: [before, marker, body, marker, body, ...]
    out, dropped = [parts[0]], 0
    for k in range(1, len(parts), 2):
        body = parts[k + 1]
        end = re.search(r'(?m)^\s*--\s*synthesis\s+translate_on\s*$', body)
        if 'goutput' in body and end:
            out.append(body[end.end():]); dropped += 1
        else:
            out.append(parts[k] + body)
    if dropped != 1:
        raise ValueError(f'expected one debug writer block, found {dropped}')
    return ''.join(out)


def siblings(spu):
    """spu_ram, spu_gauss and the FIFO: upstream files, checked against the project's
    fit-inputs.json when the SPU lives in a frozen project."""
    files = {n: UPSTREAM / n for n in ('spu_ram.vhd', 'spu_gauss.vhd', 'SyncFifoFallThrough.vhd')}
    note = 'upstream references/PSX_MiSTer/rtl (no project manifest next to the SPU)'
    for parent in spu.parents:
        manifest = parent / 'fit-inputs.json'
        if manifest.is_file():
            rows = json.loads(manifest.read_text())['quartus_sources']
            for name, path in files.items():
                hits = [r for r in rows if Path(r['path']).name == name]
                if len(hits) != 1 or hits[0]['sha256'] != sha(path):
                    raise ValueError(f'{name} of {manifest} is not the upstream file')
            mine = [r for r in rows if Path(r['path']).name == 'spu.vhd']
            if len(mine) != 1 or mine[0]['sha256'] != sha(spu):
                raise ValueError(f'{spu} is not the spu.vhd selected by {manifest}')
            note = f'upstream files, equal to the sources selected by {manifest}'
            break
    return files, note


def build(ghdl, out, spu_text, files, variant, unmodified_ram):
    w = out / f'build-{variant}'
    w.mkdir(parents=True)
    text = without_debug_writer(spu_text)
    (w / 'spu.vhd').write_text(text)
    # B: the same text as entity spu_b; a mutation is planted in B only, so the
    # unmutated A is the reference.
    if variant != 'base':
        old, new = MUTATIONS[variant]
        if text.count(old) != 1:
            raise ValueError(f'mutation anchor changed: {old}')
        text = text.replace(old, new)
    (w / 'spu_b.vhd').write_text(re.sub(r'\bspu\b', 'spu_b', text))
    ram = files['spu_ram.vhd'].read_text()
    ram, n = re.subn(r'(?im)^\s*library\s+altera_mf;\s*\n\s*use\s+altera_mf\.altera_mf_components\.all;\s*\n', '', ram)
    if n != 1:
        raise ValueError('SPU RAM vendor-context anchor changed')
    if not unmodified_ram:
        if ram.count(CACHE_OLD) != 1:
            raise ValueError('SPU RAM cache-hit anchor changed')
        ram = ram.replace(CACHE_OLD, CACHE_NEW)
    (w / 'spu_ram.vhd').write_text(ram)
    (w / 'gaussian_model.vhd').write_text(GAUSSIAN_MODEL)
    shutil.copyfile(BENCH, w / BENCH.name)
    (w / 'mem').mkdir()
    opts = ['--std=08', '-frelaxed']
    steps = [
        [ghdl, '-a', *opts, '--work=mem', '--workdir=mem', str(files['SyncFifoFallThrough.vhd']), str(MODELS)],
        [ghdl, '-a', *opts, '-Pmem', str(MODELS), str(files['spu_gauss.vhd']), 'spu_ram.vhd', 'gaussian_model.vhd',
         'spu.vhd', 'spu_b.vhd', BENCH.name],
        [ghdl, '-e', *opts, '-Pmem', TOP],
    ]
    with (w / 'compile.log').open('w') as log:
        for step in steps:
            r = subprocess.run(step, cwd=w, stdout=log, stderr=subprocess.STDOUT)
            if r.returncode:
                raise SystemExit(f'GHDL failed: {" ".join(step)}\n{(w / "compile.log").read_text()[-4000:]}')
    return w


def simulate(ghdl, w, log, generics, timeout):
    cmd = [ghdl, '-r', '--std=08', '-frelaxed', '-Pmem', TOP, *[f'-g{k}={v}' for k, v in generics.items()],
           '--ieee-asserts=disable', '--assert-level=failure']
    start = time.time()
    with log.open('w') as f:
        try:
            rc = subprocess.run(cmd, cwd=w, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            rc = -9
    return cmd, rc, round(time.time() - start, 1)


def parse(log):
    text = log.read_text(errors='replace')
    values, windows, mismatches, notes = {}, [], [], []
    for line in text.splitlines():
        m = re.search(r'\): (EV\w*) (.*)$', line)
        if not m:
            if re.search(r'\((assertion|report) (failure|error)\)', line):
                notes.append(line.strip()[-300:])
            continue
        kind, body = m[1], m[2]
        if kind == 'EV':
            for key, value in re.findall(r'(\w+)=(\S+)', body):
                values[key] = int(value) if re.fullmatch(r'-?\d+', value) else value
        elif kind == 'EVWIN':
            windows.append(int(body.split()[1]))
        else:
            mismatches.append(f'{kind[2:]} {body}')
    return values, windows, mismatches, notes


def decay(windows, threshold, require_threshold):
    """Shrinking: no window after the peak window exceeds the largest window before it
    (running maximum fixed once the peak has passed), the last quarter is at most a
    quarter of the first quarter (or at the threshold), and the last 4 windows are at or
    below the threshold when required."""
    if len(windows) < 8:
        return dict(ok=False, reason='fewer than 8 windows')
    q = len(windows) // 4
    head, tail, last = max(windows[:q]), max(windows[-q:]), max(windows[-4:])
    half = len(windows) // 2
    result = dict(peak=max(windows), peak_window=windows.index(max(windows)), first_quarter_max=head,
                  last_quarter_max=tail, first_half_max=max(windows[:half]), second_half_max=max(windows[half:]),
                  last_4_windows_max=last, threshold=threshold, threshold_required=require_threshold,
                  first_zero_window=next((i for i in range(len(windows)) if not any(windows[i:])), None))
    result['not_growing'] = result['second_half_max'] <= result['first_half_max'] and tail <= max(threshold, head // 4)
    result['below_threshold'] = last <= threshold
    result['ok'] = result['not_growing'] and (result['below_threshold'] or not require_threshold)
    return result


def judge(name, config, values, windows, rc, samples, args):
    """Positive case: list of reasons it fails (empty = pass)."""
    bad = []
    if rc != 0:
        bad.append(f'simulation exit code {rc}')
    if values.get('done') != 1:
        bad.append('bench did not finish')
        return bad, {}
    for key in HARD:
        if values.get('mismatch_' + key, -1) != 0:
            bad.append(f'{key} mismatches: {values.get("mismatch_" + key)} (first at cycle {values.get("first_" + key)})')
    for key in END:
        if values.get(key, -1) != 0:
            bad.append(f'{key}: {values.get(key)}')
    if values['samples'] < samples - 1 or values['dry_samples'] < samples - 1:
        bad.append(f'only {values["samples"]} PCM / {values["dry_samples"]} dry samples compared')
    if values['capture_writes'] == 0:
        bad.append('no capture-buffer write compared')
    if values['writes_outside'] == 0 or values['reads_outside'] == 0:
        bad.append('no RAM traffic outside the reverb area')
    extra = {}
    pcm = values['pcm_sample_diffs']
    if config['REVERB_OFF'] == 1:
        if pcm or values['pcm_cycle_diffs']:
            bad.append(f'PCM differs with the reverb off: {pcm} samples, first {values["pcm_first_diff_sample"]}')
        if values['writes_reverb_area']:
            bad.append('reverb RAM writes with REVERBOFF = 1')
    elif config['CNT7'] == 0:
        if values['writes_reverb_area']:
            bad.append('reverb RAM writes with SPUCNT bit 7 = 0')
        if values['pcm_last_diff_sample'] > PCM_STALE_SAMPLES:
            bad.append(f'PCM differs after sample {PCM_STALE_SAMPLES}: last at {values["pcm_last_diff_sample"]}')
        extra['pcm_identical_from_sample'] = values['pcm_last_diff_sample'] + 1
    else:
        if values['writes_reverb_area'] == 0:
            bad.append('no reverb RAM write compared')
        if config['PRESET'] == 1:
            if values['history_regs_differ'] == 0:
                bad.append('reverb filter history equal in A and B at release (vacuous)')
            if values['writes_reverb_area_data_differ'] == 0:
                bad.append('reverb-area exemption never exercised')
            extra['decay'] = decay(windows, args.decay_threshold, not args.quick)
            if not extra['decay']['ok']:
                bad.append(f'reverb difference does not shrink: {extra["decay"]}')
    if config['TRAFFIC'] == 1:
        for key in ('transfer_writes', 'transfer_reads', 'dma_read_words', 'dma_write_words', 'manual_fifo_words',
                    'post_guest_reads', 'irq_pulses'):
            if values.get(key, 0) == 0:
                bad.append(f'transfer case without {key}')
    return bad, extra


def caught_by(values, rc, notes):
    reasons = []
    if rc != 0:
        reasons.append('simulation stopped: ' + (notes[0] if notes else f'exit code {rc}'))
    for key in HARD:
        if values.get('mismatch_' + key, 0):
            reasons.append(f'{key}={values["mismatch_" + key]}')
    for key in END:
        if values.get(key, 0):
            reasons.append(f'{key}={values[key]}')
    if values.get('pcm_sample_diffs', 0):
        reasons.append(f'pcm_sample_diffs={values["pcm_sample_diffs"]}')
    return reasons


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--spu', type=Path, default=ROOT / 'build/ss/a4/save-states/spu.vhd')
    ap.add_argument('--output', type=Path, default=ROOT / 'build/ss/exact-voices-gate')
    ap.add_argument('--seeds', type=int, nargs='+', default=[1, 2])
    ap.add_argument('--samples', type=int, default=384, help='samples compared after release (all but the decay case)')
    ap.add_argument('--decay-samples', type=int, default=3072, help='samples of the reverb decay case')
    ap.add_argument('--decay-threshold', type=int, default=2, help='largest |outA - outB| allowed in the last 4 windows')
    ap.add_argument('--operations', type=int, default=1200, help='random guest writes per SPU before the hold')
    ap.add_argument('--quick', action='store_true', help='one seed, short runs, decay threshold not required')
    ap.add_argument('--only', nargs='+', help='case or fault names to run')
    ap.add_argument('--fault-picks', type=int, nargs='+', default=[0, 7, 13],
                    help='sample-word fault: first voice considered (tried in order until caught)')
    ap.add_argument('--timeout', type=int, default=1500, help='seconds per simulation')
    ap.add_argument('--unmodified-ram', action='store_true', help='keep the dead DDR3 cache compare of spu_ram.vhd (about 2x slower)')
    ap.add_argument('--ghdl', default=None)
    args = ap.parse_args()
    ghdl = str(Path(args.ghdl).resolve()) if args.ghdl else find_tool('ghdl')
    if not ghdl:
        raise SystemExit('ghdl not found')
    if args.quick:
        args.seeds = args.seeds[:1]
        args.samples = min(args.samples, 96)
        args.decay_samples = min(args.decay_samples, 1024)
        args.operations = min(args.operations, 300)
    spu = args.spu.resolve()
    files, source_note = siblings(spu)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = {str(p): sha(p) for p in (spu, BENCH, MODELS, Path(__file__), *files.values())}
    resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    spu_text = spu.read_text()
    builds = {}

    def workdir(variant):
        if variant not in builds:
            builds[variant] = build(ghdl, out, spu_text, files, variant, args.unmodified_ram)
        return builds[variant]

    def wanted(name):
        return not args.only or name in args.only

    report = dict(spu=str(spu), spu_sha256=sha(spu), sources=source_note, ghdl=ghdl, quick=args.quick,
                  seeds=args.seeds, samples=args.samples, spu_ram_cache_compare_skipped=not args.unmodified_ram, decay_samples=args.decay_samples,
                  pcm_stale_samples_allowed_cnt7off=PCM_STALE_SAMPLES, cases=[], faults=[])
    ok = True
    for name, config in CASES.items():
        if not wanted(name):
            continue
        for seed in (args.seeds[:1] if name == 'abort' else args.seeds):
            samples = args.decay_samples if config['PRESET'] == 1 else args.samples
            generics = dict(SEED=seed, OPERATIONS=args.operations, SAMPLES=samples, **config)
            log = out / f'{name}-seed{seed}.log'
            cmd, rc, seconds = simulate(ghdl, workdir('base'), log, generics, args.timeout)
            values, windows, mismatches, notes = parse(log)
            bad, extra = judge(name, config, values, windows, rc, samples, args)
            row = dict(case=name, seed=seed, passed=not bad, failures=bad, seconds=seconds, exit_code=rc,
                       generics=generics, first_mismatches=mismatches[:12], assertions=notes[:4], values=values, **extra)
            if config['REVERB_OFF'] == 0:
                row['reverb_window_maxima'] = windows
            report['cases'].append(row)
            ok &= not bad
            print(f"[{name} seed {seed}] {'PASS' if not bad else 'FAIL'} {seconds}s samples={values.get('samples')} "
                  f"pcm_diff_samples={values.get('pcm_sample_diffs')} reverb_writes={values.get('writes_reverb_area')}"
                  f"/{values.get('writes_reverb_area_data_differ')} differ, outside={values.get('writes_outside')} "
                  f"capture={values.get('capture_writes')}", flush=True)
            for reason in bad:
                print('    ' + reason, flush=True)
            for line in mismatches[:6] if bad else []:
                print('    ' + line, flush=True)
            if 'decay' in extra:
                print('    window maxima:', ' '.join(map(str, windows)), flush=True)
    seed = args.seeds[0]
    for name, fault in FAULTS.items():
        if not wanted(name):
            continue
        picks = args.fault_picks if fault.get('FAULT') == 1 else [0]
        row = None
        for pick in picks:
            generics = dict(SEED=seed, OPERATIONS=args.operations, SAMPLES=args.samples, **CASES['off'],
                            FAULT=fault.get('FAULT', 0), FAULT_PICK=pick)
            log = out / f'fault-{name}-pick{pick}.log'
            cmd, rc, seconds = simulate(ghdl, workdir(fault.get('variant', 'base')), log, generics, args.timeout)
            values, windows, mismatches, notes = parse(log)
            applied = 'variant' in fault or values.get('fault_applied') == 1
            reasons = caught_by(values, rc, notes) if rc != -9 else []
            attempt = dict(fault=name, seed=seed, pick=pick, applied=applied, caught=bool(reasons) and applied,
                           caught_by=reasons, seconds=seconds, exit_code=rc, generics=generics,
                           first_mismatches=mismatches[:8], assertions=notes[:4],
                           location={k: v for k, v in values.items() if k.startswith('fault_')})
            if row is None:
                row = dict(attempt, attempts=[])
            row['attempts'].append({k: attempt[k] for k in ('pick', 'applied', 'caught', 'caught_by', 'seconds')})
            if attempt['caught']:
                row.update(attempt)
                break
        report['faults'].append(row)
        ok &= row['caught']
        print(f"[fault {name}] {'CAUGHT' if row['caught'] else 'NOT CAUGHT'} {row['seconds']}s "
              f"applied={row['applied']} by: {'; '.join(row['caught_by'][:6])}", flush=True)
    changed = [p for p, d in inputs.items() if sha(p) != d]
    report.update(passed=ok and not changed, inputs_changed=changed, sha256=inputs,
                  scope='SPU with spu_ram, spu_gauss and FIFO RTL, behavioural RAM primitives, Gaussian sum model, '
                        'storing SPU RAM model behind the SDRAM pins; not a full-console simulation')
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS' if report['passed'] else 'FAIL', out / 'report.json')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
