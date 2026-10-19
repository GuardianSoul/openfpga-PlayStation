#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the registered FIR wrapper against Intel's actual altmult_add model.

This component test does not establish whole-SPU/core/game/hardware acceptance.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
from prepare_spu_fir_dsp import WRAPPER, require
from tool_paths import ROOT, find_tool

REFERENCE = 'module fir_ref(input signed [15:0] x,h1,h2,h3,h4,\n output signed [18:0] parta, output signed [19:0] partb);\n wire signed [18:0] a0={{3{x[15]}},x},a1={{3{h1[15]}},h1};\n wire signed [19:0] b2={{4{h2[15]}},h2},b3={{4{h3[15]}},h3},b4={{4{h4[15]}},h4};\n assign parta=a0+(a1<<<2);\n assign partb=(b2<<<2)+(b2<<<1)+(b3<<<2)+b4;\nendmodule\nmodule fir_registered_ref(input clk,enable,reset,input signed[15:0]x,h1,h2,h3,h4,\n output reg signed[18:0]parta=0,output reg signed[19:0]partb=0);\nwire signed[18:0]a;wire signed[19:0]b;\nfir_ref calc(x,h1,h2,h3,h4,a,b);\nalways@(posedge clk)if(reset)begin parta<=0;partb<=0;end else if(enable)begin parta<=a;partb<=b;end\nendmodule\n'
BENCH = '`timescale 1ns/1ps\nmodule tb;\nreg clk=0;always #5 clk=~clk;\nreg enable=0,reset=1;reg signed[15:0]x=0,h1=0,h2=0,h3=0,h4=0;\nwire signed[18:0]a,ra;wire signed[19:0]b,rb;\npsx_spu_fir_dsp dut(.*,.parta(a),.partb(b));fir_registered_ref refcore(.*,.parta(ra),.partb(rb));\ninteger seed=42,checks=0,pulses=0,resets=0,holds=0;\ntask check;begin\n if(a!==ra||b!==rb)$fatal(1,"registeredDSP mismatch check=%0d en=%b reset=%b a=%d/%d b=%d/%d",checks,enable,reset,a,ra,b,rb);\n checks++;\nend endtask\ninitial begin\n repeat(2)begin @(posedge clk);#1;check();@(negedge clk);end\nfor(integer i=0;i<100000;i++)begin\n x=$random(seed);h1=$random(seed);h2=$random(seed);h3=$random(seed);h4=$random(seed);\n enable=($unsigned($random(seed))%3)!=0;reset=(i%31)==0;if(reset)resets++;if(!enable)holds++;\n // Whole off-edge reset pulses must have no asynchronous effect.\n if(i%11==3 && !reset)begin #1;reset=1;#1;check();reset=0;pulses++;end\n @(posedge clk);#1;check();@(negedge clk);check();\nend\n$display("PASS registeredDSP checks=%0d resets=%0d offedge_pulses=%0d holds=%0d",checks,resets,pulses,holds);$finish;\nend\nendmodule\n'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(output, vendor_library=None):
    output = Path(output).resolve()
    require(not output.exists(), 'New output directory required')
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    require(compiler and runtime, 'Icarus compiler/runtime required')
    quartus = find_tool('quartus_sh')
    require(vendor_library or quartus, 'Quartus simulation library required')
    vendor_library = Path(vendor_library or Path(quartus).resolve().parents[1] / 'eda/sim_lib')
    vendor = [vendor_library / 'altera_mf.v', vendor_library / '220model.v']
    require(all(p.is_file() for p in vendor), 'Missing Intel DSP simulation sources')
    wrapper = ROOT / 'rtl/pocket/psx_spu_fir_dsp.sv'
    require(digest(wrapper) == WRAPPER, 'Unreviewed DSP wrapper')
    protected = {str(p): digest(p) for p in [wrapper, *vendor, Path(__file__)]}
    output.mkdir(parents=True)
    (output / 'reference.sv').write_text(REFERENCE)
    (output / 'tb.sv').write_text(BENCH)
    original = wrapper.read_text()
    require(original.count("4'd6") == 1, 'Missing DSP tap anchor')
    cases = [
        ('positive', original),
        ('fault-tap', original.replace("4'd6", "4'd5")),
        ('fault-reset-mask', original.replace("reset ? 8'd0 : {4'd4,4'd1}", "{4'd4,4'd1}")),
        ('fault-reset-enable', original.replace('.ena0(enable | reset)', '.ena0(enable)')),
        ('fault-input-stage', original.replace('.input_register_a0("UNREGISTERED")', '.input_register_a0("CLOCK0")')),
        ('fault-async-reset', original.replace(".aclr0(1'b0)", '.aclr0(reset)')),
    ]
    require(all(text != original for name, text in cases if name != 'positive'), 'Missing mutation anchor')
    report = dict(scope='Actual vendor-model registered arithmetic, synchronous reset and enable',
                  input_sha256=protected, actual_vendor_model=True, positive_checks=208800,
                  positive_cycles=100000, pipeline_stages_added=0, cases={}, passed=False)
    for name, text in cases:
        folder = output / name
        folder.mkdir()
        source = folder / 'dut.sv'
        source.write_text(text)
        command = [compiler, '-g2012', '-s', 'tb', '-o', str(folder / 'sim'),
                   str(output / 'tb.sv'), str(output / 'reference.sv'), str(source), *map(str, vendor)]
        with (folder / 'build.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        result = subprocess.run([runtime, '-N', str(folder / 'sim')], capture_output=True, text=True, timeout=180)
        (folder / 'run.log').write_text(result.stdout + result.stderr)
        model_errors = re.findall(r'(?im)^.*(?:\berror\b|VVP Stop\().*$', result.stdout + result.stderr)
        passed = ((result.returncode == 0 and 'checks=208800' in result.stdout)
                  if name == 'positive' else
                  (result.returncode != 0 and 'registeredDSP mismatch' in result.stdout))
        passed = passed and not model_errors
        report['cases'][name] = dict(passed=passed, exit_code=result.returncode,
                                    source_sha256=digest(source), command=command, model_errors=model_errors,
                                    output=result.stdout + result.stderr)
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(name, passed, result.stdout.strip(), flush=True)
        require(passed, 'Unexpected DSP verdict: ' + name)
    require(all(digest(Path(p)) == h for p, h in protected.items()), 'DSP test input changed')
    report['passed'] = True
    report['input_identity_verified'] = True
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--vendor-library', type=Path)
    args = parser.parse_args()
    run(args.output, args.vendor_library)
