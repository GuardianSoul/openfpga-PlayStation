#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""core_top: --boot-waits-disc releases the CPU on the mount alone, with no timer.

--boot-waits-disc held the CPU in reset until the first disc mount reached
cd_top, or a timer ran out: 1.8 s, 14.46 s, then 14.46 s plus the cue reader's
mount with a 57.8 s cap. Each timer lost a race with a cold card (hardware test
2026-11-05: BIOS menu after a core switch or cold boot), since a CPU let go
before the mount sees no disc and boots to the menu.

The stream's first mount needs APF's allcomplete and the cue reader's answer,
and every mount reports, no disc included: the firmware only keeps a launch
waiting while APF reports card errors on a file the Chip32 loader listed. Slot
0 is required, so a launch always has a pick and APF's allcomplete comes. So
disc_settled is the whole condition, and the 30-bit counter goes.
Usage: prepare_boot_waits_mount.py PROJECT_OR_core_top.sv (after
prepare_pocket_early.py with --boot-waits-disc --cue-mount)
"""
import re
import sys
from pathlib import Path

OLD_REG = '    reg [29:0] disc_wait=0; reg disc_wait_done=0;\n'
OLD_ALWAYS = '''    always @(posedge clk_74a)
        if (!power_reset_n) begin disc_wait<=0; disc_wait_done<=0; end
        else if (reset_n && !disc_wait_done) begin disc_wait<=disc_wait+1'b1; if (&disc_wait) disc_wait_done<=1'b1; end
'''
GATE = re.compile(r'(boot_disc_gate<=[^;\n]*?)\(disc_settled_sync\[1\] \|\| disc_wait_done\)')


def transform(text):
    for name, part in (('disc_wait register', OLD_REG), ('disc_wait timer', OLD_ALWAYS), ('cue reader allcomplete', '    wire cue_s_allcomplete, cue_table_grant;\n')):
        if text.count(part) != 1: raise ValueError(f'{name} not found once (needs --boot-waits-disc and --cue-mount)')
    text, n = GATE.subn(lambda m: m[1] + 'disc_settled_sync[1]', text)
    if n != 1: raise ValueError('boot_disc_gate condition not found once')
    return text.replace(OLD_REG, '').replace(OLD_ALWAYS, '')


def main():
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    path = Path(sys.argv[1])
    if path.is_dir():
        # the core_top the project compiles (prepare_save_states.py moves it to save-states/)
        core = re.findall(r'"([^"]*/core_top\.sv)"', (path/'apf_top.qsf').read_text())
        if len(core) != 1: raise SystemExit(f'{path}: expected one core_top.sv in apf_top.qsf')
        path = Path(core[0])
    try: path.write_text(transform(path.read_text()))
    except ValueError as error: raise SystemExit(f'{path}: {error}')
    print(f'{path}: CPU waits for the mount, no timer')


if __name__ == '__main__':
    main()
