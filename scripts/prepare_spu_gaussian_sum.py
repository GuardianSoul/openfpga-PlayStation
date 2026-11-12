#!/usr/bin/env python3
"""Move the selected SPU's four-product Gaussian sum into hard DSP adders."""
import argparse
import hashlib
import json
from pathlib import Path

from measure_pocket_seeds import inputs
from prepare_cd_drive_tables import SPU_SHA
from prepare_spu_ring_paired import once
from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot

ROOT=Path(__file__).resolve().parents[1]
GAUSS_SHA='882a584646b82962c249593199058c30e3137aee7f8c4b743e56b379483969e8'
DECL='''
   component psx_spu_gaussian_sum is
      port (x0,x1,x2,x3,c0,c1,c2,c3 : in std_logic_vector(15 downto 0);
            sum : out std_logic_vector(30 downto 0));
   end component;
   signal gaussian_sum : std_logic_vector(30 downto 0);
'''
INSTANCE='''
   gaussian_mac : psx_spu_gaussian_sum port map (
      x0 => std_logic_vector(adpcmSamples(0)), x1 => std_logic_vector(adpcmSamples(1)),
      x2 => std_logic_vector(adpcmSamples(2)), x3 => std_logic_vector(adpcmSamples(3)),
      c0 => std_logic_vector(gauss1), c1 => std_logic_vector(gauss2),
      c2 => std_logic_vector(gauss3), c3 => std_logic_vector(gauss4), sum => gaussian_sum);
'''


def transform(text):
    if hashlib.sha256(text.encode()).hexdigest()!=SPU_SHA:
        raise ValueError('Requires exact selected full-audio SPU')
    start=text.index('                  interpol1  := resize(gauss1 * adpcmSamples(0), 31);')
    end=text.index('                  --sample     <=',start)
    original=text[start:end]
    sample="                  sample     <= resize(shift_right(interpol1 + interpol2 + interpol3 + interpol4, 15), 16);"
    if original.count(sample)!=1:
        raise ValueError('Gaussian sample expression changed')
    shadow=original.replace(sample,'''                  assert gaussian_sum = std_logic_vector(interpol1 + interpol2 + interpol3 + interpol4)
                     report "SPU Gaussian DSP sum differs from original" severity failure;''')
    replacement='''                  -- synthesis translate_off
'''+shadow+'''                  -- synthesis translate_on
                  sample <= signed(gaussian_sum(30 downto 15));
'''
    text=text[:start]+replacement+text[end:]
    text=once(text,'architecture arch of spu is\n','architecture arch of spu is\n'+DECL)
    return once(text,'\nbegin\n','\nbegin\n'+INSTANCE)


def prepare(source,output):
    source,output=source.resolve(),output.resolve()
    manifest=load_source(source,source/'fit-inputs.json')
    for name,wanted in (('spu.vhd',SPU_SHA),('spu_gauss.vhd',GAUSS_SHA)):
        selected=[r for r in manifest['quartus_sources'] if Path(r['path']).name==name]
        if len(selected)!=1 or selected[0]['sha256']!=wanted:
            raise ValueError('Selected source changed: '+name)
    manifest=snapshot(source,source/'fit-inputs.json',output,2)
    before=inputs(output)
    spu=next(Path(r['path']) for r in before if Path(r['path']).name=='spu.vhd')
    spu.write_text(transform(spu.read_text()))
    wrapper=output/'psx_spu_gaussian_sum.sv'
    wrapper.write_bytes((ROOT/'rtl/pocket/psx_spu_gaussian_sum.sv').read_bytes())
    qsf=output/'apf_top.qsf'
    qsf.write_text(qsf.read_text()+f'\nset_global_assignment -name SYSTEMVERILOG_FILE "{wrapper}" -library work\n')
    after=inputs(output);a={r['path']:r for r in after};b={r['path']:r for r in before}
    if a.keys()-b.keys()!={str(wrapper)} or b.keys()-a.keys():
        raise ValueError('Unexpected closure addition/removal')
    if {p for p in b if a[p]!=b[p]}!={str(spu),str(qsf)}:
        raise ValueError('Expected SPU and wrapper assignment changes only')
    hold_guard(output,after)
    manifest['quartus_sources']=after
    manifest['project_sha256']['.qsf']=digest(qsf)
    (output/'fit-inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
    load_source(output,output/'fit-inputs.json')
    return spu


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(prepare(a.source,a.output))
