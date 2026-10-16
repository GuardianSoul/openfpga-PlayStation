#!/usr/bin/env python3
"""SPU ADSR tables without their 275 flip-flops.

spu.vhd registers adsr_ticks(0..4) (24 bits), adsr_step(0..4) (16) and
adsr_target(0..4) (15) on every clk1x edge from adsr_rate/adsr_dec and
adsrSetting, which are themselves combinational from adsrSetting. That is
the same as registering adsrSetting once (at the same place in the clocked
process, so with the same enables) and computing the three tables
combinationally from the registered copy: identical values on every cycle,
32 flip-flops instead of 275.
"""
import argparse
from pathlib import Path
import re


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('spu-adsr-lean: SPU source changed near: ' + old.strip().splitlines()[0][:80])
    return text.replace(old, new)


def adsr_lean_text(text):
    start = text.index('            -- adsr\n            for i in 0 to 4 loop\n')
    end_marker = "            adsr_target(ADSR_RELEASE) <= (others => '0');\n"
    end = text.index(end_marker, start) + len(end_marker)
    block = text[start:end]
    if text.count(block) != 1: raise ValueError('spu-adsr-lean: ADSR block not unique')
    text = text[:start] + '            adsrSetting_q <= adsrSetting;   -- spu-adsr-lean: tables follow combinationally\n' + text[end:]
    # the same assignments, combinational from the registered setting
    comb = block.replace('            -- adsr\n', '')
    comb = re.sub(r'(?m)^            ', '      ', comb)
    comb = comb.replace('adsr_rate(', 'adsr_rate_q(').replace('adsr_dec(', 'adsr_dec_q(').replace('adsrSetting(', 'adsrSetting_q(')
    text = _replace(text, '   signal reset_adsr    : std_logic := \'0\';\n',
                    '   signal reset_adsr    : std_logic := \'0\';\n'
                    '   -- spu-adsr-lean\n'
                    '   signal adsrSetting_q : unsigned(31 downto 0);\n'
                    '   signal adsr_dec_q    : std_logic_vector(0 to 4);\n'
                    '   signal adsr_rate_q   : tadsr_rate;\n')
    anchor = '   adsr_rate(ADSR_RELEASE)  <= adsrSetting(20 downto 16) & "00";\n'
    text = _replace(text, anchor, anchor + '''
   -- spu-adsr-lean: ADSR tables from the registered setting
   adsr_dec_q(ADSR_OFF    )   <= '1';
   adsr_dec_q(ADSR_ATTACK )   <= '0';
   adsr_dec_q(ADSR_DECAY  )   <= '1';
   adsr_dec_q(ADSR_SUSTAIN)   <= adsrSetting_q(30);
   adsr_dec_q(ADSR_RELEASE)   <= '1';
   adsr_rate_q(ADSR_OFF    )  <= (others => '0');
   adsr_rate_q(ADSR_ATTACK )  <= adsrSetting_q(14 downto 8);
   adsr_rate_q(ADSR_DECAY  )  <= '0' & adsrSetting_q(7 downto 4) & "00";
   adsr_rate_q(ADSR_SUSTAIN)  <= adsrSetting_q(28 downto 22);
   adsr_rate_q(ADSR_RELEASE)  <= adsrSetting_q(20 downto 16) & "00";

   process (adsr_rate_q, adsr_dec_q, adsrSetting_q)
      variable adsr_rateshift : integer range 0 to 11;
   begin
''' + comb + '''   end process;
''')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(adsr_lean_text(args.source.read_text()))


if __name__ == '__main__':
    main()
