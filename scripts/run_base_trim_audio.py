#!/usr/bin/env python3
"""Lockstep checks and area screen for the 0.5.6 (rc1i) SPU/audio base trims.

Patches (rtl/pocket/base_trims_056, unified diffs against the rc1i selected
files, applied with patch --fuzz=0, together or alone):
  spu_adsrq.patch          spu.vhd: drop adsrSetting_q (tables read adsrSetting)
  spu_noise.patch          spu.vhd: 4-bit noise shift register instead of noiseCheck
  audio_bridge_dups.patch  psx_audio_bridge.sv: no append_frame/pending_frame copies
  audio_sinc_history.patch psx_audio_sinc.sv: history read two stages later

Checks (each original vs patched, every output compared every cycle):
  spu      GHDL (native VHDL) tb_spu_base_trim.vhd: two SPUs, ADPCM voices,
           ADSR/noise/CNT/key on-off/reverb/CD/ce gaps/resets, savestate-port
           noiseCnt/CNT writes, directed CNT writes in CAPTURE2.
  noise    GHDL exhaustive proof of the noise rewrite for all 16 shifts and
           all 65536 upper noiseCnt halves (tb_spu_noise_mask.vhd).
  sinc     Verilator tb_audio_sinc_trim.sv: random appends/starts/phases/reset.
  bridge   Verilator tb_audio_bridge_trim.sv: two clocks, frame streams with
           stalls, starvation, overflow, src_running drops and async resets.
Each check also runs planted mutations of the patched logic that must fail.
--area runs a yowasp-yosys generic synthesis screen (estimate only).

Usage: run_base_trim_audio.py --project <rc1i project root> [--seeds 1 2]
       [--cycles N] [--only spu noise sinc bridge] [--no-mutations] [--area]
"""
import os
import argparse
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tool_paths import ROOT, find_tool

# BASE_TRIM_PATCHES: another patch set (rtl/pocket/base_trims_a, re-based on the save-state graph)
PATCHES = Path(os.environ.get('BASE_TRIM_PATCHES', ROOT / 'rtl/pocket/base_trims_056'))
SIM = ROOT / 'sim/reference'
GHDL_LOCAL = (ROOT / 'build/tools/ghdl/ghdl-mcode-6.0.0-ubuntu24.04-x86_64/bin/ghdl')
VERILATOR_LOCAL = (ROOT / 'build/tools/verilator/bin/verilator')

