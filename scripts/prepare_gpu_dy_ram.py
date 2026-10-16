#!/usr/bin/env python3
"""gpu_poly's five y gradients in block RAM instead of 160 flip-flops.

dyR, dyG, dyB, dyU and dyV (32 bits each) are only read through the
lineGradient mux (odd gradientIndex) and, for dyU/dyV, by two comparisons in
the texture-filter decision. They come from divider quotients: CALCCOLOR3
copies div2/div4/div6 and CALCTEXTURE3 copies div2/div4 on every cycle until
the dividers finish. A divider's quotient then stays put until its next
start, which only CALCTEXTURE2 or the next primitive issues.

Here the five values live in a 5 x 32 block RAM read on the falling clk2x
edge at gradientIndex/2 (gradientIndex comes from registers, so it only
changes on rising edges). They are written one per cycle from the held
quotients in the first steps of CALCCOLOR4 (R, G, B at mulStep 0, 1, 2) and
CALCTEXTURE4 (U, V at 0, 1); the first reads are at mulStep 3, 4, 5 and 2, 3,
after the write lands. The two comparisons use flags captured with the U/V
writes. Pixel output and cycle timing are unchanged.
"""
import argparse
from pathlib import Path


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('gpu-dy-ram: gpu_poly source changed near: ' + old.strip().splitlines()[0][:80])
    return text.replace(old, new)


def dy_ram_text(text):
    for name in ('dyR', 'dyG', 'dyB', 'dyU', 'dyV'):
        text = _replace(text, f'   signal {name}                 : unsigned(31 downto 0) := (others => \'0\');',
                        f'   -- {name}: gpu-dy-ram entry')
    text = _replace(text, '   signal base_R              : unsigned(31 downto 0) := (others => \'0\');',
                    '''   -- gpu-dy-ram: y gradients R, G, B, U, V in a falling-edge block RAM
   type tDyRam is array(0 to 7) of unsigned(31 downto 0);
   signal dyRam               : tDyRam := (others => (others => '0'));
   RAMSTYLE_DECLARATION   attribute ramstyle of dyRam : signal is "M10K, no_rw_check";
   signal dyRead              : unsigned(31 downto 0) := (others => '0');
   signal dyWe                : std_logic := '0';
   signal dyWaddr             : integer range 0 to 7 := 0;
   signal dyWdata             : unsigned(31 downto 0) := (others => '0');
   signal dyU_zero            : std_logic := '1';
   signal dyV_4096            : std_logic := '0';

   signal base_R              : unsigned(31 downto 0) := (others => '0');''')
    text = _replace(text, '''   lineGradient <= dxR when gradientIndex=0 else dyR when gradientIndex=1 else
                   dxG when gradientIndex=2 else dyG when gradientIndex=3 else
                   dxB when gradientIndex=4 else dyB when gradientIndex=5 else
                   dxU when gradientIndex=6 else dyU when gradientIndex=7 else
                   dxV when gradientIndex=8 else dyV;''', '''   lineGradient <= dxR when gradientIndex=0 else dyRead when gradientIndex=1 else
                   dxG when gradientIndex=2 else dyRead when gradientIndex=3 else
                   dxB when gradientIndex=4 else dyRead when gradientIndex=5 else
                   dxU when gradientIndex=6 else dyRead when gradientIndex=7 else
                   dxV when gradientIndex=8 else dyRead;

   -- gpu-dy-ram: read at gradientIndex/2 on the falling edge, write a cycle
   -- after the state machine names the entry
   process (clk2x)
   begin
      if falling_edge(clk2x) then
         dyRead <= dyRam(gradientIndex / 2);
      end if;
   end process;
   process (clk2x)
   begin
      if rising_edge(clk2x) then
         if (dyWe = '1') then
            dyRam(dyWaddr) <= dyWdata;
         end if;
      end if;
   end process;''')
    for name in ('dyR <= unsigned(div2.quotient(31 downto 0));', 'dyG <= unsigned(div4.quotient(31 downto 0));',
                 'dyB <= unsigned(div6.quotient(31 downto 0));', 'dyU <= unsigned(div2.quotient(31 downto 0));',
                 'dyV <= unsigned(div4.quotient(31 downto 0));'):
        text = _replace(text, '                  ' + name + '\n', '')
    # CALCCOLOR4 / CALCTEXTURE4: write the held quotients, one per cycle
    text = _replace(text, '''               when CALCCOLOR4 =>
                  mulStep <= mulStep + 1;
''', '''               when CALCCOLOR4 =>
                  mulStep <= mulStep + 1;
                  dyWe <= '0';
                  if (mulStep = 0) then dyWe <= '1'; dyWaddr <= 0; dyWdata <= unsigned(div2.quotient(31 downto 0)); end if;
                  if (mulStep = 1) then dyWe <= '1'; dyWaddr <= 1; dyWdata <= unsigned(div4.quotient(31 downto 0)); end if;
                  if (mulStep = 2) then dyWe <= '1'; dyWaddr <= 2; dyWdata <= unsigned(div6.quotient(31 downto 0)); end if;
''')
    text = _replace(text, '''               when CALCTEXTURE4 =>
                  mulStep <= mulStep + 1;
''', '''               when CALCTEXTURE4 =>
                  mulStep <= mulStep + 1;
                  dyWe <= '0';
                  if (mulStep = 0) then
                     dyWe <= '1'; dyWaddr <= 3; dyWdata <= unsigned(div2.quotient(31 downto 0));
                     if (div2.quotient(31 downto 0) = 0) then dyU_zero <= '1'; else dyU_zero <= '0'; end if;
                  end if;
                  if (mulStep = 1) then
                     dyWe <= '1'; dyWaddr <= 4; dyWdata <= unsigned(div4.quotient(31 downto 0));
                     if (unsigned(div4.quotient(31 downto 0)) = 4096) then dyV_4096 <= '1'; else dyV_4096 <= '0'; end if;
                  end if;
''')
    text = _replace(text, "                  if (textureFilter2DOff = '1' and dyU = 0 and dxV = 0) then\n",
                    "                  if (textureFilter2DOff = '1' and dyU_zero = '1' and dxV = 0) then\n")
    text = _replace(text, "                     if (dxU = 4096 or dyV = 4096) then\n",
                    "                     if (dxU = 4096 or dyV_4096 = '1') then\n")
    # gpu-vertex-ram may already declare the ramstyle attribute
    declared = text.count('   attribute ramstyle : string;') > 0
    text = text.replace('   RAMSTYLE_DECLARATION', '' if declared else '   attribute ramstyle : string;\n')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(dy_ram_text(args.source.read_text()))


if __name__ == '__main__':
    main()
