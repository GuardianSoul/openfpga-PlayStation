#!/usr/bin/env python3
"""Measure the output FIR on Pocket's device with a real clock pin.

Standalone component only. Excludes async FIFO, servo, I2S, PLL and SPU;
no complete-top, external-interface or board timing claim.
"""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from build_pocket import source
from generate_pocket_audio_coeffs import generate
from prepare_resource_targets import apply_compile_profile, digest, write_project
from run_resource_fit import fit_target
from tool_paths import ROOT, find_tool
from verify_pocket_audio_rom import verify, verify_packed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packed', action='store_true', help='Exact tap-width ROM packing; no phase/tap reduction')
    args = parser.parse_args()
    parent = ROOT/'build/resources/pocket-audio-sinc/runs'; parent.mkdir(parents=True,exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=parent))
    project = work/'project'; project.mkdir()
    coefficients = generate(work/'coefficients')
    copied = project/'psx_audio_sinc.sv'
    copied.write_bytes((ROOT/'rtl/audio/psx_audio_sinc.sv').read_bytes())
    if args.packed:
        from prepare_audio_rom_packed import adapt_text, verify_bank, packed_files
        verify_bank(coefficients['compact_path'])
        packed = packed_files(coefficients['compact_path'], project/'packed-coefficients')
        copied.write_text(adapt_text(copied.read_text(), packed))
    top = 'psx_audio_sinc_resource'
    wrapper = project/(top+'.sv')
    wrapper.write_text('''module psx_audio_sinc_resource (
    input wire clk, reset, append_valid, start,
    input wire [31:0] append_frame,
    input wire [7:0] phase_coarse,
    input wire [15:0] phase_fine,
    output wire ready, busy, result_valid,
    output wire [31:0] result_frame
);
    psx_audio_sinc #(.COEFFICIENT_FILE("'''+coefficients['compact_path']+'''")) converter (.*);
endmodule
''')
    specs = [('clk','in',1),('reset','in',1),('append_valid','in',1),('start','in',1),
             ('append_frame','in',32),('phase_coarse','in',8),('phase_fine','in',16),
             ('ready','out',1),('busy','out',1),('result_valid','out',1),('result_frame','out',32)]
    ports = [(n,d,'std_logic' if w==1 else f'std_logic_vector({w-1} downto 0)') for n,d,w in specs]
    sources = [source(wrapper),source(copied)]
    if args.packed:
        sources += [dict(path=str(p), sha256=digest(p), library='work', assignment='MISC_FILE') for p in packed]
    hashes = write_project(project,top,ports,('clk',),sources,
        ['create_clock -name audio_mclk -period 81.380208 [get_ports clk]',
         'derive_clock_uncertainty', '# Virtual interfaces have no board timing model.'])
    target = dict(top=top,directory=str(project),quartus_sources=sources,project_sha256=hashes,
                  physical_clock_ports=['clk'],fixed_ports={},included_components=['128-tap stereo output FIR'],
                  limitations=['Virtual interfaces, no FIFO/servo/I2S/PLL/SPU, no external timing closure'])
    manifest = apply_compile_profile(dict(device='5CEBA4F23C8',targets={'audio_sinc':target}),'area-min-state')
    manifest['coefficients']=coefficients
    manifest['packed_coefficients']=args.packed
    manifest['original_rtl_sha256']=digest(ROOT/'rtl/audio/psx_audio_sinc.sv')
    (work/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(work,flush=True)
    binary=Path(find_tool('quartus_sh')).parent
    result=fit_target(binary,target,manifest['device'],1800)
    result['coefficient_identity_verified']=digest(Path(coefficients['path']))==coefficients['sha256']
    result['coefficient_identity_verified'] &= digest(Path(coefficients['compact_path']))==coefficients['compact_sha256']
    if not result['coefficient_identity_verified']: result['fit_verified']=False
    if result['fit_verified']:
        try:
            result['mapped_coefficients'] = verify_packed(project, packed) if args.packed else verify(project, coefficients['compact_path'])
        except ValueError as error:
            result['errors'].append(str(error))
            result['fit_verified'] = False
    report=dict(**manifest,result=result,tool_version=subprocess.check_output([str(binary/'quartus_sh'),'--version'],text=True))
    (work/'quartus-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:result.get(k) for k in ('fit_verified','counts','tool_error_messages','reported_timing')}),flush=True)
    if not result['fit_verified']: raise SystemExit(1)


if __name__=='__main__': main()