# Mutations of the patched logic: (check, patch the mutation targets, old, new, why)
MUTATIONS = [
    ('spu', 'spu_envstep', "env_calcstep := resize(shift_left(to_signed(-8, 16) + to_integer(env_rate(1 downto 0)), env_rateshift), 17);",
     "env_calcstep := resize(shift_left(to_signed(-7, 16) + to_integer(env_rate(1 downto 0)), env_rateshift), 17);", 'decreasing sweep step base -7 instead of -8'),
    ('spu', 'spu_envstep', "                     end if;\n                     if (env_rate < 48) then\n                        if (volumeSetting(13) = '1') then\n                           env_calcstep",
     "                     end if;\n                     if (env_rate < 44) then\n                        if (volumeSetting(13) = '1') then\n                           env_calcstep", 'EQUIVALENT-EXPECTED: sweep shifted-step range ends at rate 44 instead of 48 (rates 44-47 have env_rate(6 downto 2) = 11, so env_rateshift = 0 and the shifted and unshifted steps are the same value; lockstep passed, 2026-11-11)'),
    ('spu', 'spu_adsrq', '(adsrSetting(3 downto 0) + 1) & "00000000000"',
     '(adsrSetting(3 downto 0) + 2) & "00000000000"', 'decay target (sustain level) off by one step'),
    ('spu', 'spu_adsrq', "adsr_rate_q(ADSR_SUSTAIN)  <= adsrSetting(28 downto 22);",
     "adsr_rate_q(ADSR_SUSTAIN)  <= adsrSetting(29 downto 23);", 'sustain rate field shifted one bit'),
    ('spu', 'spu_noise', 'not shift_right(to_unsigned(16#7FFF#, 16), to_integer(noiseShift))',
     'not shift_right(to_unsigned(16#FFFF#, 16), to_integer(noiseShift))', 'noise mask one bit short (threshold 2**(32-s))'),
    ('spu', 'spu_noise', 'not shift_right(to_unsigned(16#7FFF#, 16), to_integer(noiseShift))',
     'not shift_right(to_unsigned(16#7FFF#, 16), to_integer(unsigned(cnt(13 downto 10))))', 'noise shift without the one-ce lag behind CNT'),
    ('noise', 'spu_noise', 'not shift_right(to_unsigned(16#7FFF#, 16), to_integer(noiseShift))',
     'not shift_right(to_unsigned(16#FFFF#, 16), to_integer(noiseShift))', 'noise mask one bit short (threshold 2**(32-s))'),
    ('sinc', 'audio_sinc_history', "history_ptr <= history_ptr - 7'd1;",
     "history_ptr <= history_ptr;", 'history pointer does not step back'),
    ('sinc', 'audio_sinc_history', 'if (valid_2) history_3 <= history[history_ptr];',
     'if (valid_1) history_3 <= history[history_ptr];', 'history read one stage early'),
    ('sinc', 'audio_sinc_history', 'history_ptr <= newest;',
     "history_ptr <= newest - 7'd1;", 'history pointer starts one frame old'),
    ('bridge', 'audio_bridge_dups', '.append_frame(popped_frame)',
     '.append_frame(packets[rd_bin[5:0]])', 'sinc fed from the FIFO head instead of the popped frame'),
    ('bridge', 'audio_bridge_dups', 'if(pending_valid) begin output_frame<=fir_frame; pending_valid<=0; end',
     'if(pending_valid) begin output_frame<={fir_frame[15:0],fir_frame[31:16]}; pending_valid<=0; end', 'output channels swapped'),
    ('bridge', 'audio_bridge_dups', 'if(pending_valid) begin output_frame<=fir_frame; pending_valid<=0; end',
     'if(pending_valid) begin output_frame<=fir_valid ? 32\'d0 : fir_frame; pending_valid<=0; end',
     'EQUIVALENT-EXPECTED: zero the output when fir_valid coincides with a tick (never happens: a result is never due at a tick with pending_valid set)'),
    ('bridge', 'audio_sinc_history', 'if (valid_2) history_3 <= history[history_ptr];',
     'if (valid_1) history_3 <= history[history_ptr];', 'history read one stage early (in bridge context)'),
]


def run(cmd, cwd, timeout=3600, check=True):
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise SystemExit(f'failed: {" ".join(map(str, cmd))}\n{r.stdout[-4000:]}{r.stderr[-4000:]}')
    return r


def hardware_text(text):
    """Drop synthesis translate_off sections (debug writers, savestate asserts)."""
    lines, depth = [], 0
    for line in text.splitlines(keepends=True):
        if re.search(r'--\s*synthesis\s+translate_off', line, re.I): depth += 1
        elif re.search(r'--\s*synthesis\s+translate_on', line, re.I): depth -= 1
        elif not depth: lines.append(line)
    if depth: raise ValueError('Unbalanced synthesis markers')
    return ''.join(lines)


def project_sources(project):
    qsf = (project / 'apf_top.qsf').read_text()
    def assigned(name):
        hits = sorted({line.split('"')[1] for line in qsf.splitlines() if 'set_global_assignment' in line and f'/{name}"' in line})
        if not hits: raise SystemExit(f'{name} not assigned in {project}')
        if len(hits) > 1: raise SystemExit(f'{name} assigned more than once: {hits}')
        return project / hits[0]
    names = ['spu.vhd', 'spu_ram.vhd', 'spu_gauss.vhd', 'SyncFifoFallThrough.vhd',
             'psx_audio_bridge.sv', 'psx_audio_sinc.sv', 'psx_audio_rate.sv', 'psx_audio_i2s.sv']
    found = {n: assigned(n) for n in names}
    coeff = sorted((project / 'pocket-audio').rglob('*.hex18'))
    if len(coeff) != 1: raise SystemExit('expected one coefficient file')
    found['coefficients'] = coeff[0]
    return found


def apply_patches(src, names, dest):
    """Copy src into dest and apply the named patches with --fuzz=0."""
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / src.name
    out.write_text(src.read_text())
    for name in names:
        run(['patch', '--fuzz=0', '--no-backup-if-mismatch', '-s', str(out), '-i', str(PATCHES / f'{name}.patch')], dest)
    return out


def mutate(path, old, new):
    text = path.read_text()
    if text.count(old) != 1: raise SystemExit(f'mutation anchor not unique in {path}: {old}')
    path.write_text(text.replace(old, new))


