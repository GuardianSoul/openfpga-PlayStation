#!/usr/bin/env python3
"""Replace fixed MDEC chroma products with two clocked coefficient ROMs."""
import argparse
import hashlib
import json
from pathlib import Path

from measure_pocket_seeds import inputs
from prepare_spu_ring_paired import once
from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot

SOURCE = '48a7308ccb0fa08c78417555737a9613192411582cd586f6aa97bc34ac9bc9df'
TABLES = '''
   type t_chroma_cr_rom is array(0 to 255) of std_logic_vector(22 downto 0);
   type t_chroma_cb_rom is array(0 to 255) of std_logic_vector(19 downto 0);
   function make_chroma_cr return t_chroma_cr_rom is
      variable result : t_chroma_cr_rom;
      variable sample : integer;
   begin
      for i in result'range loop
         sample := i;
         if i >= 128 then sample := i-256; end if;
         result(i) := std_logic_vector(resize(shift_right(to_signed(sample * 359,17),7),10)) &
                      std_logic_vector(resize(shift_right(to_signed(sample * (-183),17),3),13));
      end loop;
      return result;
   end function;
   function make_chroma_cb return t_chroma_cb_rom is
      variable result : t_chroma_cb_rom;
      variable sample : integer;
   begin
      for i in result'range loop
         sample := i;
         if i >= 128 then sample := i-256; end if;
         result(i) := std_logic_vector(resize(shift_right(to_signed(sample * 454,17),7),10)) &
                      std_logic_vector(resize(shift_right(to_signed(sample * (-88),17),5),10));
      end loop;
      return result;
   end function;
   constant CHROMA_CR : t_chroma_cr_rom := make_chroma_cr;
   constant CHROMA_CB : t_chroma_cb_rom := make_chroma_cb;
   attribute romstyle : string;
   attribute romstyle of CHROMA_CR : constant is "M10K";
   attribute romstyle of CHROMA_CB : constant is "M10K";
   signal color_cr_q : std_logic_vector(22 downto 0);
   signal color_cb_q : std_logic_vector(19 downto 0);
'''
DECL = """
   component psx_mdec_chroma_rom is
      port(clk,cr_enable,cb_enable: in std_logic;
           address: in std_logic_vector(7 downto 0);
           cr_q: out std_logic_vector(22 downto 0);
           cb_q: out std_logic_vector(19 downto 0));
   end component;
   signal color_cr_q: std_logic_vector(22 downto 0);
   signal color_cb_q: std_logic_vector(19 downto 0);
   signal chroma_cr_enable,chroma_cb_enable: std_logic;
"""
READS = """
   chroma_cr_enable <= '1' when reset_calc='0' and ce='1' and colorState=COLOR_READ1 else '0';
   chroma_cb_enable <= '1' when reset_calc='0' and ce='1' and colorState=COLOR_READ2 else '0';
   chroma_rom: psx_mdec_chroma_rom port map(clk=>clk2x,
      cr_enable=>chroma_cr_enable,cb_enable=>chroma_cb_enable,address=>idct_readData,
      cr_q=>color_cr_q,cb_q=>color_cb_q);
"""
ROOT = Path(__file__).resolve().parents[1]


def mif(channel):
    width=23 if channel=='cr' else 20
    rows=[]
    for i in range(256):
        value=i if i<128 else i-256
        if channel=='cr':
            word=(((value*359)>>7)&1023)<<13 | (((value*(-183))>>3)&8191)
        else:
            word=(((value*454)>>7)&1023)<<10 | (((value*(-88))>>5)&1023)
        rows.append(f'{i:02X} : {word:0{(width+3)//4}X};')
    return f'WIDTH={width};\nDEPTH=256;\nADDRESS_RADIX=HEX;\nDATA_RADIX=HEX;\nCONTENT BEGIN\n'+'\n'.join(rows)+'\nEND;\n'

CONVERT = '''                  color_conv_R <= signed(color_cr_q(22 downto 13));
                  chroma_green := resize(signed(color_cr_q(12 downto 0)),14) +
                     shift_left(resize(signed(color_cb_q(9 downto 0)),14),2);
                  color_conv_G <= chroma_green(13 downto 4);
                  color_conv_B <= signed(color_cb_q(19 downto 10));'''


def color_block(text):
    start = text.index('                  -- mdec-mednafen: green\'s terms')
    end = text.index('\n\n               when COLOR_CALC',start)
    return text[start:end]


def transform(text):
    from prepare_mdec_idct_mac import split_credit
    credit,text=split_credit(text)   # the release's licence comment stays on top
    if hashlib.sha256(text.encode()).hexdigest()!=SOURCE:
        raise ValueError('Exact selected DSP MDEC required')
    return credit+transform_body(text)


def transform_body(text):
    for c in 'RB':
        text=once(text,f"   signal color_read_{c}        : signed(7 downto 0) := (others => '0');\n",'')
    text=once(text,'architecture arch of mdec is\n','architecture arch of mdec is\n'+DECL)
    text=once(text,'                  color_read_R <= signed(idct_readData);',
              '')
    text=once(text,'                  color_read_B <= signed(idct_readData);',
              '')
    text=once(text,color_block(text),CONVERT)
    anchor='      variable conv_R'
    pos=text.index(anchor); line_end=text.index('\n',pos)
    text=text[:line_end]+ '\n      variable chroma_green : signed(13 downto 0);'+text[line_end:]
    return once(text,'\nbegin\n','\nbegin\n'+READS)


def prepare(source,output):
    source,output=source.resolve(),output.resolve()
    original=load_source(source,source/'fit-inputs.json')
    rows=[r for r in original['quartus_sources'] if Path(r['path']).name=='mdec.vhd']
    from prepare_mdec_idct_mac import qualified
    if len(rows)!=1 or not qualified(Path(rows[0]['path']).read_text(),SOURCE):raise ValueError('Selected MDEC changed')
    manifest=snapshot(source,source/'fit-inputs.json',output,2)
    before=inputs(output);mdec=next(Path(r['path']) for r in before if Path(r['path']).name=='mdec.vhd')
    mdec.write_text(transform(mdec.read_text()))
    wrapper=output/'psx_mdec_chroma_rom.vhd'
    wrapper.write_bytes((ROOT/'rtl/pocket/psx_mdec_chroma_rom.vhd').read_bytes())
    qsf=output/'apf_top.qsf'
    assignments=f'\nset_global_assignment -name VHDL_FILE "{wrapper}" -library work\n'
    for channel in ('cr','cb'):
        path=output/f'psx_mdec_chroma_{channel}.mif';path.write_text(mif(channel))
        assignments+=f'set_global_assignment -name MIF_FILE "{path}"\n'
    qsf.write_text(qsf.read_text()+assignments)
    after=inputs(output);a,b=({r['path']:r for r in rs} for rs in (after,before))
    expected={str(wrapper),str(output/'psx_mdec_chroma_cr.mif'),str(output/'psx_mdec_chroma_cb.mif')}
    if a.keys()-b.keys()!=expected or b.keys()-a.keys():raise ValueError('Unexpected new inputs')
    if {p for p in b if a[p]!=b[p]}!={str(mdec),str(qsf)}:
        raise ValueError('Expected only MDEC/assignment changes')
    hold_guard(output,after);manifest['quartus_sources']=after
    manifest['project_sha256']['.qsf']=digest(qsf)
    (output/'fit-inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
    load_source(output,output/'fit-inputs.json');return mdec



if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(prepare(a.source,a.output))
