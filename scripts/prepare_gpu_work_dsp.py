#!/usr/bin/env python3
"""Move the selected polygon work registers/adders to DSP feedback."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from measure_pocket_seeds import inputs
from prepare_spu_ring_paired import once
from run_frozen_pocket_fit import digest,hold_guard,load_source,snapshot

ROOT=Path(__file__).resolve().parents[1]
SOURCE='1ca4f6dcc2c434e95ca411f1b548474816e8015deeb489787316c907d20a4910'
CHANNELS='RGBUV'
DECL='''
   component psx_gpu_work_dsp is
      port(clk,enable,load,initialize: in std_logic;
           base,increment: in std_logic_vector(19 downto 0);
           value: out std_logic_vector(19 downto 0));
   end component;
   signal work_dsp_q,work_dsp_base,work_dsp_increment: t_base_dsp_values;
   signal work_dsp_enable: std_logic_vector(4 downto 0);
   signal work_dsp_load: std_logic;
   -- synthesis translate_off
   signal work_expected_R,work_expected_G,work_expected_B,work_expected_U,work_expected_V:
      unsigned(19 downto 0) := (others=>'0');
   -- synthesis translate_on
'''
CONTROL='''
   process(all)
   begin
      work_dsp_enable <= (others=>'0');
      work_dsp_load <= '0';
      for lane in 0 to 4 loop
         work_dsp_increment(lane) <= std_logic_vector(mulResult32clk);
      end loop;
      if reset='0' and ce='1' then
         case state is
            when PREPARELINE =>
               work_dsp_enable <= (others=>'1'); work_dsp_load <= '1';
            when CALCLINEADD =>
               case lineMulStep is
                  when 0 | 1 => work_dsp_enable(0) <= '1';
                  when 2 | 3 => work_dsp_enable(1) <= '1';
                  when 4 | 5 => work_dsp_enable(2) <= '1';
                  when 6 | 7 => work_dsp_enable(3) <= '1';
                  when 8 | 9 => work_dsp_enable(4) <= '1';
                  when others => null;
               end case;
            when PROCPIXELS =>
               work_dsp_increment(0) <= std_logic_vector(dxR);
               work_dsp_increment(1) <= std_logic_vector(dxG);
               work_dsp_increment(2) <= std_logic_vector(dxB);
               work_dsp_increment(3) <= std_logic_vector(dxU);
               work_dsp_increment(4) <= std_logic_vector(dxV);
               if pipeline_stall='0' and (firstPixel='0' or pipeline_busy='0') then
                  if rec_shading='1' then work_dsp_enable(2 downto 0) <= "111"; end if;
                  if rec_texture='1' then work_dsp_enable(4 downto 3) <= "11"; end if;
               end if;
            when others => null;
         end case;
      end if;
   end process;
   g_work_dsp: for lane in 0 to 4 generate
      accumulator: psx_gpu_work_dsp port map(clk=>clk2x,
         enable=>work_dsp_enable(lane),load=>work_dsp_load,initialize=>base_dsp_initialize,
         base=>work_dsp_base(lane),increment=>work_dsp_increment(lane),value=>work_dsp_q(lane));
   end generate;
   -- synthesis translate_off
   process(clk2x) begin
      if falling_edge(clk2x) and base_dsp_initialize='0' then
         assert work_R=work_expected_R and work_G=work_expected_G and work_B=work_expected_B
            and work_U=work_expected_U and work_V=work_expected_V
            report "GPU DSP work differs from original registers" severity failure;
      end if;
   end process;
   -- synthesis translate_on
'''


def transform(text):
    if hashlib.sha256(text.encode()).hexdigest()!=SOURCE:
        raise ValueError('Exact selected base-DSP polygon required')
    found=re.findall(r'\bwork_([RGBUV])\s*<=\s*([^;]+);',text)
    for c in CHANNELS:
        expected=[(c,f'base_{c}'),(c,f'work_{c} + mulResult32clk'),(c,f'work_{c} + dx{c}')]
        if any(found.count(pair)!=1 for pair in expected):raise ValueError('Work assignment inventory changed')
    if len(found)!=15:raise ValueError('Unexpected work writer')
    def shadow(match):
        c,expression=match.groups();expression=re.sub(r'\bwork_'+c+r'\b','work_expected_'+c,expression)
        return ('\n                  -- synthesis translate_off\n'+f'                  work_expected_{c} <= {expression};\n'
                +'                  -- synthesis translate_on\n')
    text=re.sub(r'\bwork_([RGBUV])\s*<=\s*([^;]+);',shadow,text)
    for c in CHANNELS:
        text,n=re.subn(r'(signal work_'+c+r'\s+: unsigned\(19 downto 0\)) := \(others => \'0\'\);',r'\1;',text)
        if n!=1:raise ValueError('Work declaration changed: '+c)
    text=once(text,'architecture arch of gpu_poly is\n','architecture arch of gpu_poly is\n'+DECL)
    # The shared value-array type precedes these declarations in VHDL.
    text=once(text,DECL,'')
    anchor='   type t_base_dsp_vertices is array'
    pos=text.index(anchor);text=text[:pos]+DECL+text[pos:]
    wires=''.join(f'   work_{c} <= unsigned(work_dsp_q({i}));\n'
                  +f'   work_dsp_base({i}) <= std_logic_vector(base_{c});\n' for i,c in enumerate(CHANNELS))
    return once(text,'\nbegin\n','\nbegin\n'+CONTROL+wires)


def prepare(source,output):
    source,output=source.resolve(),output.resolve();manifest=load_source(source,source/'fit-inputs.json')
    rows=[r for r in manifest['quartus_sources'] if Path(r['path']).name=='gpu_poly.vhd']
    if len(rows)!=1 or rows[0]['sha256']!=SOURCE:raise ValueError('Selected polygon changed')
    manifest=snapshot(source,source/'fit-inputs.json',output,2)
    before=inputs(output);poly=next(Path(r['path']) for r in before if Path(r['path']).name=='gpu_poly.vhd')
    poly.write_text(transform(poly.read_text()))
    wrapper=output/'psx_gpu_work_dsp.sv';wrapper.write_bytes((ROOT/'rtl/pocket/psx_gpu_work_dsp.sv').read_bytes())
    qsf=output/'apf_top.qsf';qsf.write_text(qsf.read_text()+f'\nset_global_assignment -name SYSTEMVERILOG_FILE "{wrapper}" -library work\n')
    after=inputs(output);a,b=({r['path']:r for r in rs} for rs in (after,before))
    if a.keys()-b.keys()!={str(wrapper)} or b.keys()-a.keys():raise ValueError('Unexpected new inputs')
    if {p for p in b if a[p]!=b[p]}!={str(poly),str(qsf)}:raise ValueError('Expected only polygon/assignment changes')
    hold_guard(output,after);manifest['quartus_sources']=after;manifest['project_sha256']['.qsf']=digest(qsf)
    (output/'fit-inputs.json').write_text(json.dumps(manifest,indent=2)+'\n');load_source(output,output/'fit-inputs.json');return poly


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(prepare(a.source,a.output))
