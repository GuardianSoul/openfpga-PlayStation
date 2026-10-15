#!/usr/bin/env python3
"""Check actual generated primitive maps using installed Intel simulation IP.

The proprietary vendor model is located locally, never copied or committed.
Both write orientations, three clock relations, enables, power-up, wide
words, same-address reads and unknown collision results are compared.
An independent data oracle covers known non-colliding reads too.
"""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_dpram_sdp import primitive
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def verilog(text, name):
    """Translate the two literal altsyncram maps, rejecting unknown syntax."""
    generic = text.split('GENERIC MAP (', 1)[1].split('\n\t)', 1)[0]
    ports = text.split('PORT MAP (', 1)[1].split('\n\t);', 1)[0]
    def associations(source):
        result = []
        for item in source.split(','):
            m = re.fullmatch(r'\s*(\w+)\s*=>\s*([^,]+?)\s*', item, re.S)
            if not m:
                raise ValueError('Unknown primitive association')
            value = re.sub(r'\b(addr_width|data_width)\b',
                           lambda x: 'AW' if x[0].lower() == 'addr_width' else 'DW', m[2], flags=re.I)
            if not re.fullmatch(r'"[^"\n]+"|[\w*]+', value):
                raise ValueError('Unknown primitive parameter expression')
            result.append(f'.{m[1]}({value})')
        return ',\n'.join(result)
    return f'''module {name} #(parameter AW=8,DW=32) (
        input clock_a,clock_b,clken_a,clken_b,wren_a,wren_b,
        input [AW-1:0] address_a,address_b,input [DW-1:0] data_a,data_b,
        output [DW-1:0] q_a,q_b);
        altsyncram #({associations(generic)}) dut ({associations(ports)});
        endmodule
'''


def main():
    quartus = find_tool('quartus_sh')
    if not quartus:
        raise ValueError('Install Quartus locally to use its primitive simulation model')
    vendor = Path(quartus).resolve().parent.parent/'eda/sim_lib/altera_mf.v'
    if not vendor.is_file():
        raise ValueError('Installed altera_mf.v not found: '+str(vendor))
    parent = ROOT/'build/sim/dpram-sdp'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    original = ROOT/'references/PSX_MiSTer/rtl/dpram.vhd'
    bench = ROOT/'sim/pocket/tb_dpram_sdp.sv'
    protected = {str(p): digest(p) for p in (original, bench, Path(__file__), ROOT/'scripts/prepare_dpram_sdp.py', vendor)}
    old = work/'old.sv'; old.write_text(verilog(original.read_text(), 'ram_old'))
    runs = []
    def run(label, helper, aw, dw, clocks, reverse, fault=False):
        case = work/label; case.mkdir()
        source = case/'new.sv'; source.write_text(verilog(helper, 'ram_new'))
        binary = case/'sim.vvp'
        params = [f'-Ptb_dpram_sdp.{name}={value}' for name, value in
                  (('AW', aw), ('DW', dw), ('CLOCKS', clocks), ('REVERSE', reverse))]
        with (case/'build.log').open('w') as log:
            subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_dpram_sdp',
                            '-I', str(vendor.parent), *params, '-o', str(binary), str(old),
                            str(source), str(bench), str(vendor)], stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=60)
        r = subprocess.run([find_tool('vvp'), str(binary)], capture_output=True, text=True, timeout=60)
        output = r.stdout+r.stderr; (case/'run.log').write_text(output)
        passed = r.returncode != 0 and ('RAM mode difference' in output or 'RAM independent data oracle' in output) if fault else r.returncode == 0 and 'PASS ACTUAL_HELPER' in output
        runs.append(dict(label=label, passed=passed, fault=fault, exit_code=r.returncode,
                         output=output, primitive_map_sha256=digest(source)))
        print(label+': '+str(passed), flush=True)
        if not passed:
            raise RuntimeError(label+' failed: '+output[-1200:])
    for reverse in (0, 1):
        helper = primitive(original.read_text(), bool(reverse))
        widths = [(8, 24), (8, 64), (5, 103), (10, 12), (13, 32)] if not reverse else [(4, 32), (4, 24), (8, 32)]
        for aw, dw in widths:
            for clocks in (0, 1, 2):
                run(f'r{reverse}-a{aw}-d{dw}-c{clocks}', helper, aw, dw, clocks, reverse)
        before = 'address_b => address_a' if reverse else 'address_b => address_b'
        after = 'address_b => address_b' if reverse else 'address_b => address_a'
        if helper.count(before) != 1:
            raise ValueError('Fault control anchor')
        run(f'fault{reverse}', helper.replace(before, after), 8, 32, 0, reverse, True)
    identity = all(digest(Path(p)) == sha for p, sha in protected.items())
    report = dict(passed=identity and all(r['passed'] for r in runs), runs=runs,
                  protected_sha256=protected, source_identity_verified=identity,
                  vendor_model_redistributed=False, hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(work)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
