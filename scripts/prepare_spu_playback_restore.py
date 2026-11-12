#!/usr/bin/env python3
"""Prepare own PXS8 with the reverb output latch rebuilt after Load."""
import argparse
import hashlib
import re
from pathlib import Path

from prepare_spu_lean_restore import (
    cpu_bench as lean_bench,
    SPU_SHA, clear_dac_on_restore, firmware_text as lean_firmware, freeze_restore, once,
)


def transform(text, stale_valid=False):
    if hashlib.sha256(text.encode()).hexdigest() != SPU_SHA:
        raise ValueError('Requires the measured PXS6 pending-write source')
    text = clear_dac_on_restore(text)
    # The ring preserves its 16 transfer words. These two bits only control
    # the speaker mix; the reverb RAM writes do not use rv_last_q.
    text = once(text, '& rvLastValid & std_logic_vector(reverbPartB_R)',
                '& "00" & std_logic_vector(reverbPartB_R)')
    validity = '"11"' if stale_valid else '"00"'
    text = once(text, '            rvLastValid <= global_scan_next(405 downto 404);',
                f'''            if SS_wren = '1' then rvLastValid <= {validity}; end if;''')
    text = once(text, """            if SS_Adr >= 48 and SS_Adr <= 62 then
               rvw_we := '1'; rvw_addr := to_integer(SS_Adr(3 downto 0)); rvw_data := SS_DataWrite(15 downto 0);
            end if;
""", '')
    text = once(text, """            elsif SS_Adr >= 48 and SS_Adr <= 62 then
               SS_DataRead <= x"0000" & rv_work_q;
""", '')
    text = once(text, "   rv_work_raddr <= to_integer(SS_Adr(3 downto 0)) when save_spu_quiet = '1' and SS_Adr >= 48 and SS_Adr <= 62 else rv_work_engine_raddr;\n", '')
    text = once(text, '   signal rv_work_engine_raddr : integer range 0 to 15;\n', '')
    text = re.sub(r'\brv_work_engine_raddr\b', 'rv_work_raddr', text)
    text = once(text, '   rv_work_q <= rvWork(rv_work_raddr);', '''   -- synthesis translate_off
   process(clk1x)
      variable prior_load_word : boolean := false;
   begin
      if rising_edge(clk1x) then
         if prior_load_word then
            assert rvLastValid = "00" report "Load left stale speaker history valid" severity failure;
         end if;
         prior_load_word := global_scan_access = '1' and SS_wren = '1' and SS_reset = '0';
      end if;
   end process;
   -- synthesis translate_on
   rv_work_q <= rvWork(rv_work_raddr);''')
    return text


def firmware_text(text):
    text = lean_firmware(text)
    text = once(text, '0x37535850       # "PXS7" (restart DAC output after Load)',
                '0x38535850       # "PXS8" (restart speaker history after Load)')
    return once(text, '.hword  32, 63,', '.hword  32, 48,')


def cpu_bench(text):
    text = lean_bench(text)
    text = once(text, 'return (n >= 32 and n <= 62) or', 'return (n >= 32 and n <= 47) or')
    text = once(text, 'x"37" report "Expected a PXS7 source state"',
                'x"38" report "Expected a PXS8 source state"')
    return once(text, 'x"36"; -- preceding PXS6 format', 'x"37"; -- preceding PXS7 format')


def prepare(source, output):
    source = source.resolve()
    return freeze_restore(
        source, output, transform((source / 'save-states/spu.vhd').read_text()),
        firmware_text((source / 'staged-ring.S').read_text()), 'playback',
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    import json
    print(json.dumps(prepare(args.source, args.output), indent=2))
