#!/usr/bin/env python3
"""Lockstep check of the late VRAM read setup (prepare_gpu_read_late.py).

Two whole GPUs run side by side in the run_gpu_line_share oracle, each with
its own MiSTer DDR model: the baseline (MiSTer gpu.vhd with the timing prune
and bounded read-wrap sources, or with --baseline mister the unmodified
MiSTer GPU) and the same build with read_late_text applied to gpu.vhd. Every
public GPU output is compared on every clk2x cycle, GPUREAD words are compared
on every CPU read, and both VRAMs after every program.

On top of the oracle's primitives and readbacks, the VRAM is filled with a
pattern and the bench issues GP0 0xC0 readbacks and GP0 0x80 copies with rows
up to 1024 halfwords and source/destination x past 1024, textured rectangles
and polygons on the filled VRAM, and the same again with render24 set (the
READSECOND path). Counters on the internal reqVRAMwrap/reqVRAMnext registers
prove the second-burst cases happened; a probe on each memory reports the
fewest clk2x edges between a read request and the first DOUT_READY the GPU
samples (the transform needs >= 2). Planted faults must be rejected.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import time
from pathlib import Path

import run_gpu_line_share
from prepare_gpu_read_late import read_late_text
from prepare_resource_targets import digest
from prepare_system_verilator import prepare
from tool_paths import ROOT, find_tool

FAULTS = {
    # Remaining burst words one short (only reqVRAMremain, not remain2).
    'remain-minus-two': ('               reqVRAMremain  <= unsigned(vram_BURSTCNT) - 1;\n',
                         '               reqVRAMremain  <= unsigned(vram_BURSTCNT) - 2;\n'),
    # Wrap from the request x of the late cycle (zero by then) instead of the latch.
    'wrap-stale-x': ('read_wrap(readLateXPos, readLateSize)', 'read_wrap(reqVRAMXPos, readLateSize)'),
    # Second 128-word burst forgotten.
    'next-zero': ('                  reqVRAMnext <= resize((readLateSize - 512) / 4, 8);\n',
                  "                  reqVRAMnext <= (others => '0');\n"),
}

# (srcX, srcY, dstX, dstY, width, height, mask setting E6)
COPIES = [
    (0, 10, 0, 300, 1024, 2, 0),        # 1024 halfwords: second burst (next)
    (1000, 20, 100, 310, 600, 2, 0),    # source wrap > 128 words (wrap, then next)
    (700, 30, 900, 320, 1024, 1, 0),    # capped row, next and wrap, destination wrap
    (1021, 40, 1019, 330, 7, 3, 0),     # small unaligned wrap
    (513, 50, 5, 340, 511, 2, 0),       # rounded just past 512
    (3, 505, 600, 100, 33, 10, 0),      # source rows wrap past y=511
    (1023, 60, 1023, 350, 1, 1, 0),
    (256, 70, 768, 360, 513, 1, 3),     # set and check mask
    (902, 80, 1010, 370, 250, 3, 1),    # wrap, destination wrap, forced mask
    (1, 90, 2, 380, 1023, 1, 2),        # unaligned, size rounding to 1024
]
# (x, y, width, height)
READBACKS = [
    (0, 10, 1024, 1), (1000, 20, 600, 1), (700, 30, 1024, 1), (1021, 40, 7, 3),
    (513, 50, 511, 2), (3, 509, 33, 5), (1023, 60, 1, 1), (2, 2, 3, 1),
    (902, 370, 250, 2), (1, 380, 1023, 1),
]
LATENCIES = (15, 0)


SIDES = {}


def hooked_prepare(directory, **_):
    """prepare() for transcode(): pruned or MiSTer GPU, optionally read-late."""
    timing_prune, late, fault = SIDES[Path(directory).resolve()]
    manifest = prepare(directory, gpu_timing_prune=timing_prune)
    if late:
        source = next(s for s in manifest['sources'] if Path(s['path']).name == 'gpu.vhd')
        text = read_late_text(Path(source['path']).read_text())
        transforms = ['Late VRAM read setup (prepare_gpu_read_late.py)']
        if fault:
            old, new = FAULTS[fault]
            if text.count(old) != 1:
                raise ValueError('Fault marker missing: ' + fault)
            text = text.replace(old, new)
            transforms.append('PLANTED FAULT ' + fault)
        folder = Path(directory) / 'gpu-read-late'
        folder.mkdir(exist_ok=True)
        path = folder / 'gpu.vhd'
        path.write_text(text)
        copy = {**source, 'path': str(path), 'sha256': digest(path),
                'pre_read_late': source['path'], 'pre_read_late_sha256': source['sha256'],
                'transforms': source.get('transforms', []) + transforms}
        manifest['sources'][manifest['sources'].index(source)] = copy
        manifest['gpu_read_late'] = copy
    return manifest


def transcode(directory, adapted, timing_prune, late, fault, ghdl, log):
    # run_gpu_line_share.transcode() calls its module-level prepare(); the
    # hook (installed once, thread safe) selects the sources per directory.
    SIDES[Path(directory).resolve()] = (timing_prune, late, fault)
    run_gpu_line_share.prepare = hooked_prepare
    return run_gpu_line_share.transcode(Path(directory), adapted, ghdl, log)


def sv_traffic():
    text = ''' // ---- gpu-read-late traffic ----
 write_gpu(4,32'h08000000);write_gpu(4,32'h05000000);
 write_gpu(0,32'he1000000);write_gpu(0,32'he3000000);write_gpu(0,32'he407ffff);write_gpu(0,32'he5000000);write_gpu(0,32'he6000000);
 idle();
 // Identical pseudo-random VRAM in both memories (mask bits included).
 for(integer i=0;i<262144;i++) begin : fill
  reg [31:0] v;
  v=i*32'h9e3779b1;v=v^(v>>15)^32'h5bd1e995;v=v*32'h85ebca6b;v=v^(v>>13);
  old_vram.data[i]=v;new_vram.data[i]=v;
 end
 compare_vram();
'''
    for index, (x, y, w, h) in enumerate(READBACKS):
        text += f' vram_readback({x},{y},{w},{h});\n'
    for sx, sy, dx, dy, w, h, mask in COPIES:
        text += (f" write_gpu(0,32'he6000000|{mask});write_gpu(0,32'h80000000);"
                 f"write_gpu(0,({sy}<<16)|{sx});write_gpu(0,({dy}<<16)|{dx});"
                 f"write_gpu(0,(({h}&511)<<16)|({w}&1023));idle();compare_vram();copy_programs++;\n")
    text += " write_gpu(0,32'he6000000);\n"
    for index, (x, y, w, h) in enumerate(READBACKS[:4]):
        # Read the copy destinations back too.
        dx, dy = COPIES[index][2], COPIES[index][3]
        text += f' vram_readback({dx},{dy},{min(w, 1024)},{h});\n'
    text += ''' for(integer pass24=0;pass24<2;pass24++) begin : textured_late
  render24=pass24;
  for(integer trial=0;trial<24;trial++) begin : textured_late_case
   integer x,y,w,h,page,clut,depth,command;
   depth=trial%3;
   page=(trial%16) | ((trial/16)<<4) | (depth<<7) | ((trial%4)<<5);
   // CLUTs on the right edge and bottom rows, and at x=0.
   clut=trial%3==0 ? ((500<<6)|63):trial%3==1 ? ((511<<6)|48):((trial*7)<<6);
   x=(trial*61)%1000;y=(trial*29)%480;w=1+(trial*13)%120;h=1+(trial*5)%40;
   write_gpu(0,32'he1000000|page);write_gpu(0,32'he2000000|(trial%4==3 ? 32'h000f7bde:0));
   command=32'h64000000|(trial%2 ? 32'h01000000:0)|(trial%5==0 ? 32'h02000000:0);
   write_gpu(0,command|24'h808080);write_gpu(0,(y<<16)|x);
   write_gpu(0,(clut<<16)|(((trial*17)&255)<<8)|((trial*41)&255));write_gpu(0,(h<<16)|w);
   idle();compare_vram();
   command=(trial%2 ? 32'h3c000000:32'h2c000000)|(trial%3==0 ? 32'h01000000:0);
   write_gpu(0,command|24'h808080);write_gpu(0,(y<<16)|x);write_gpu(0,(clut<<16)|16'h0000);
   if(trial%2) write_gpu(0,24'h4080c0);
   write_gpu(0,(y<<16)|(x+w+20));write_gpu(0,(page<<16)|16'h00ff);
   if(trial%2) write_gpu(0,24'hc08040);
   write_gpu(0,((y+h+10)<<16)|x);write_gpu(0,16'hff00);
   if(trial%2) write_gpu(0,24'h80a060);
   write_gpu(0,((y+h+10)<<16)|(x+w+20));write_gpu(0,16'hffff);
   idle();compare_vram();
  end
  write_gpu(0,32'he2000000);
  write_gpu(0,32'h80000000);write_gpu(0,32'h001403e8);write_gpu(0,32'h01900064);write_gpu(0,32'h00020258);
  idle();compare_vram();copy_programs++;
 end
 render24=0;write_gpu(0,32'he1000000);
 vram_readback(1000,20,600,2);
 $display("PASS GPU read late traffic copies=%d cpu_words=%d",copy_programs,late_cpu_words);
 $display("READ LATE STATS old_reads=%d new_reads=%d old_wrap=%d new_wrap=%d old_wrap_over_128=%d new_wrap_over_128=%d old_next=%d new_next=%d",
  old_reads,new_reads,old_wrap_events,new_wrap_events,old_bigwrap_events,new_bigwrap_events,old_next_events,new_next_events);
 $display("READ LATE LATENCY old_min=%d new_min=%d old_max=%d new_max=%d early_ready=%d",
  old_min_latency,new_min_latency,old_max_latency,new_max_latency,early_ready);
'''
    return text


PROBES = '''// ---- gpu-read-late probes ----
integer old_wrap_events=0,new_wrap_events=0,old_bigwrap_events=0,new_bigwrap_events=0;
integer old_next_events=0,new_next_events=0,old_reads=0,new_reads=0,early_ready=0;
integer old_min_latency=1000000,new_min_latency=1000000,old_max_latency=0,new_max_latency=0;
integer old_pending=0,new_pending=0,old_k=0,new_k=0,copy_programs=0,late_cpu_words=0;
reg [7:0] old_wrap_q=0,new_wrap_q=0,old_next_q=0,new_next_q=0;
// Values seen between edges E+j and E+j+1 are what edge E+j+1 samples. A
// request leaves the GPU on edge E (vram_rd high after it); latency k means
// the GPU first samples DOUT_READY high on edge E+k.
always @(negedge clk2x) if(!reset) begin
 if(old_gpu.reqvramwrap!=0 && old_wrap_q==0) begin old_wrap_events++;if(old_gpu.reqvramwrap>128) old_bigwrap_events++;end
 if(new_gpu.reqvramwrap!=0 && new_wrap_q==0) begin new_wrap_events++;if(new_gpu.reqvramwrap>128) new_bigwrap_events++;end
 if(old_gpu.reqvramnext!=0 && old_next_q==0) old_next_events++;
 if(new_gpu.reqvramnext!=0 && new_next_q==0) new_next_events++;
 old_wrap_q=old_gpu.reqvramwrap;new_wrap_q=new_gpu.reqvramwrap;
 old_next_q=old_gpu.reqvramnext;new_next_q=new_gpu.reqvramnext;
 if(old_vram_rd) begin
  if(old_vram_dout_ready) early_ready++;
  old_pending=1;old_k=0;old_reads++;
 end else if(old_pending) begin
  old_k++;
  if(old_vram_dout_ready) begin
   if(old_k+1<old_min_latency) old_min_latency=old_k+1;
   if(old_k+1>old_max_latency) old_max_latency=old_k+1;
   old_pending=0;
  end
 end
 if(new_vram_rd) begin
  if(new_vram_dout_ready) early_ready++;
  new_pending=1;new_k=0;new_reads++;
 end else if(new_pending) begin
  new_k++;
  if(new_vram_dout_ready) begin
   if(new_k+1<new_min_latency) new_min_latency=new_k+1;
   if(new_k+1>new_max_latency) new_max_latency=new_k+1;
   new_pending=0;
  end
 end
end
'''

READBACK_TASK = '''task vram_readback(input integer x,y,w,h);
 integer pixels,total_words;
 reg [31:0] status,got,expected,mask;
 pixels=w*h;total_words=(pixels+1)/2;
 write_gpu(0,32'hc0000000);write_gpu(0,(y<<16)|x);write_gpu(0,((h&511)<<16)|(w&1023));
 for(integer word_index=0;word_index<total_words;word_index++) begin : late_word
  integer polls;
  polls=0;read_gpu(4,status);
  while(!status[27]) begin
   read_gpu(4,status);polls++;
   if(polls>50000) $fatal(1,"GPU read-late readback timeout x=%d y=%d word=%d",x,y,word_index);
  end
  read_gpu(0,got);
  expected=0;
  for(integer half=0;half<2;half++) begin : late_pixel
   integer p,px,py;reg [31:0] stored;
   p=word_index*2+half;px=(x+p%w)%1024;py=(y+p/w)%512;
   stored=old_vram.data[(py*1024+px)>>1];
   expected[half*16+:16]=px%2 ? stored[31:16]:stored[15:0];
  end
  mask=word_index*2+1>=pixels ? 32'h0000ffff:32'hffffffff;
  if((got&mask)!==(expected&mask)) $fatal(1,"GPU read-late readback data x=%d y=%d word=%d got=%x expected=%x",x,y,word_index,got,expected);
  late_cpu_words++;
 end
 idle();compare_vram();
endtask
'''


def make_bench(run_dir, oldname, newname, slowtiming):
    path = run_gpu_line_share.bench(run_dir, oldname, newname, timing_prune=True)
    text = path.read_text()

    def once(old, new):
        nonlocal text
        if text.count(old) != 1:
            raise ValueError('Oracle bench marker changed: ' + old[:60])
        text = text.replace(old, new)

    for prefix in ('old_', 'new_'):
        once(f'mister_ddr_model {prefix}vram(', f'mister_ddr_model #(.SLOWTIMING({slowtiming})) {prefix}vram(')
    once('integer checks=0;', PROBES + 'integer checks=0;')
    once('initial begin\n repeat(100)', READBACK_TASK + 'initial begin\n repeat(100)')
    once(' $display("PASS GPU cycle outputs', sv_traffic() + ' $display("PASS GPU cycle outputs')
    text = text.replace('PASS GPU timing prune', 'PASS GPU read late')
    path.write_text(text)
    return path


def build(verilator, run_dir, verilogs, models, top, log_path):
    with log_path.open('w') as log:
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '6', '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_share', '--Mdir', str(run_dir / 'obj'),
                        *map(str, verilogs), str(models), str(top)],
                       cwd=run_dir, stdout=log, stderr=subprocess.STDOUT, timeout=1800, check=True)
    return run_dir / 'obj/Vtb_gpu_share'


def number(pattern, text):
    match = re.search(pattern, text)
    return int(match[1]) if match else None


def simulate(run_dir, executable):
    started = time.time()
    result = subprocess.run([str(executable)], cwd=run_dir, capture_output=True, text=True, timeout=1800)
    output = result.stdout + result.stderr
    (run_dir / 'sim.log').write_text(output)
    stats = {name: number(name + r'=\s*(-?\d+)', output) for name in (
        'old_reads', 'new_reads', 'old_wrap', 'new_wrap', 'old_wrap_over_128', 'new_wrap_over_128',
        'old_next', 'new_next', 'old_min', 'new_min', 'old_max', 'new_max', 'early_ready',
        'copies', 'cpu_words')}
    stats['cycle_output_ticks'] = number(r'PASS GPU cycle outputs ticks=\s*(\d+)', output)
    stats['oracle_readback_checks'] = number(r'PASS GPU readback checks=\s*(\d+)', output)
    final = re.search(r'PASS GPU read late oracle=\s*(\d+) programs=\s*(\d+) memory_checks=\s*(\d+)', output)
    stats['programs'] = int(final[2]) if final else None
    stats['memory_checks'] = int(final[3]) if final else None
    fatal = [line for line in output.splitlines() if 'mismatch' in line.lower() or 'timeout' in line.lower()
             or 'differs' in line or '%Error' in line or 'fatal' in line.lower()]
    return {'exit_code': result.returncode, 'seconds': round(time.time() - started, 1), 'stats': stats,
            'first_failure': fatal[0] if fatal else None, 'stdout_tail': output[-3000:]}


def passed(run):
    s = run['stats']
    ok = run['exit_code'] == 0 and s['memory_checks'] is not None and s['programs'] and s['memory_checks'] == s['programs'] * 262144
    ok &= bool(s['cycle_output_ticks']) and s['oracle_readback_checks'] == 24 and bool(s['cpu_words'])
    ok &= all(s[k] is not None and s[k] > 0 for k in ('old_wrap', 'old_wrap_over_128', 'old_next'))
    ok &= all(s['old_' + k] == s['new_' + k] for k in ('reads', 'wrap', 'wrap_over_128', 'next', 'min', 'max'))
    ok &= s['old_min'] is not None and s['old_min'] >= 2 and s['early_ready'] == 0
    return bool(ok)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--baseline', choices=('pruned', 'mister'), default='pruned',
                        help='pruned: timing-pruned GPU with bounded read wrap (isolates read-late); '
                             'mister: unmodified MiSTer GPU')
    parser.add_argument('--latency', type=int, nargs='+', default=list(LATENCIES),
                        help='mister_ddr_model SLOWTIMING values for the passing runs (default 15 0)')
    parser.add_argument('--faults', nargs='*', choices=sorted(FAULTS), default=sorted(FAULTS),
                        help='planted faults that must be rejected (default all; none with --faults)')
    parser.add_argument('--output', type=Path, help='output directory (default build/sim/gpu-read-late/BASELINE)')
    args = parser.parse_args()
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    if not ghdl or not verilator:
        raise SystemExit('GHDL and Verilator required (build/tools)')
    started = time.time()
    directory = (args.output.resolve() if args.output else
                 ROOT / 'build/sim/gpu-read-late' / args.baseline)
    directory.mkdir(parents=True, exist_ok=True)
    models = ROOT / 'sim/reference/mister_memory_models.sv'
    sides = {'original': (False, args.baseline == 'pruned', False, None), 'adapted': (True, True, True, None)}
    for fault in args.faults:
        sides['fault-' + fault] = (True, True, True, fault)
    print(f'Transcoding {len(sides)} GPUs (baseline {args.baseline}) ...', flush=True)

    def do_transcode(item):
        name, (adapted, prune, late, fault) = item
        with (directory / (name + '-transcode.log')).open('w') as log:
            return name, transcode(directory / name, adapted, prune, late, fault, ghdl, log)

    run_gpu_line_share.prepare = hooked_prepare
    with concurrent.futures.ThreadPoolExecutor(len(sides)) as pool:
        transcoded = dict(pool.map(do_transcode, sides.items()))
    old_manifest, old_verilog, old_name = transcoded['original']
    runs = [('pass-slow%d' % s, 'adapted', s, False) for s in args.latency]
    runs += [('fault-' + f, 'fault-' + f, LATENCIES[0], True) for f in args.faults]
    executables = {}
    for label, side, slow, _ in runs:
        run_dir = directory / ('run-' + label); run_dir.mkdir(exist_ok=True)
        link = run_dir / 'original'
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(directory / 'original')
        top = make_bench(run_dir, old_name, transcoded[side][2], slow)
        executables[label] = (run_dir, top)
    print(f'Building {len(runs)} Verilator benches ...', flush=True)

    def do_build(item):
        label, side, _, _ = item
        run_dir, top = executables[label]
        return label, build(verilator, run_dir, [old_verilog, transcoded[side][1]], models, top, run_dir / 'build.log')

    with concurrent.futures.ThreadPoolExecutor(min(len(runs), max(1, (os.cpu_count() or 4) // 6))) as pool:
        built = dict(pool.map(do_build, runs))
    print('Simulating ...', flush=True)
    with concurrent.futures.ThreadPoolExecutor(len(runs)) as pool:
        results = dict(zip([r[0] for r in runs], pool.map(lambda r: simulate(executables[r[0]][0], built[r[0]]), runs)))
    report = {'baseline': args.baseline, 'runs': {}, 'passed': True}
    for label, side, slow, fault in runs:
        result = results[label]
        result['slowtiming'] = slow
        result['side'] = side
        result['bench_sha256'] = digest(executables[label][1])
        if fault:
            # A planted fault must stop the bench with a GPU comparison failure.
            rejected = result['exit_code'] != 0 and result['first_failure'] is not None
            result['expected'] = 'reject'; result['rejected'] = rejected; ok = rejected
        else:
            result['expected'] = 'pass'; ok = passed(result)
        result['ok'] = ok; report['passed'] &= ok
        report['runs'][label] = result
        s = result['stats']
        if fault:
            print(f'{label:24} {"REJECTED" if ok else "NOT REJECTED"} ({result["first_failure"]})')
        else:
            print(f'{label:24} {"PASS" if ok else "FAIL"} SLOWTIMING={slow} reads={s["old_reads"]} '
                  f'wrap={s["old_wrap"]}(>128: {s["old_wrap_over_128"]}) next={s["old_next"]} '
                  f'min_latency={s["old_min"]}/{s["new_min"]} cpu_words={s["cpu_words"]} '
                  f'cycle_ticks={s["cycle_output_ticks"]} programs={s["programs"]}'
                  + ('' if ok else f' first_failure={result["first_failure"]}'))
    report['sources'] = {name: [{'path': s['path'], 'sha256': s['sha256'], 'transforms': s.get('transforms', [])}
                                for s in manifest['sources'] if Path(s['path']).name in
                                ('gpu.vhd', 'gpu_poly.vhd', 'psx_gpu_read_math.vhd')]
                         for name, (manifest, _, _) in transcoded.items()}
    report['minimum_read_latency_clk2x'] = min((r['stats']['old_min'] for r in report['runs'].values()
                                                 if r['expected'] == 'pass' and r['stats']['old_min']), default=None)
    report['contract'] = 'No vram_DOUT_READY sampled on the clk2x edge right after a read request (latency >= 2)'
    report['seconds'] = round(time.time() - started, 1)
    report.update({'game_verified': False, 'hardware_verified': False})
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{"PASS" if report["passed"] else "FAIL"} GPU read-late equivalence (baseline {args.baseline}, '
          f'{report["seconds"]} s); report {directory / "report.json"}')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
