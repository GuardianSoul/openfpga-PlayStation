#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Timer0 counts every dot clock (ps1-tests timers: 127 vs 198 per 1000 cycles).

gpu_videoout_async reports the dot clock to the timers as videoout_out.ce, a
one-clkvid-cycle strobe (53.69 MHz) that reaches clk1x (33.87 MHz) through a
plain level synchronizer. A strobe shorter than a clk1x period is missed
whenever no clk1x edge falls inside it, so Timer0 in dot-clock mode ran about
36% slow (127 vs 198 ticks per 1000 CPU cycles at 320 px). The video side now
toggles a level on every dot, and the timer counts each change of that level.
The level holds for at least 4 clkvid cycles (640 px), so every change is
sampled. Same synchronizer and crossing as before; +1 FF on each side.

Guest-visible: Timer0 in dot-clock mode now counts at the PS1 dot rate.
Usage: prepare_timer_dotclock.py PROJECT  (post-prepare step; patches the
gpu_videoout_async.vhd and timer.vhd the project's apf_top.qsf compiles)
"""
import re
import sys
from pathlib import Path

TAG = 'prepare_timer_dotclock'
SIG_OLD = '   signal hCropCount          : unsigned(11 downto 0) := (others => \'0\');\n'
SIG_NEW = SIG_OLD + f"   signal dotToggle           : std_logic := '0';  -- {TAG}\n"
DOT_OLD = '   videoout_reports.dotclock <= videoout_out.ce;\n'
DOT_NEW = (f'   -- {TAG}: a level that changes on every dot, so the clk1x timer\n'
           '   -- cannot miss a one-clkvid-cycle strobe\n'
           '   process (clkvid)\n'
           '   begin\n'
           '      if rising_edge(clkvid) then\n'
           "         if (videoout_out.ce = '1') then\n"
           '            dotToggle <= not dotToggle;\n'
           '         end if;\n'
           '      end if;\n'
           '   end process;\n'
           '   videoout_reports.dotclock <= dotToggle;\n')
TICK_OLD = '               newTick(0) := dotclock and (not dotclock_1);\n'
TICK_NEW = f'               newTick(0) := dotclock xor dotclock_1; -- {TAG}: every change of the dot level\n'


def one(text, old, new, what):
    if text.count(old) != 1:
        raise ValueError(f'{what}: expected exactly one match, found {text.count(old)}')
    return text.replace(old, new)


def video_text(text):
    if TAG in text:
        return text
    return one(one(text, SIG_OLD, SIG_NEW, 'hCropCount signal'), DOT_OLD, DOT_NEW, 'dotclock report')


def timer_text(text):
    if TAG in text:
        return text
    return one(text, TICK_OLD, TICK_NEW, 'Timer0 dot clock tick')


def apply(project):
    project = Path(project).resolve()
    qsf = (project / 'apf_top.qsf').read_text()
    done = []
    for name, fn in (('gpu_videoout_async.vhd', video_text), ('timer.vhd', timer_text)):
        paths = sorted({Path(p) for p in re.findall(r'VHDL_FILE "([^"]*/' + re.escape(name) + r')"', qsf)})
        if len(paths) != 1:
            raise SystemExit(f'{TAG}: expected one compiled {name}, found {len(paths)}')
        path = paths[0].resolve()
        if project not in path.parents:
            raise SystemExit(f'{TAG}: {path} is outside the project; refusing to patch a shared source')
        try:
            path.write_text(fn(path.read_text()))
        except ValueError as error:
            raise SystemExit(f'{TAG}: {path}: {error}')
        done.append(path)
    return done


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split('Usage: ')[1])
    for p in apply(sys.argv[1]):
        print('patched', p)
