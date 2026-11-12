#!/usr/bin/env python3
"""Put selected MDEC product/sum stages into a native DSP accumulator."""
import argparse
import hashlib
import json
from pathlib import Path

from measure_pocket_seeds import inputs
from prepare_spu_ring_paired import once
from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'b5a220d716c05f7b3227481a21f3bc6950262734a99761e59b7d75c3adfaaa8d'
COMPONENT = '''
   component psx_mdec_idct_mac is
      port (clk, enable, valid, first : in std_logic;
            a0, b0, a1, b1 : in std_logic_vector(15 downto 0);
            sum : out std_logic_vector(30 downto 0));
   end component;
   signal idct_mac_enable : std_logic;
   signal idct_mac_sum : std_logic_vector(30 downto 0);
   -- synthesis translate_off
   signal idct_sum_expected : signed(30 downto 0);
   -- synthesis translate_on
'''
INSTANCE = '''
   idct_mac_enable <= ce and not reset_calc;
   idct_sum <= signed(idct_mac_sum);
   idctmac : psx_mdec_idct_mac port map (
      clk => clk2x, enable => idct_mac_enable,
      valid => idct_calc1_ena, first => idct_calc1_first,
      a0 => std_logic_vector(idct_calcP_mul11),
      b0 => std_logic_vector(idct_calcP_mul12),
      a1 => std_logic_vector(idct_calcP_mul21),
      b1 => std_logic_vector(idct_calcP_mul22), sum => idct_mac_sum);
   -- synthesis translate_off
   process(clk2x)
   begin
      if rising_edge(clk2x) then
         if ce = '1' and reset_calc = '0' and idct_calc2_ena = '1' then
            assert idct_sum = idct_sum_expected
               report "MDEC DSP accumulator differs from original" severity failure;
         end if;
         if idct_mac_enable = '1' and idct_calcP_ena = '1' then
            assert resize(resize(idct_calcP_mul12,13),16) = idct_calcP_mul12
               and resize(resize(idct_calcP_mul22,13),16) = idct_calcP_mul22
               report "MDEC scale coefficient exceeds its source range" severity failure;
         end if;
      end if;
   end process;
   -- synthesis translate_on
'''


def simulation_only(text):
    return '-- synthesis translate_off\n' + text + '\n-- synthesis translate_on'


# The release adds this licence credit above the same MDEC (prepare_mdec_beetle).
# It is a comment: the qualified source is what follows it, and the credit is kept.
CREDIT = ('-- GPL-2.0 MiSTer PSX_MiSTer mdec.vhd; constants and arithmetic after Mednafen psx/mdec.c\n'
          '-- (Beetle PSX e57860d8, GPL-2.0-or-later).\n')


def split_credit(text):
    return (CREDIT, text[len(CREDIT):]) if text.startswith(CREDIT) else ('', text)


def qualified(text, expected=None):
    """The text is the qualified source, with or without the release's credit lines."""
    return hashlib.sha256(split_credit(text)[1].encode()).hexdigest() == (expected or SOURCE)


def transform(text):
    credit, text = split_credit(text)
    if hashlib.sha256(text.encode()).hexdigest() != SOURCE:
        raise ValueError('Requires selected full-audio baseline MDEC')
    return credit + transform_body(text)


def transform_body(text):
    text = once(text, 'architecture arch of mdec is\n',
                'architecture arch of mdec is\n' + COMPONENT)
    for line in (
        '   signal idct_calc1_mul1     : signed(31 downto 0);',
        '   signal idct_calc1_mul2     : signed(31 downto 0);',
        '            idct_calc1_mul1    <= idct_calcP_mul11 * idct_calcP_mul12;',
        '            idct_calc1_mul2    <= idct_calcP_mul21 * idct_calcP_mul22;',
    ):
        text = once(text,line,simulation_only(line))
    start = text.index("            if (idct_calc1_ena = '1') then\n")
    end = text.index('            -- stage 2 write\n',start)
    original = text[start:end]
    if original.count('idct_sum <=') != 2:
        raise ValueError('Accumulator assignment inventory changed')
    text = text[:start] + simulation_only(original.replace('idct_sum','idct_sum_expected')) + '\n\n' + text[end:]
    return once(text,'\nbegin\n','\nbegin\n'+INSTANCE)


def prepare(source, output):
    source, output = source.resolve(), output.resolve()
    manifest = load_source(source, source/'fit-inputs.json')
    selected = [r for r in manifest['quartus_sources']
                if r['assignment'] == 'VHDL_FILE' and Path(r['path']).name == 'mdec.vhd']
    if len(selected) != 1 or not qualified(Path(selected[0]['path']).read_text()):
        raise ValueError('Expected exact assigned MDEC source')
    manifest = snapshot(source, source/'fit-inputs.json',output,2)
    before = inputs(output)
    mdec = next(Path(r['path']) for r in before
                if r['assignment'] == 'VHDL_FILE' and Path(r['path']).name == 'mdec.vhd')
    mdec.write_text(transform(mdec.read_text()))
    wrapper = output/'psx_mdec_idct_mac.sv'
    wrapper.write_bytes((ROOT/'rtl/pocket/psx_mdec_idct_mac.sv').read_bytes())
    qsf = output/'apf_top.qsf'
    qsf.write_text(qsf.read_text()+f'\nset_global_assignment -name SYSTEMVERILOG_FILE "{wrapper}" -library work\n')
    after = inputs(output)
    b = {r['path']:r for r in before}; a = {r['path']:r for r in after}
    if a.keys()-b.keys() != {str(wrapper)} or b.keys()-a.keys():
        raise ValueError('Unexpected source closure addition/removal')
    if {p for p in b if a[p] != b[p]} != {str(mdec),str(qsf)}:
        raise ValueError('Expected changed MDEC source and wrapper assignment')
    hold_guard(output,after)
    manifest['quartus_sources'] = after
    manifest['project_sha256']['.qsf'] = digest(qsf)
    (output/'fit-inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
    load_source(output,output/'fit-inputs.json')
    return mdec


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    print(prepare(args.source,args.output))
