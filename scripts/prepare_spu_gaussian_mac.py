#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Reproduce exact native Gaussian DSP arithmetic in ignored reset-only projects.

Unselected experiment. Pin the actual coefficient ROM: native signed resize
and the MAC low-bit sum agree only within its proved coefficient bounds.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from prepare_spu_adsr_select import adsr_select_text, require, CONTROLLER, TOP

EXPECTED='b75586c8b309299bd16e5f558760d69aa21db738a15de35b2d9af5f153eda256'
OUTPUT='dbba681cb7e253521843f25c7c2e8f60eacb2fc581b6b4ba9800165ff56aa7b0'
COMBINED_OUTPUT='b6bc99e013999b2f210aa4ec8281425d08aa4785d7947b1e71965c02a76aa795'
WRAPPER='0fa129dbefc39e29a4e2936fdcb7fdc226d8d9f5db8ed993633e328a092851f0'
ROM='882a584646b82962c249593199058c30e3137aee7f8c4b743e56b379483969e8'

def gaussian_text(text, *, with_adsr=False):
    require(hashlib.sha256(text.encode()).hexdigest() == EXPECTED, 'Exact FIR-width SPU required')
    t = adsr_select_text(text) if with_adsr else text
    where = t.index('\nbegin\n')
    decl = '\n   component psx_spu_gaussian_mac is\n   port(coeff,samples:in std_logic_vector(63 downto 0);total:out std_logic_vector(30 downto 0);y:out std_logic_vector(15 downto 0));\n   end component;\n   signal gaussian_y:std_logic_vector(15 downto 0);\n'
    t = t[:where] + decl + t[where:]
    t = t.replace('\nbegin\n', "\nbegin\n   gaussian_mac: psx_spu_gaussian_mac port map(\n      coeff=>std_logic_vector(signed'(gauss4 & gauss3 & gauss2 & gauss1)),\n      samples=>std_logic_vector(signed'(adpcmSamples(3) & adpcmSamples(2) & adpcmSamples(1) & adpcmSamples(0))),total=>open,y=>gaussian_y);\n", 1)
    a = t.index('                  interpol1  := resize(gauss1 *')
    b = t.index('                  --sample     <=', a)
    removed = t[a:b]
    t = t[:a] + '                  sample <= signed(gaussian_y);\n' + t[b:]
    require(hashlib.sha256(t.encode()).hexdigest() == (COMBINED_OUTPUT if with_adsr else OUTPUT), 'Unexpected Gaussian SPU output')
    return t


def prepare(directory, *, with_adsr=False):
    from tool_paths import ROOT
    directory=Path(directory).resolve()
    require(directory.is_relative_to(ROOT/'build') and not subprocess.run(
        ['git','check-ignore','--quiet',str(directory/'gaussian-mac/manifest.json')],cwd=ROOT).returncode,
        'Ignored private build project required')
    require(not any((directory/n).exists() for n in
                    ['db','incremental_db','output_files','gaussian-mac']),
            'Prepare Gaussian once, before compilation')
    require(json.loads((directory/'manifest.json').read_text()).get('reset_only'),
            'Reset-only composition required')
    wrapper=ROOT/'rtl/pocket/psx_spu_gaussian_mac.sv'
    require(wrapper.is_file() and hashlib.sha256(wrapper.read_bytes()).hexdigest()==WRAPPER,
            'Unreviewed Gaussian wrapper')
    from measure_pocket_seeds import inputs
    from prepare_pocket_config_cdc import assignments
    from prepare_resource_targets import digest
    from prepare_spu_fir_width import HOLD,HOLD_SCOPE
    qsf=directory/'apf_top.qsf';text=qsf.read_text();assigned=assignments(directory,text)
    def source(name):
        found={p for kind,p in assigned if kind=='VHDL_FILE' and p.name==name}
        require(len(found)==1,'One assigned '+name+' required')
        return found.pop()
    original,rom,controller,top=map(source,['spu.vhd','spu_gauss.vhd','savestates.vhd','psx_top.vhd'])
    require(digest(rom)==ROM,'Reviewed Gaussian coefficient ROM required')
    require(digest(controller)==CONTROLLER and digest(top)==TOP,
            'Reviewed reset-only controller/top required')
    hold=directory/'cpu-hold-margin/pocket_cpu_hold.sdc'
    require(('SDC_FILE',hold)in assigned and hold.is_file(),'Assigned 150 ps hold SDC required')
    require(hashlib.sha256(hold.read_text().replace(str(directory),'<PROJECT>').encode()).hexdigest()==HOLD
            and digest(hold.with_name('scope.tcl')) in HOLD_SCOPE,
            'Exact retained 150 ps hold constraint/scope required')
    before=inputs(directory);previous_qsf=digest(qsf)
    protected={str(p):digest(p)for p in [original,rom,controller,top,hold,hold.with_name('scope.tcl'),wrapper]}
    output=gaussian_text(original.read_text(),with_adsr=with_adsr)
    token='"'+str(original)+'"';require(text.count(token)==1,'One quoted absolute SPU assignment required')
    work=directory/'gaussian-mac'
    changed=text.replace(token,'"'+str(work/'spu.vhd')+'"',1)
    changed+='\nset_global_assignment -name SYSTEMVERILOG_FILE "'+str(work/'psx_spu_gaussian_mac.sv')+'"\n'
    require(before==inputs(directory) and previous_qsf==digest(qsf)
            and all(digest(Path(p))==h for p,h in protected.items()),'Project changed during preparation')
    work.mkdir();(work/'spu.vhd').write_text(output)
    (work/'psx_spu_gaussian_mac.sv').write_bytes(wrapper.read_bytes());qsf.write_text(changed)
    require(all(digest(Path(p))==h for p,h in protected.items()),'Original source/constraints changed')
    report=dict(original_inputs=before,protected_original_sha256=protected,
                previous_qsf_sha256=previous_qsf,output_qsf_sha256=digest(qsf),
                input_spu_sha256=EXPECTED,output_spu_sha256=COMBINED_OUTPUT if with_adsr else OUTPUT,
                wrapper_sha256=WRAPPER,coefficient_rom_sha256=ROM,preparer_sha256=digest(Path(__file__)),
                adsr_select=with_adsr,noise_rewrite=False,reset_only=True,extra_cpu_hold_ns=.15,
                added_cycles=0,added_stages=0,extra_DSP_requested=0,extra_M10K_requested=0,
                selected=False,fit_verified=False,strict_timing_verified=False,hardware_verified=False)
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path)
    parser.add_argument('--with-adsr',action='store_true',help='Also reproduce qualified select-before-decode ADSR')
    args=parser.parse_args()
    print(json.dumps(prepare(args.project,with_adsr=args.with_adsr),indent=2))