# ---------------------------------------------------------------- SPU (GHDL)

def spu_check(work, src, ghdl, seeds, cycles, mutations, directed):
    w = work / 'spu'
    if w.exists(): shutil.rmtree(w)
    w.mkdir(parents=True)
    opts = ['--std=08', '-frelaxed']
    (w / 'mem').mkdir()
    run([ghdl, '-a', *opts, '--work=mem', '--workdir=mem', src['SyncFifoFallThrough.vhd'], SIM / 'spu_base_trim_models.vhd'], w)
    spu_ram = w / 'spu_ram.vhd'
    spu_ram.write_text(re.sub(r'(?im)^\s*library\s+altera_mf;\s*\n\s*use\s+altera_mf\.altera_mf_components\.all;\s*\n', '',
                              hardware_text(src['spu_ram.vhd'].read_text())))

    def lib(label, spu_path):
        d = w / label; d.mkdir()
        hw = d / 'spu_hw.vhd'; hw.write_text(hardware_text(spu_path.read_text()))
        run([ghdl, '-a', *opts, f'--work={label}', f'--workdir={label}', '-Pmem', SIM / 'spu_base_trim_models.vhd',
             src['spu_gauss.vhd'], spu_ram, *extra, hw], w)
        return label

    # The save-state SPU instantiates psx_spu_gaussian_sum (hard DSP): bind Codex's portable model
    # (run_spu_gaussian_sum.MODEL), or both sides would run with an unbound interpolation output.
    gauss_model = w / 'psx_spu_gaussian_sum_model.vhd'
    if 'psx_spu_gaussian_sum' in src['spu.vhd'].read_text():
        from run_spu_gaussian_sum import MODEL
        gauss_model.write_text(MODEL)
    extra = [gauss_model] if gauss_model.exists() else []
    ref = lib('ref', src['spu.vhd'])
    spu_patches = os.environ.get('SPU_TRIM_PATCHES', 'spu_adsrq,spu_noise').split(',')
    variants = {'cand': apply_patches(src['spu.vhd'], spu_patches, w / 'cand-src')}
    if mutations:
        for k, (check, patch, old, new, why) in enumerate(MUTATIONS):
            if check != 'spu' or patch not in spu_patches: continue
            p = apply_patches(src['spu.vhd'], spu_patches, w / f'mut{k}-src')
            mutate(p, old, new); variants[f'mut{k}'] = p
    for label, p in variants.items():
        d = w / label; d.mkdir()
        hw = d / 'spu_hw.vhd'; hw.write_text(hardware_text(p.read_text()))
        run([ghdl, '-a', *opts, '--work=cand', f'--workdir={label}', '-Pmem', SIM / 'spu_base_trim_models.vhd',
             src['spu_gauss.vhd'], spu_ram, *extra, hw], w)
        run([ghdl, '-a', *opts, f'--workdir={label}', f'-P{label}', '-Pref', '-Pmem', SIM / 'tb_spu_base_trim.vhd'], w)
        run([ghdl, '-e', *opts, f'--workdir={label}', f'-P{label}', '-Pref', '-Pmem', 'tb_spu_base_trim'], w)

    def sim(label, seed):
        r = run([ghdl, '-r', *opts, f'--workdir={label}', f'-P{label}', '-Pref', '-Pmem', 'tb_spu_base_trim',
                 f'-gSEED={seed}', f'-gCYCLES={cycles}', f'-gDIRECTED={directed}', '--ieee-asserts=disable'], w, timeout=36000, check=False)
        text = r.stdout + r.stderr
        (w / f'{label}-seed{seed}.log').write_text(text)
        m = re.search(r'(PASS SPU base-trim lockstep[^\n]*|MISMATCH[^\n]*)', text)
        return m[1] if m else 'NO RESULT: ' + text[-600:]

    jobs = [('cand', s) for s in seeds] + [(l, seeds[0]) for l in variants if l != 'cand']
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        results = list(pool.map(lambda j: sim(*j), jobs))
    return collect('spu', jobs, results)


