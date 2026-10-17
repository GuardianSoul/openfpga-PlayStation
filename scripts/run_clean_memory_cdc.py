#!/usr/bin/env python3
"""Exercise the real clean card/VRAM pair with skewed CDC payload wires.

Only test copies contain delays. Use the existing full-card APF/guest/GPU
fixture and two chip models. This proves digital protocol settling, not
metastability or the physical board's pin timing.
"""
import concurrent.futures
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_clean_memory_cdc import CARD_SHA, VRAM_SHA, preserved
from tool_paths import ROOT, find_tool


def delayed_card(text, *, pattern=0, fault=''):
    text = preserved(text, CARD_SHA, card=True)
    wires = '\n// Test-only propagation delays; synchronizers/control remain unchanged.\n'
    for stem, width in (('req_we_74', 1), ('req_addr_74', 15), ('req_data_74', 32),
                        ('ready_tgl_74', 1), ('aux_dout', 32)):
        wires += f'wire [{width-1}:0] {stem}_flight;\n'
        for bit in range(width):
            delay = 13000 if (bit + pattern) % 2 else 0
            if fault == 'request' and stem == 'req_data_74': delay = 200000
            if fault == 'response' and stem == 'aux_dout': delay = 200000
            src = stem if width == 1 else f'{stem}[{bit}]'
            wires += f'assign #({delay}) {stem}_flight[{bit}] = {src};\n'
    marker = '    assign aux_we   = own ? fmt_active : req_we_74;'
    if text.count(marker) != 1: raise ValueError('Test delay insertion anchor differs')
    pre, body = text.split(marker)
    body = marker + body
    for stem in ('req_we_74', 'req_addr_74', 'req_data_74', 'ready_tgl_74'):
        body = re.sub(r'\b' + stem + r'\b', stem + '_flight', body)
    response = "        if (rdv_edge) bridge_rd_data <= (BRIDGE_LITTLE != 0) ? aux_dout : swap32(aux_dout);"
    if pre.count(response) != 1: raise ValueError('Test response insertion anchor differs')
    pre = pre.replace(response, response.replace('aux_dout', 'aux_dout_flight'))
    return '`timescale 1ps/1ps\n' + pre + wires + body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', help='Run only these named cases (repeatable)')
    opts = parser.parse_args()
    w = ROOT / 'build/sim/clean-memory-cdc'; w.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix='run-', dir=w))
    source = ROOT / 'rtl/pocket/psx_memcard_slot.sv'
    vram = ROOT / 'rtl/memory/psx_vram_psram.sv'
    bench = ROOT / 'sim/pocket/tb_memcard_slot_vram.sv'
    files = [source, vram, bench, ROOT / 'sim/pocket/psx_cram_model.sv',
             ROOT / 'rtl/pocket/psx_memcard_blank.sv', ROOT / 'sim/pocket/psx_vram_psram_windows.svh']
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (d / vram.name).write_text(preserved(vram.read_text(), VRAM_SHA, card=False))
    cases = [('bounded-' + str(i), i % 2, '', phase) for i, phase in enumerate((0, 2500, 7000, 13000))]
    cases += [('fault-request', 0, 'request', 3000), ('fault-response', 1, 'response', 9000)]
    if opts.case:
        if set(opts.case) - {c[0] for c in cases}: parser.error('Unknown CDC case')
        cases = [c for c in cases if c[0] in opts.case]
    iv = find_tool('iverilog'); vvp = str(Path(iv).with_name('vvp'))
    binaries = {}
    for name, pattern, fault, phase in cases:
        card = d / (name + '.sv'); card.write_text(delayed_card(source.read_text(), pattern=pattern, fault=fault))
        binary = d / (name + '.vvp')
        cmd = [iv, '-g2012', '-Wall', '-Wno-timescale', '-Wno-implicit-dimensions', '-I', str(ROOT / 'sim/pocket'),
               '-s', 'tb_memcard_slot_vram', '-o', str(binary), str(bench), str(card), str(d / vram.name),
               str(ROOT / 'rtl/pocket/psx_memcard_blank.sv'), str(ROOT / 'sim/pocket/psx_cram_model.sv')]
        c = subprocess.run(cmd, capture_output=True, text=True)
        (d / (name + '-compile.log')).write_text(c.stdout + c.stderr)
        if c.returncode: raise RuntimeError('CDC fixture compile failed: ' + name)
        binaries[name] = binary
    def run(case):
        name, pattern, fault, phase = case
        cmd = [vvp, '-n', str(binaries[name]), '+IMAGE=real', '+SEED=17', f'+PHASE={phase}',
               '+GPU_BURST=128', '+GPU_GAP=0', '+GPU_WR=1']
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        (d / (name + '.log')).write_text(r.stdout + r.stderr)
        rows = re.findall(r'^RESULT (\{.*\})$', r.stdout, re.M)
        result = json.loads(rows[-1]) if rows else None
        passed = r.returncode == 0 and result is not None and result['errors'] == 0 and 'PASS' in r.stdout.splitlines()
        expected = 'image in the chips when busy fell' if fault == 'request' else 'readback'
        rejected = not passed and any(line.startswith('ERROR') and expected in line for line in r.stdout.splitlines())
        ok = rejected if fault else passed
        print(('PASS ' if ok else 'FAIL ') + name, flush=True)
        return dict(name=name, ok=ok, fault=fault or None, result=result,
                    failure=[line for line in r.stdout.splitlines() if line.startswith('ERROR')][:3])
    print('RUN_DIRECTORY', d, flush=True)
    with concurrent.futures.ThreadPoolExecutor(3) as pool: results = list(pool.map(run, cases))
    unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())
    ok = unchanged and all(r['ok'] for r in results)
    report = dict(passed=ok, source_identity_unchanged=unchanged, source_hashes=hashes, cases=results,
                  payload_bound_ns=13, actual_fault_delay_ns=200, complete_card_bytes=131072,
                  physical_timing_verified=False, hardware_verified=False)
    (d / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS' if ok else 'FAIL', flush=True)
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__': main()
