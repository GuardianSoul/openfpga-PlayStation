#!/usr/bin/env python3
"""Check exact coefficient packing, stereo integer output and cycle lockstep."""
import json
from pathlib import Path
import subprocess
import tempfile

from generate_pocket_audio_coeffs import generate
from prepare_audio_rom_packed import adapt_text, verify_bank, packed_files
from prepare_resource_targets import digest
from run_pocket_audio_sinc import load_coefficients, write_vectors
from tool_paths import ROOT, find_tool


def main():
    parent = ROOT/'build/sim/pocket-audio-rom-packed'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    coefficients = generate(work/'coefficients')
    bank = verify_bank(coefficients['compact_path'])
    original = ROOT/'rtl/audio/psx_audio_sinc.sv'
    protected = {str(p): digest(p) for p in (original, Path(__file__),
                    ROOT/'scripts/prepare_audio_rom_packed.py', ROOT/'sim/pocket/tb_audio_sinc.sv')}
    reference = work/'reference.sv'
    reference.write_text(original.read_text().replace('module psx_audio_sinc #(', 'module sinc_reference #('))
    files = packed_files(coefficients['compact_path'], work/'packed-coefficients')
    packed = adapt_text(original.read_text(), files)
    vectors = work/'vectors.txt'
    count = write_vectors(vectors, load_coefficients(Path(coefficients['path'])))
    anchor = '    psx_audio_sinc #(.COEFFICIENT_FILE(`COEFFICIENT_FILE)) dut (.*);'
    bench = (ROOT/'sim/pocket/tb_audio_sinc.sv').read_text()
    assert bench.count(anchor) == 1
    bench = bench.replace(anchor, anchor+'''
    wire r_ready,r_busy,r_result_valid;
    wire [31:0] r_result_frame;
    sinc_reference #(.COEFFICIENT_FILE(`COEFFICIENT_FILE)) ref_dut (
        .clk(clk),.reset(reset),.append_valid(append_valid),.append_frame(append_frame),
        .start(start),.phase_coarse(phase_coarse),.phase_fine(phase_fine),
        .ready(r_ready),.busy(r_busy),.result_valid(r_result_valid),.result_frame(r_result_frame));
    integer lookup_checks=0;
    always @(posedge clk) begin
        #1;
        if ({ready,busy,result_valid,result_frame} !== {r_ready,r_busy,r_result_valid,r_result_frame})
            $fatal(1,"Packed audio cycle mismatch");
        if (!reset && dut.valid_1) begin
            if (dut.coefficient_a !== ref_dut.coefficient_a || dut.coefficient_b !== ref_dut.coefficient_b)
                $fatal(1,"Packed audio coefficient mismatch");
            lookup_checks=lookup_checks+2;
        end
    end
''')
    test = work/'tb.sv'; test.write_text(bench)
    runs = []
    for name, source in (
        ('packed', packed),
        ('fault-side', packed.replace('side_a ?', '!side_a ?')),
        ('fault-width', packed.replace('{{11{selected0_a[6]}},selected0_a}', '{{12{selected0_a[5]}},selected0_a[5:0]}')),
        ('fault-phase', packed.replace('{phase_b[6:0], folded_b[3:0]}', '{phase_a[6:0], folded_b[3:0]}')),
    ):
        if name != 'packed' and source == packed:
            raise ValueError('Missing packed audio fault anchor: '+name)
        path = work/(name+'.sv'); path.write_text(source)
        binary = work/(name+'.vvp')
        command = [find_tool('iverilog'), '-g2012', '-s', 'tb_audio_sinc',
                   '-DCOEFFICIENT_FILE="'+coefficients['compact_path']+'"', '-o', str(binary),
                   str(reference), str(path), str(test)]
        with (work/(name+'-build.log')).open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        r = subprocess.run([find_tool('vvp'), str(binary), '+VECTORS='+str(vectors)],
                           capture_output=True, text=True, timeout=120)
        output = r.stdout+r.stderr
        (work/(name+'.log')).write_text(output)
        passed = r.returncode == 0 and f'{count} stereo results' in output
        rejected = r.returncode != 0 and 'Packed audio' in output
        runs.append(dict(name=name, passed=passed, fault_rejected=rejected,
                         source_sha256=digest(path), exit_code=r.returncode, output=output))
        print(name+': '+output, end='', flush=True)
    # Custom banks that violate the packing contract must fail before QSF edits.
    bad = work/'bad.hex18'; data = Path(coefficients['compact_path']).read_text().splitlines()
    data[0] = '00100'; bad.write_text('\n'.join(data)+'\n')
    try:
        verify_bank(bad)
    except ValueError:
        bad_bank_rejected = True
    else:
        bad_bank_rejected = False
    identity = all(digest(Path(p)) == h for p, h in protected.items())
    passed = runs[0]['passed'] and all(r['fault_rejected'] for r in runs[1:]) and bad_bank_rejected and identity
    report = dict(passed=passed, coefficient_bank=bank, stereo_results=count, runs=runs,
                  source_identity_verified=identity, protected_sha256=protected,
                  unsupported_bank_rejected=bad_bank_rejected,
                  fit_verified=False, timing_verified=False, hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(work)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