def noise_check(work, ghdl, mutations):
    w = work / 'noise'
    if w.exists(): shutil.rmtree(w)
    w.mkdir(parents=True)
    tb = (SIM / 'tb_spu_noise_mask.vhd').read_text()
    variants = {'cand': tb}
    if mutations:
        for k, (check, patch, old, new, why) in enumerate(MUTATIONS):
            if check != 'noise': continue
            if tb.count(old) != 1: raise SystemExit('noise mutation anchor missing in tb_spu_noise_mask.vhd')
            variants[f'mut{k}'] = tb.replace(old, new)
    jobs, results = [], []
    for label, text in variants.items():
        d = w / label; d.mkdir()
        (d / 'tb.vhd').write_text(text)
        run([ghdl, '-a', '--std=08', f'--workdir={label}', d / 'tb.vhd'], w)
        r = run([ghdl, '--elab-run', '--std=08', f'--workdir={label}', 'tb_spu_noise_mask'], w, check=False)
        text = r.stdout + r.stderr
        m = re.search(r'(PASS noise mask[^\n]*|MISMATCH[^\n]*)', text)
        jobs.append((label, 0)); results.append(m[1] if m else 'NO RESULT: ' + text[-400:])
    return collect('noise', jobs, results)


# ---------------------------------------------------------- audio (Verilator)

def rename_modules(text, suffix):
    return re.sub(r'\bpsx_audio_(bridge|sinc|rate|i2s)\b', lambda m: m[0] + suffix, text)


def audio_check(kind, work, src, verilator, seeds, cycles, mutations):
    w = work / kind
    if w.exists(): shutil.rmtree(w)
    w.mkdir(parents=True)
    names = ['psx_audio_bridge.sv', 'psx_audio_sinc.sv', 'psx_audio_rate.sv', 'psx_audio_i2s.sv']
    ref = w / 'ref.sv'
    ref.write_text('\n'.join(rename_modules(src[n].read_text(), '_ref') for n in names))
    patch_of = {'psx_audio_bridge.sv': ['audio_bridge_dups'], 'psx_audio_sinc.sv': ['audio_sinc_history']}

    def candidate(label, mutation=None):
        d = w / f'{label}-src'
        files = []
        for n in names:
            files.append(apply_patches(src[n], patch_of.get(n, []), d) if n in patch_of else d / n)
            if n not in patch_of: d.mkdir(parents=True, exist_ok=True); (d / n).write_text(src[n].read_text())
        if mutation:
            target = d / ('psx_audio_sinc.sv' if mutation[0] == 'audio_sinc_history' else 'psx_audio_bridge.sv')
            mutate(target, mutation[1], mutation[2])
        out = w / f'{label}.sv'
        out.write_text('\n'.join(rename_modules(f.read_text(), '_cand') for f in files))
        return out

    variants = {'cand': candidate('cand')}
    if mutations:
        for k, (check, patch, old, new, why) in enumerate(MUTATIONS):
            if check == kind: variants[f'mut{k}'] = candidate(f'mut{k}', (patch, old, new))
    tb = SIM / f'tb_audio_{kind}_trim.sv'
    coeff = src['coefficients']

    def build(label, path):
        obj = w / f'obj-{label}'
        run([verilator, '--binary', '--timing', '-O2', '-j', '4', '-Wno-fatal', '-Wno-lint', '-Wno-style', '--top-module', f'tb_audio_{kind}_trim',
             f'-GCOEFFICIENT_FILE="{coeff}"', '--Mdir', obj, ref, path, tb], w, timeout=1200)
        return obj / f'Vtb_audio_{kind}_trim'

    with ThreadPoolExecutor(max_workers=4) as pool:
        bins = dict(zip(variants, pool.map(lambda kv: build(*kv), variants.items())))

    def sim(label, seed):
        r = run([bins[label], f'+seed={seed}', f'+cycles={cycles}'], w, timeout=7200, check=False)
        text = r.stdout + r.stderr
        (w / f'{label}-seed{seed}.log').write_text(text)
        m = re.search(r'(PASS audio [^\n]*|MISMATCH[^\n]*)', text)
        return m[1] if m else 'NO RESULT: ' + text[-600:]

    jobs = [('cand', s) for s in seeds] + [(l, seeds[0]) for l in variants if l != 'cand']
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        results = list(pool.map(lambda j: sim(*j), jobs))
    return collect(kind, jobs, results)


