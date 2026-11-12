#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""CPU bench of the routine step "checksum" (prepare_save_checksum_routine.py) for
run_save_project_cpu.py --routine-step checksum (after compress). docs/save-states-finish/checksum-card.md.

  firmware_text / FAULTS / fault_text   the step and its planted faults on the routine text
  bench_text    sim/reference/tb_staged_save_cpu.vhd (after the other steps' transforms, stage2-mark's
                STATE_FAULT among them) with the device's CSUM as prepare_save_checksum.py builds it:
                  every word of a buffer page copy (either direction) is added to cs rotated left by one
                  bit, in word order; a write to 0x1830-0x1833 clears cs; a read of 0x1830-0x1833 gives
                  its bytes, bit 31 flipped while a load request is open and the model's overflow flag
                  is set; the flag clears at every FINISH write
                and two more STATE_FAULT values at the load request:
                  4  one word of the stream (buffer page 2, word 100) has bit 0 flipped: a word damaged
                     between the save buffer and the SD card, or a stale word
                  5  the overflow flag set: the card slot dropped a bridge word of the upload
                  6  page 1 word 300 (GTE control) bit 0 flipped
                  7  page 0 word 20 bit 0 flipped
                Scenarios (run_save_stage2_mark.py): 'load stream word' (4), 'load overflow' (5), 'load page1 word' (6)
                and 'load page0 word' (7) must be
                refused with the game as it was (FINISH 2, no guest write, final RAM equal to the reference).
"""
import prepare_save_checksum_routine as step

FAULTS = step.FAULTS
fault_text = step.fault_text
firmware_text = step.firmware_text


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'checksum bench anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def bench_text(text):
    text = once(text, '      variable tap7 : boolean := false;\n',
                '      variable tap7 : boolean := false;\n'
                "      variable cs : std_logic_vector(31 downto 0) := (others => '0');   -- checksum: the copy engine's sum\n"
                '      variable cs_ovf : boolean := false;                                -- checksum: the card slot\'s overflow\n')
    text = once(text, '         elsif a = 16#180B# then\n',
                '         elsif a >= 16#1830# and a < 16#1834# then   -- checksum: CSUM\n'
                '            w := cs;\n'
                "            if want_load = '1' and cs_ovf then w(31) := not w(31); end if;\n"
                '            exp1_q <= w(8 * (a mod 4) + 7 downto 8 * (a mod 4));\n'
                '         elsif a = 16#180B# then\n')
    text = once(text, '                     else buf(p * 512 + k) := page(k); end if;\n',
                '                     else buf(p * 512 + k) := page(k); end if;\n'
                "                     if cp_busy = '0' then   -- checksum (a command the busy engine ignores moves no word)\n"
                '                        cs := std_logic_vector(unsigned(cs(30 downto 0) & cs(31)) + unsigned(page(k)));\n'
                '                     end if;\n')
    text = once(text, '            elsif a = 16#1804# then\n',
                '            elsif a >= 16#1830# and a < 16#1834# then   -- checksum: a write clears CSUM\n'
                "               cs := (others => '0');\n"
                '            elsif a = 16#1804# then\n')
    text = once(text, '            elsif a = 16#1803# then\n',
                '            elsif a = 16#1803# then\n'
                '               cs_ovf := false;   -- checksum: the FINISH pulse clears the slot\'s overflow\n')
    text = once(text, "               want_load <= '1'; trap <= '1';\n",
                '               if STATE_FAULT = 4 then buf(2 * 512 + 100)(0) := not buf(2 * 512 + 100)(0); end if;   -- checksum\n'
                '               if STATE_FAULT = 5 then cs_ovf := true; end if;   -- checksum\n'
                '               if STATE_FAULT = 6 then buf(512 + 300)(0) := not buf(512 + 300)(0); end if;   -- checksum: page 1\n'
                '               if STATE_FAULT = 7 then buf(20)(0) := not buf(20)(0); end if;   -- checksum: page 0\n'
                "               want_load <= '1'; trap <= '1';\n")
    return text
