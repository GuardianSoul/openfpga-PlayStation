#!/usr/bin/env python3
"""Move the five selected polygon base accumulators to native DSP feedback."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from measure_pocket_seeds import inputs
from prepare_spu_ring_paired import once
from run_frozen_pocket_fit import digest,hold_guard,load_source,snapshot

ROOT=Path(__file__).resolve().parents[1]
POLY_SHA='49736f5f460c264de0bbd0a5695790092f75272bd0631976ef88923762321d05'
CHANNELS='RGBUV'
DECL='''
   component psx_gpu_base_dsp is
      port(clk,enable,load,initialize : in std_logic;
           vertex : in std_logic_vector(7 downto 0);
           increment : in std_logic_vector(19 downto 0);
           value : out std_logic_vector(19 downto 0));
   end component;
   type t_base_dsp_values is array(0 to 4) of std_logic_vector(19 downto 0);
   type t_base_dsp_vertices is array(0 to 4) of std_logic_vector(7 downto 0);
   signal base_dsp_q : t_base_dsp_values;
   signal base_dsp_vertex : t_base_dsp_vertices;
   signal base_dsp_enable : std_logic_vector(4 downto 0);
   signal base_dsp_load : std_logic;
   signal base_dsp_initialize : std_logic := '1';
   -- synthesis translate_off
   signal base_expected_R,base_expected_G,base_expected_B,base_expected_U,base_expected_V : unsigned(19 downto 0) := (others=>'0');
   -- synthesis translate_on
'''
CONTROL='''
   -- Explicit one-time cold initialization; a warm reset retains the old base.
   process(clk2x) begin
      if rising_edge(clk2x) then base_dsp_initialize <= '0'; end if;
   end process;
   process(all)
   begin
      base_dsp_enable <= (others=>'0');
      base_dsp_load <= '0';
      if reset='0' and ce='1' then
         case state is
            when CALCBOUNDARY2 =>
               base_dsp_enable <= (others=>'1'); base_dsp_load <= '1';
            when CALCCOLOR4 =>
               case mulStep is
                  when 1 | 4 => base_dsp_enable(0) <= '1';
                  when 2 | 5 => base_dsp_enable(1) <= '1';
                  when 3 | 6 => base_dsp_enable(2) <= '1';
                  when others => null;
               end case;
            when CALCTEXTURE4 =>
               case mulStep is
                  when 1 | 3 => base_dsp_enable(3) <= '1';
                  when 2 | 4 => base_dsp_enable(4) <= '1';
                  when others => null;
               end case;
            when others => null;
         end case;
      end if;
   end process;
   g_base_dsp : for lane in 0 to 4 generate
      accumulator : psx_gpu_base_dsp port map (
         clk=>clk2x, enable=>base_dsp_enable(lane), load=>base_dsp_load,
         initialize=>base_dsp_initialize, vertex=>base_dsp_vertex(lane),
         increment=>std_logic_vector(mulResult32clk), value=>base_dsp_q(lane));
   end generate;
   -- synthesis translate_off
   process(clk2x) begin
      if falling_edge(clk2x) and base_dsp_initialize='0' then
         assert base_R=base_expected_R and base_G=base_expected_G and base_B=base_expected_B
            and base_U=base_expected_U and base_V=base_expected_V
            report "GPU DSP base differs from original registers" severity failure;
      end if;
   end process;
   -- synthesis translate_on
'''


def transform(text):
    if hashlib.sha256(text.encode()).hexdigest()!=POLY_SHA:
        raise ValueError('Exact selected native polygon source required')
    found=re.findall(r'\bbase_([RGBUV])\s*<=\s*([^;]+);',text)
    for c in CHANNELS:
        if found.count((c,f'vt(coreVertex).{c.lower()} & x"800"'))!=1 or found.count((c,f'base_{c} + mulResult32clk'))!=2:
            raise ValueError('Polygon base assignment inventory changed')
    if len(found)!=15:raise ValueError('Unexpected base writers')
    def shadow(match):
        c,expression=match.groups()
        expression=re.sub(r'\bbase_'+c+r'\b','base_expected_'+c,expression)
        return ('\n                  -- synthesis translate_off\n'
                +f'                  base_expected_{c} <= {expression};\n'
                +'                  -- synthesis translate_on\n')
    text=re.sub(r'\bbase_([RGBUV])\s*<=\s*([^;]+);',shadow,text)
    for c in CHANNELS:
        text,n=re.subn(r'(signal base_'+c+r'\s+: unsigned\(19 downto 0\)) := \(others => \'0\'\);',r'\1;',text)
        if n!=1:raise ValueError('Base declaration changed: '+c)
    text=once(text,'architecture arch of gpu_poly is\n','architecture arch of gpu_poly is\n'+DECL)
    wires=''.join(f'   base_{c} <= unsigned(base_dsp_q({i}));\n'
                  +f'   base_dsp_vertex({i}) <= std_logic_vector(vt(coreVertex).{c.lower()});\n'
                  for i,c in enumerate(CHANNELS))
    return once(text,'\nbegin\n','\nbegin\n'+CONTROL+wires)


def prepare(source,output):
    source,output=source.resolve(),output.resolve()
    manifest=load_source(source,source/'fit-inputs.json')
    rows=[r for r in manifest['quartus_sources'] if Path(r['path']).name=='gpu_poly.vhd']
    if len(rows)!=1 or rows[0]['sha256']!=POLY_SHA:raise ValueError('Selected polygon input changed')
    manifest=snapshot(source,source/'fit-inputs.json',output,2)
    before=inputs(output);poly=next(Path(r['path']) for r in before if Path(r['path']).name=='gpu_poly.vhd')
    poly.write_text(transform(poly.read_text()))
    wrapper=output/'psx_gpu_base_dsp.sv';wrapper.write_bytes((ROOT/'rtl/pocket/psx_gpu_base_dsp.sv').read_bytes())
    qsf=output/'apf_top.qsf';qsf.write_text(qsf.read_text()+f'\nset_global_assignment -name SYSTEMVERILOG_FILE "{wrapper}" -library work\n')
    after=inputs(output);a={r['path']:r for r in after};b={r['path']:r for r in before}
    if a.keys()-b.keys()!={str(wrapper)} or b.keys()-a.keys():raise ValueError('Unexpected new inputs')
    if {p for p in b if a[p]!=b[p]}!={str(poly),str(qsf)}:raise ValueError('Expected only polygon/assignment changes')
    hold_guard(output,after);manifest['quartus_sources']=after;manifest['project_sha256']['.qsf']=digest(qsf)
    (output/'fit-inputs.json').write_text(json.dumps(manifest,indent=2)+'\n');load_source(output,output/'fit-inputs.json')
    return poly


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(prepare(a.source,a.output))