def collect(kind, jobs, results):
    rows = []
    ok = True
    for (label, seed), result in zip(jobs, results):
        if label == 'cand':
            good = result.startswith('PASS')
            rows.append({'check': kind, 'variant': 'patched', 'seed': seed, 'result': result, 'ok': good})
        else:
            k = int(label[3:])
            caught = result.startswith('MISMATCH')
            expect_equivalent = MUTATIONS[k][4].startswith('EQUIVALENT-EXPECTED')
            good = (result.startswith('PASS') and expect_equivalent) or (caught and not expect_equivalent)
            rows.append({'check': kind, 'variant': label, 'mutation': MUTATIONS[k][4], 'patch': MUTATIONS[k][1],
                         'seed': seed, 'result': result, 'caught': caught, 'ok': good})
        ok &= rows[-1]['ok']
        print(f"[{kind}] {rows[-1]['variant']:8} seed {seed}: {'ok ' if rows[-1]['ok'] else 'BAD'} {result[:200]}", flush=True)
    return ok, rows


# ------------------------------------------------------------ area (yosys)

def area_screen(work, src, ghdl, yosys):
    """yowasp-yosys synth_intel_alm (Cyclone V) cell counts per block,
    original vs patched. An estimate only: Quartus maps and packs differently."""
    w = work / 'area'
    if w.exists(): shutil.rmtree(w)
    w.mkdir(parents=True)
    jobs = []
    owner = {'psx_audio_bridge.sv': 'audio_bridge_dups', 'psx_audio_sinc.sv': 'audio_sinc_history'}
    # audio blocks: sinc alone; bridge with its rate/i2s/sinc children
    for block, top, patches in (('sinc', 'psx_audio_sinc', ['audio_sinc_history']),
                                ('bridge', 'psx_audio_bridge', ['audio_bridge_dups']),
                                ('bridge+sinc', 'psx_audio_bridge', ['audio_bridge_dups', 'audio_sinc_history'])):
        for label, use in (('orig', []), ('patched', patches)):
            d = w / f'{block}-{label}'; d.mkdir()
            files = []
            for n in ('psx_audio_bridge.sv', 'psx_audio_sinc.sv', 'psx_audio_rate.sv', 'psx_audio_i2s.sv'):
                f = apply_patches(src[n], [owner[n]] if owner.get(n) in use else [], d)
                f.write_text(f.read_text().replace('parameter COEFFICIENT_FILE = ""', 'parameter COEFFICIENT_FILE = "coeff.hex"'))
                files.append(f.name)
            (d / 'coeff.hex').write_text(src['coefficients'].read_text())
            jobs.append((block, label, d, f'read_verilog -sv -DSYNTHESIS {" ".join(files)}; synth_intel_alm -top {top}; tee -o stat.txt stat'))
    # SPU extracts (statements copied from spu.vhd / the patches): the whole
    # SPU's ABC result moves by hundreds of LUTs on any edit.
    for block, entity in (('spu-adsrq-extract', 'spu_adsrq_area'), ('spu-noise-extract', 'spu_noise_area')):
        for label, variant in (('orig', 0), ('patched', 1)):
            d = w / f'{block}-{label}'; d.mkdir()
            run([ghdl, '-a', '--std=08', SIM / f'{entity}.vhd'], d)
            r = run([ghdl, '--synth', '--std=08', f'-gVARIANT={variant}', '--out=verilog', entity], d, timeout=600)
            (d / 'x.v').write_text(r.stdout)
            jobs.append((block, label, d, f'read_verilog x.v; synth_intel_alm -top {entity}; tee -o stat.txt stat'))
    # SPU: ghdl --synth to Verilog (RAM wrappers as behavioural models)
    for label, use in (('orig', []), ('adsrq', ['spu_adsrq']), ('noise', ['spu_noise']), ('both', ['spu_adsrq', 'spu_noise'])):
        d = w / f'spu-{label}'; d.mkdir()
        p = apply_patches(src['spu.vhd'], use, d)
        hw = d / 'spu_hw.vhd'; hw.write_text(hardware_text(p.read_text()))
        spu_ram = d / 'spu_ram_hw.vhd'
        spu_ram.write_text(re.sub(r'(?im)^\s*library\s+altera_mf;\s*\n\s*use\s+altera_mf\.altera_mf_components\.all;\s*\n', '',
                                  hardware_text(src['spu_ram.vhd'].read_text())))
        (d / 'mem').mkdir()
        run([ghdl, '-a', '--std=08', '-frelaxed', '--work=mem', '--workdir=mem', src['SyncFifoFallThrough.vhd'], SIM / 'spu_base_trim_models.vhd'], d)
        run([ghdl, '-a', '--std=08', '-frelaxed', '-Pmem', SIM / 'spu_base_trim_models.vhd', src['spu_gauss.vhd'], spu_ram, hw], d)
        r = run([ghdl, '--synth', '--std=08', '-frelaxed', '-Pmem', '--out=verilog', 'spu'], d, timeout=1800)
        (d / 'spu.v').write_text(r.stdout)
        jobs.append(('spu', label, d, 'read_verilog spu.v; synth_intel_alm -top spu; tee -o stat.txt stat'))

    def one(job):
        block, label, d, script = job
        run([yosys, '-q', '-p', script], d, timeout=14400)
        cells = {}
        for line in (d / 'stat.txt').read_text().splitlines():
            m = re.match(r'\s+(\d+)\s+(MISTRAL_\w+|\$\w+)\s*$', line)
            if m: cells[m[2]] = int(m[1])
        return {'block': block, 'variant': label, 'cells': cells}
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(one, jobs))


