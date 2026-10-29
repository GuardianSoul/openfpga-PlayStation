#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Pin-level checks of rtl/memory/psx_vram_psram.sv (Icarus Verilog).

Runs the bench (sim/pocket/tb_psx_vram_psram.sv + psx_cram_model.sv) for
several board-delay profiles and seeds, chips left synchronous by another
core (FOREIGN), LATENCY 4, the latency/bandwidth
measurement, then broken controller variants and broken boards that must be
rejected. Writes build/sim/vram-psram/<run>/results.json (histograms in
clk2x cycles) and prints a summary. X3 runs put the controller on clk3x
(101.6 MHz, latency code 6) behind rtl/memory/psx_vram_x3.sv (--vram-x3),
with board profiles from the release fit's STA, and break the bridge too.

  python3 scripts/run_vram_psram.py            # everything
  python3 scripts/run_vram_psram.py --quick    # one seed, measurement, controls
"""
import argparse
import concurrent.futures as cf
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / 'rtl/memory/psx_vram_psram.sv'
X3_RTL = ROOT / 'rtl/memory/psx_vram_x3.sv'
SIM = ROOT / 'sim/pocket'
BENCH = [SIM / 'psx_cram_model.sv', SIM / 'tb_psx_vram_psram.sv']

# (clock out, control/address out, data out, data back) in ns; chip tACLK/tKOH
PROFILES = {
    'common-1ns':      dict(CLK_DLY=1.0, CTL_DLY=1.0, DQ_DLY=1.0, RET_DLY=1.0),
    'early-clock':     dict(CLK_DLY=0.5, CTL_DLY=4.0, DQ_DLY=4.0, RET_DLY=3.0),
    'late-clock':      dict(CLK_DLY=6.0, CTL_DLY=0.5, DQ_DLY=0.5, RET_DLY=3.0),
    'late-return':     dict(CLK_DLY=6.0, CTL_DLY=0.5, DQ_DLY=0.5, RET_DLY=10.0),
    'slow-control':    dict(CLK_DLY=1.0, CTL_DLY=9.0, DQ_DLY=9.0, RET_DLY=3.0),
    'fast-chip':       dict(CLK_DLY=0.5, CTL_DLY=0.5, DQ_DLY=0.5, RET_DLY=0.5, T_ACLK=3.0, T_KOH=2.0),
}

# broken controllers: (name, before, after, accepted failure markers)
MUTATIONS = [
    ('no-row-split', "if (act_cnt == 8'd1 || row_end || rst_pend",
     "if (act_cnt == 8'd1 || rst_pend", ['DDR read data mismatch', 'tCEM', 'aux read mismatch']),
    ('early-read-sample', 'localparam int RD_FIRST   = LATENCY + 1 + READ_ADJ;',
     'localparam int RD_FIRST   = LATENCY + READ_ADJ;', ['mismatch', 'before tOE']),
    ('late-write-beat', 'localparam int WR_FIRST   = LATENCY + 1 + WRITE_ADJ;',
     'localparam int WR_FIRST   = LATENCY + 2 + WRITE_ADJ;', ['mismatch', 'crossed a row']),
    ('park-keeps-sync-bcr', 'parking ? BCR_ASYNC :', 'parking ? BCR_SYNC :',
     ['park did not restore', 'asynchronous access']),
    ('park-clock-left-running', "clk_run <= !parking;", "clk_run <= 1'b1;",
     ['clock during a configuration access', 'clock running while a die is asynchronous']),
    # the asynchronous form alone: ignored by a synchronous die (another core's, or ours at park)
    ('park-async-form-only', "st <= ST_SCFG; tmr <= 13'd0; cop <= 2'd1; cpass <= 1'b1; parking <= 1'b1;",
     "st <= ST_CFG; tmr <= 13'd0; cop <= 2'd1; cpass <= 1'b0; parking <= 1'b1; clk_run <= 1'b0;",
     ['park did not restore', 'asynchronous access']),
    ('start-async-form-only', "st <= parking ? ST_CFG : ST_SCFG;", "st <= parking ? ST_CFG : ST_IDLE;",
     ['fixed-latency', 'partial refresh'], dict(FOREIGN=1)),
    ('reset-aborts-burst', "        default: st <= ST_INIT;\n        endcase\n",
     "        default: st <= ST_INIT;\n        endcase\n"
     "        if (rst_pend && (st == ST_LAT || st == ST_DATA)) begin pins_idle(); st <= ST_IDLE; act_busy <= 1'b0; end\n",
     ['burst terminated before first data', 'mismatch', 'CE# hold']),
    ('write-continues-any-address', "b_adr == act_adr &&", "1'b1 &&", ['mismatch']),
    ('aux-waits-for-whole-burst', "|| aux_want && aux_prio) last <= 1'b1;", ") last <= 1'b1;",
     ['aux latency bound']),
    ('aux-pause-loses-read', "if (!sel_aux) act_busy <= 1'b1;", "act_busy <= !sel_aux;", ['mismatch', 'timeout']),
    ('buffer-data-lost', "act_din <= {hi_q, lo_q}; act_be <= b_be; b_v <= 1'b0;\n        end else if (sel_bus)",
     "act_din <= ddr_din; act_be <= b_be; b_v <= 1'b0;\n        end else if (sel_bus)", ['mismatch']),
    ('parked-read-sampled-early', [("            3'd3: p_oe_n <= aux_we;",
                                    "            3'd3: begin p_oe_n <= aux_we; if (!aux_we) aux_dout <= dq_in; end"),
                                   ("                if (!aux_we) aux_dout <= FULL_RATE ? dq_r : dq_in;\n", "")], None, ['aux read mismatch']),
    ('out-reg-word-taken-early', "localparam int CAP_DLY = (READ_PIPE != 0 ? 1 : 0) + (OUT_REG ? 1 : 0);",
     "localparam int CAP_DLY = (READ_PIPE != 0 ? 1 : 0);", ['mismatch'],
     dict(X3=1, OUT_REG=1, LATENCY=6, PERIOD=14.766, CLK_KHZ=101606, T_LAT_MIN=55.0, T_CLK_MIN=7.5, PHASE=4.716,
          T_ACLK=5.5, CLK_DLY=3.9, CTL_DLY=3.9, DQ_DLY=3.9, RET_DLY=3.5)),
]
# broken bridges (controller on clk3x behind psx_vram_x3), same shape as MUTATIONS
X3_MUTATIONS = [
    ('x3-fifo-overrun', "ddr_busy <= (wptr_n - rptr_2x) >= 3'd3", "ddr_busy <= (wptr_n - rptr_2x) >= 3'd6", ['mismatch']),
    ('x3-ready-toggle-reset-kept', "rtog_d <= reset ? 1'b0 : rtog_s;", "rtog_d <= rtog_s;", ['without an outstanding read']),
    ('x3-reset-drops-queued-writes', "    if (reset) begin\n        rd_left <= 8'd0; ddr_busy <= 1'b1;",
     "    if (reset) begin\n        rd_left <= 8'd0; ddr_busy <= 1'b1; wptr <= wptr;", ['mismatch'],
     dict(SEED=25, N_OPS=1200, PARKS=2, FOREIGN=1)),
    ('x3-word-not-held', "ddr_dout <= rhold;", "ddr_dout <= c_dout;", ['mismatch']),
    # 0.5.7-test8's bridge: the entry read at the first clk3x edge, before the M10K holds it
    ('x3-fifo-read-before-ram-write', "wire        have = wptr_s != rptr;", "wire        have = wptr != rptr;", ['mismatch']),
    ('x3-aux-request-not-copied', "c_aux_we <= aux_we; c_aux_addr <= aux_addr; c_aux_din <= aux_din;",
     "c_aux_we <= aux_we; c_aux_din <= aux_din;", ['mismatch']),
]
# broken boards with the unchanged controller
BOARD_FAULTS = [
    ('clock-13ns-late', dict(CLK_DLY=13.5, CTL_DLY=0.5, DQ_DLY=0.5, RET_DLY=1.0), ['hold after clock', 'setup to clock']),
    ('return-22ns', dict(CLK_DLY=4.0, CTL_DLY=4.0, DQ_DLY=4.0, RET_DLY=22.0), ['mismatch']),
]


def compile_and_run(work, name, params, rtl=RTL, x3_rtl=X3_RTL):
    exe = work / f'{name}.vvp'
    args = ['iverilog', '-g2012', '-DPSX_M10K_WRITE_HALF', '-I', str(SIM), '-s', 'tb_psx_vram_psram', '-o', str(exe)]
    args += [f'-Ptb_psx_vram_psram.{k}={v}' for k, v in params.items()]
    build = subprocess.run(args + [str(rtl), str(x3_rtl), *map(str, BENCH)], capture_output=True, text=True)
    if build.returncode:
        raise SystemExit(f'{name}: compile failed\n{build.stdout}{build.stderr}')
    run = subprocess.run(['vvp', '-n', str(exe)], capture_output=True, text=True, timeout=3600)
    out = run.stdout + run.stderr
    (work / f'{name}.log').write_text(out)
    return run.returncode, out


def json_line(tag, out):
    m = re.search(tag + r' (\{.*\})', out)
    return json.loads(m.group(1)) if m else None


def percentile(h, q):
    n = sum(h.values())
    acc = 0
    for k in sorted(h, key=int):
        acc += h[k]
        if acc >= q * n:
            return int(k)
    return None


def summarize(h):
    n = sum(h.values())
    if not n:
        return 'none'
    keys = sorted(int(k) for k in h)
    mean = sum(int(k) * v for k, v in h.items()) / n
    return f'n={n} min={keys[0]} mean={mean:.1f} max={keys[-1]}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--jobs', type=int, default=4)
    a = ap.parse_args()
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        raise SystemExit('Icarus Verilog (iverilog, vvp) required')
    parent = ROOT / 'build/sim/vram-psram'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))

    if shutil.which('verilator'):
        lint = subprocess.run(['verilator', '--lint-only', '-Wall', str(RTL), '--top-module', 'psx_vram_psram'],
                              capture_output=True, text=True)
        if lint.returncode:
            raise SystemExit('verilator lint failed\n' + lint.stdout + lint.stderr)
        print('lint: verilator -Wall clean', flush=True)

    positive = {'measure': dict(MEASURE=1)}
    seeds = [1] if a.quick else [1, 2, 3]
    for s in seeds:
        positive[f'random-seed{s}'] = dict(SEED=s, N_OPS=2500, RESETS=4, PARKS=3)
        # chips left synchronous by another core (BCR 1D1F, RCR 0011)
        positive[f'foreign-seed{s}'] = dict(SEED=s, N_OPS=1200, RESETS=2, PARKS=2, FOREIGN=1)
        # latency summary without resets or parks (reads waiting for re-init excluded)
        positive[f'quiet-seed{s}'] = dict(SEED=s, N_OPS=2500, RESETS=0, PARKS=0)
        positive[f'quiet-noaux-seed{s}'] = dict(SEED=s, N_OPS=2500, RESETS=0, PARKS=0, AUX=0)
    # full-rate chip clock (--vram-full-rate): latency 5, DDIO clock PHASE ns
    # after clk, matched pin delays (the window moves with clock-vs-data skew)
    full = dict(FULL_RATE=1, LATENCY=5, T_CLK_MIN=14.7, PHASE=10.0, CLK_DLY=4.5, CTL_DLY=4.5, DQ_DLY=4.5, RET_DLY=2.0)
    positive['full-rate-seed1'] = dict(SEED=1, N_OPS=1500, RESETS=2, PARKS=2, **full)
    positive['full-rate-measure'] = dict(MEASURE=1, **full)
    # --vram-x3: clk3x controller behind the bridge; chip at the 133 MHz column
    # (tACLK 5.5 ns) and the 108 MHz one (7 ns). Board profiles from the rc1i
    # STA paths relative to the capture register clock (slow / fast corner),
    # phase 4.716 ns (--vram-phase default).
    x3 = dict(X3=1, OUT_REG=1, LATENCY=6, PERIOD=14.766, CLK_KHZ=101606, T_LAT_MIN=55.0, T_CLK_MIN=7.5, PHASE=4.716)
    x3_slow = dict(CLK_DLY=3.9, CTL_DLY=3.9, DQ_DLY=3.9, RET_DLY=3.5)
    x3_fast = dict(CLK_DLY=1.9, CTL_DLY=2.1, DQ_DLY=2.1, RET_DLY=1.85, T_ACLK=3.5)
    positive['x3-slow-seed1'] = dict(SEED=21, N_OPS=1500, RESETS=2, PARKS=2, T_ACLK=5.5, **x3, **x3_slow)
    positive['x3-measure'] = dict(MEASURE=1, T_ACLK=5.5, **x3, **x3_slow)
    if not a.quick:
        positive['x3-fast-seed2'] = dict(SEED=22, N_OPS=1500, RESETS=2, PARKS=2, **x3, **x3_fast)
        positive['x3-slow-tACLK7'] = dict(SEED=23, N_OPS=1200, RESETS=2, PARKS=1, T_ACLK=7.0, **x3, **x3_slow)
        positive['x3-foreign'] = dict(SEED=24, N_OPS=1200, RESETS=2, PARKS=2, FOREIGN=1, T_ACLK=5.5, **x3, **x3_slow)
    if not a.quick:
        positive['full-rate-foreign'] = dict(SEED=2, N_OPS=1200, RESETS=2, PARKS=2, FOREIGN=1, **full)
        positive['full-rate-1ns-phase11.5'] = dict(SEED=3, N_OPS=1200, RESETS=2, PARKS=1,
                                                   **{**full, 'PHASE': 11.5, 'CLK_DLY': 1.0, 'CTL_DLY': 1.0, 'DQ_DLY': 1.0, 'RET_DLY': 1.0})
        for name, prof in PROFILES.items():
            positive[f'profile-{name}'] = dict(SEED=10 + len(positive), N_OPS=1200, RESETS=2, PARKS=1, **prof)
        positive['latency4'] = dict(SEED=7, N_OPS=1200, RESETS=2, PARKS=2, LATENCY=4)
        positive['latency4-measure'] = dict(MEASURE=1, LATENCY=4)
        positive['segment32'] = dict(SEED=8, N_OPS=1200, RESETS=2, PARKS=1, ROW_LOG2=5)
        positive['segment32-measure'] = dict(MEASURE=1, ROW_LOG2=5)

    jobs = {}
    with cf.ThreadPoolExecutor(a.jobs) as pool:
        for name, params in positive.items():
            jobs[pool.submit(compile_and_run, work, name, params)] = ('pass', name, None)
        for name, before, after, markers, *extra in MUTATIONS:
            text = RTL.read_text()
            for b, c in (before if after is None else [(before, after)]):
                if text.count(b) != 1:
                    raise SystemExit(f'mutation {name}: anchor not found exactly once')
                text = text.replace(b, c)
            mutated = work / f'mutated-{name}.sv'
            mutated.write_text(text)
            jobs[pool.submit(compile_and_run, work, 'mutated-' + name,
                             dict(SEED=5, N_OPS=900, RESETS=2, PARKS=1, **(extra[0] if extra else {})), mutated)] = ('reject', name, markers)
        for name, before, after, markers, *extra in X3_MUTATIONS:
            text = X3_RTL.read_text()
            if text.count(before) != 1:
                raise SystemExit(f'mutation {name}: anchor not found exactly once')
            mutated = work / f'mutated-{name}.sv'
            mutated.write_text(text.replace(before, after))
            jobs[pool.submit(compile_and_run, work, 'mutated-' + name,
                             {**dict(SEED=5, N_OPS=900, RESETS=2, PARKS=1, T_ACLK=5.5, **x3, **x3_slow), **(extra[0] if extra else {})},
                             RTL, mutated)] = ('reject', name, markers)
        for name, params, markers in BOARD_FAULTS:
            jobs[pool.submit(compile_and_run, work, 'board-' + name,
                             dict(SEED=6, N_OPS=400, RESETS=0, PARKS=0, **params))] = ('reject', name, markers)
        results = {'work': str(work), 'runs': {}, 'rejected': {}}
        failed = []
        for fut in cf.as_completed(jobs):
            kind, name, markers = jobs[fut]
            code, out = fut.result()
            passed = code == 0 and 'VRAM_PSRAM PASS' in out
            if kind == 'pass':
                if not passed:
                    failed.append(f'{name}: expected PASS (log {work}/{name}.log)')
                    print(f'FAIL {name}\n' + '\n'.join(out.splitlines()[-8:]), flush=True)
                    continue
                entry = dict(params=positive[name])
                for tag in ('VP_HIST', 'VP_MEASURE', 'VP_PARKED_AUX'):
                    j = json_line(tag, out)
                    if j:
                        entry[tag] = j
                results['runs'][name] = entry
                print(f'pass {name}', flush=True)
            else:
                fail_line = next((l for l in out.splitlines() if 'FAIL' in l), '')
                hit = [m for m in markers if m in fail_line]
                if passed or not hit:
                    failed.append(f'{name}: broken variant not rejected as expected ({fail_line or "passed"})')
                    print(f'NOT REJECTED {name}: {fail_line or "PASS"}', flush=True)
                else:
                    results['rejected'][name] = fail_line.strip()
                    print(f'rejected {name}: {fail_line.strip()}', flush=True)

    quiet = {}
    for name, r in results['runs'].items():
        if name.startswith('quiet-') and 'VP_HIST' in r:
            h = r['VP_HIST']
            quiet[name] = {m: dict(mean=round(sum(int(k) * v for k, v in h[m].items()) / max(1, sum(h[m].values())), 3),
                                   p99=percentile(h[m], 0.99), max=max(map(int, h[m])) if h[m] else None,
                                   n=sum(h[m].values()))
                           for m in ('read_first', 'read_done', 'write_accept', 'aux_read_done', 'aux_write_done')}
    results['random_latency_summary'] = quiet
    (work / 'results.json').write_text(json.dumps(results, indent=1, sort_keys=True))
    print(f'\nresults: {work}/results.json')
    for name in sorted(results['runs']):
        r = results['runs'][name]
        if 'VP_MEASURE' in r:
            m = r['VP_MEASURE']
            print(f'{name}: read1 first {summarize(m["read1_first"])}; 128-word row-aligned done '
                  f'{summarize(m["read128_row_aligned_done"])}, {m["read128_row_aligned_cycles_per_word"]} clk/word; '
                  f'offset32 {m["read128_offset32_cycles_per_word"]} clk/word; gpu 1024-px line '
                  f'{m["gpu_line1024_cycles_per_line"]} clk; writes seq {m["write_sequential_cycles_per_word"]} / '
                  f'scattered {m["write_scattered_cycles_per_word"]} clk/word; aux read {summarize(m["aux_read_done"])}, '
                  f'write {summarize(m["aux_write_done"])}')
        if 'VP_HIST' in r:
            h = r['VP_HIST']
            print(f'{name}: reads {h["reads"]} ({h["read_words"]} words), writes {h["writes"]}, aux {h["aux"]}, '
                  f'resets {h["resets"]} ({h["resets_ce_low"]} with CE# low), parks {h["parks"]}, '
                  f'max CE# low {h["max_ce_low_ns"]} ns; aux max (running) {h["aux_max_running"]} clk; read first {summarize(h["read_first"])}, '
                  f'write accept {summarize(h["write_accept"])}')
        if 'VP_PARKED_AUX' in r:
            p = r['VP_PARKED_AUX']
            print(f'{name}: parked (asynchronous) aux read {summarize(p["aux_read_done"])}, '
                  f'write {summarize(p["aux_write_done"])}')
    for name in sorted(quiet):
        print('VP_RANDOM_SUMMARY', name, json.dumps(quiet[name], sort_keys=True))
    for name in sorted(results['runs']):
        if 'VP_MEASURE' in results['runs'][name]:
            print('VP_MEASURE', name, json.dumps(results['runs'][name]['VP_MEASURE'], sort_keys=True))
    if failed:
        raise SystemExit('FAILED:\n  ' + '\n  '.join(failed))
    print(f'VRAM_PSRAM ALL PASS: {len(results["runs"])} runs, {len(results["rejected"])} broken variants rejected')


if __name__ == '__main__':
    main()
