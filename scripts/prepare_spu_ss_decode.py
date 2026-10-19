"""Save-state SPU readback decoded from the words the save routine reads.

The save routine (rtl/pocket/save_fw/save_handler.S, SPU_SS) reads only port
words 2-6, 37, 40, 43 and the ranges 256-303, 320-369 and 384-479. The
upstream readback compares SS_Adr against four ranges and muxes all 64
ss_out words; after the save-states trim (prepare_save_states.py
spu_tap_text) only 2-6, 37 and 40 are live. Here the region comes from
SS_Adr bits 8/7/6 and the ss_out word from bits 5, 3 and 2-0, so every word
the routine reads returns the same value; words it never reads return
whatever their decode lands on. Restores only use the field bits.
Opt-in: apply_project() rewrites save-states/spu.vhd in place.
"""
from pathlib import Path
import sys

OLD = '''            if (SS_Adr < 64) then
               SS_DataRead <= ss_out(to_integer(SS_Adr));
            elsif (SS_Adr >= 256 and SS_Adr < 304) then
               SS_DataRead <= x"0000" & RamVoiceVolumes_dataB(1);    
            elsif (SS_Adr >= 320 and SS_Adr < 370) then
               SS_DataRead <= "0000000" & EnvCounterRam_dataB;
            elsif (SS_Adr >= 384 and SS_Adr < 480) then
'''
NEW = '''            -- spu-ss-decode: only the save routine's words (SPU_SS) are decoded
            if (SS_Adr(8) = '0') then
               if (SS_Adr(5) = '0') then
                  SS_DataRead <= ss_out(to_integer(SS_Adr(2 downto 0)));
               elsif (SS_Adr(3) = '0') then
                  SS_DataRead <= ss_out(37);
               else
                  SS_DataRead <= ss_out(40);
               end if;
            elsif (SS_Adr(7) = '0') then
               if (SS_Adr(6) = '0') then
                  SS_DataRead <= x"0000" & RamVoiceVolumes_dataB(1);
               else
                  SS_DataRead <= "0000000" & EnvCounterRam_dataB;
               end if;
            else
'''
OLD_TAIL = '''                   when others =>
                end case;
            else
               SS_DataRead <= (others => '0');
            end if;
'''
NEW_TAIL = '''                   when others =>
                end case;
            end if;
'''
def ss_decode_text(text):
    if 'save_spu_hold' not in text:
        raise ValueError('spu-ss-decode needs the save-states SPU tap')
    for old, new in ((OLD, NEW), (OLD_TAIL, NEW_TAIL)):
        if text.count(old) != 1: raise ValueError('save-states SPU readback changed: ' + old.strip()[:60])
        text = text.replace(old, new)
    return text


def apply_project(project):
    """Rewrite project/save-states/spu.vhd (written by prepare_save_states) in place."""
    path = Path(project) / 'save-states/spu.vhd'
    path.write_text(ss_decode_text(path.read_text()))
    return dict(path=str(path))


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit('usage: prepare_spu_ss_decode.py PROJECT')
    print(apply_project(sys.argv[1]))