def summarize_area(rows):
    out = []
    for row in rows:
        c = row['cells']
        out.append({'block': row['block'], 'variant': row['variant'],
                    'ff': c.get('MISTRAL_FF', 0),
                    'alut': sum(v for k, v in c.items() if k.startswith('MISTRAL_ALUT')),
                    'm10k': c.get('MISTRAL_M10K', 0), 'mlab': c.get('MISTRAL_MLAB', 0),
                    'dsp': sum(v for k, v in c.items() if k.startswith('MISTRAL_MUL'))})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', type=Path, required=True, help='rc1i project root (contains apf_top.qsf)')
    ap.add_argument('--work', type=Path, default=ROOT / 'build/sim/base-trims-056')
    ap.add_argument('--seeds', type=int, nargs='+', default=[1, 2])
    ap.add_argument('--spu-cycles', type=int, default=1500000)
    ap.add_argument('--audio-cycles', type=int, default=3000000)
    ap.add_argument('--directed', type=int, default=300)
    ap.add_argument('--only', nargs='+', choices=['spu', 'noise', 'sinc', 'bridge'], default=['spu', 'noise', 'sinc', 'bridge'])
    ap.add_argument('--no-mutations', action='store_true')
    ap.add_argument('--area', action='store_true')
    args = ap.parse_args()
    ghdl = find_tool('ghdl') or (str(GHDL_LOCAL) if GHDL_LOCAL.exists() else None)
    verilator = str(VERILATOR_LOCAL) if VERILATOR_LOCAL.exists() else find_tool('verilator')
    yosys = find_tool('yosys') or find_tool('yowasp-yosys')
    work = args.work.resolve(); work.mkdir(parents=True, exist_ok=True)
    src = project_sources(args.project.resolve())
    # all four patches must apply together
    together = work / 'together'
    if together.exists(): shutil.rmtree(together)
    apply_patches(src['spu.vhd'], os.environ.get('SPU_TRIM_PATCHES', 'spu_adsrq,spu_noise').split(','), together)
    if 'bridge' in args.only: apply_patches(src['psx_audio_bridge.sv'], ['audio_bridge_dups'], together)
    if 'sinc' in args.only: apply_patches(src['psx_audio_sinc.sv'], ['audio_sinc_history'], together)
    mutations = not args.no_mutations
    ok, rows = True, []
    if 'noise' in args.only:
        r = noise_check(work, ghdl, mutations); ok &= r[0]; rows += r[1]
    for kind in ('sinc', 'bridge'):
        if kind in args.only:
            r = audio_check(kind, work, src, verilator, args.seeds, args.audio_cycles, mutations); ok &= r[0]; rows += r[1]
    if 'spu' in args.only:
        r = spu_check(work, src, ghdl, args.seeds, args.spu_cycles, mutations, args.directed); ok &= r[0]; rows += r[1]
    report = {'passed': ok, 'project': str(args.project), 'results': rows}
    if args.area:
        area_rows = area_screen(work, src, ghdl, yosys)
        report['area'] = summarize_area(area_rows)
        for a in report['area']: print('[area]', a, flush=True)
        report['area_cells'] = area_rows
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS' if ok else 'FAIL', 'base trims 0.5.6 —', work / 'report.json')
    if not ok: sys.exit(1)


if __name__ == '__main__':
    main()
