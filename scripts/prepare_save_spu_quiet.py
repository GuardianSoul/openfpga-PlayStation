#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "spu-quiet": the quiet poll before every SPU RAM page command.

Rule (kept in the routine source next to the command):
  Poll quiet before every SPU RAM page command, one request at a time; a save-port
  request before quiet corrupts an SPU access in progress.
Before the step the routine polled SPU_HOLD's quiet bit once (spu_wait) and then sent
all SPU RAM page commands (COMMAND 7 / 5) relying on the hold. The step polls the quiet
bit again before each of them; "one request at a time" is page_cmd's wait for the
copy-busy bit after every command, which was there already.
Only stage 2 changes (4 instructions, 16 bytes unpacked).
Bench check "spu-quiet-poll" (run_save_stage2_mark.py, generic QUIET_RULE): every SPU RAM
page command must follow a read of SPU_HOLD that returned quiet since the command before.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'spu-quiet anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


OLD = '''\
# a0 -> SPU RAM page; SPU RAM -> staging page
spu_ram_get:
        li      $t9, 7
        b       page_cmd
        nop
# a0 -> SPU RAM page; staging page -> SPU RAM
spu_ram_put:
        li      $t9, 5
        b       page_cmd
        nop
'''
NEW = '''\
# RULE (spu-quiet, scripts/prepare_save_spu_quiet.py): poll quiet before every SPU RAM
# page command, one request at a time; a save-port request before quiet corrupts an SPU
# access in progress. The poll is label 93; "one at a time" is page_cmd's busy wait.
# a0 -> SPU RAM page; SPU RAM -> staging page
spu_ram_get:
        b       93f
        li      $t9, 7
# a0 -> SPU RAM page; staging page -> SPU RAM
spu_ram_put:
        li      $t9, 5
93:     lbu     $t8, SPU_HOLD($k0)      # [0]: the SPU is quiet (held, voice loop idle)
        nop
        andi    $t8, $t8, 1
        beqz    $t8, 93b
        nop
        b       page_cmd
        nop
'''

FAULTS = {
    # the page command is issued without the quiet poll (the routine as it was)
    'spu-ram-no-quiet-poll': ('93:     lbu     $t8, SPU_HOLD($k0)      # [0]: the SPU is quiet (held, voice loop idle)\n'
                              '        nop\n        andi    $t8, $t8, 1\n        beqz    $t8, 93b\n        nop\n',
                              '93:                                     # FAULT: no quiet poll before the command\n'),
}


def firmware_text(text):
    import re
    if 'RULE (spu-quiet' in text:
        raise ValueError('spu-quiet is applied already')
    if re.search(r'^93:|[ ,]93[fb]\b', text, flags=re.M):
        raise ValueError('numeric label 93 is in use')
    return once(text, OLD, NEW)


def fault_text(text, name):
    return once(text, *FAULTS[name])
