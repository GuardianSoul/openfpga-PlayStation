# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""lab-room step for memorymux.vhd: the MiSTer EXE side-load path goes.

Before: loadExe latches loadExe_latched; IDLE then runs EXEPATCHBIOSWRITE / WAIT (nine BIOS
patch words from exe_initial_pc / gp / stackpointer) and EXECOPYREAD / WRITE (copies
exe_file_size bytes from SDRAM 0x800 to exe_load_address, 32-bit execopycnt), then pulses
reset_exe.

Why it can go: the Pocket top (psx_mister_psx_top_resource.vhd) ties loadExe and every exe_*
input to '0', so loadExe_latched never leaves '0' and IDLE never enters the four EXE states.
Quartus folds loadExe_latched to GND but keeps the states, the 32-bit counter, its adders
and compare, and the extra ram_Adr / ram_dataWrite sources (map: ram_dataWrite is a 17:1
mux). After: the four states, exestep, execopycnt and loadExe_latched are gone; reset_exe
keeps its default '0' (as before, since it was only set at the end of an EXE copy). The ports
stay, unused.

Check: the RC whole-system benches never set loadExe; the planted faults below must be
caught by the memorymux lockstep (scripts/run_lab_room_exe.py).
"""

import re

TAG = 'lab-room-exe'

ENUM = re.compile(r"WAITING,\s*\n\s*\n\s*EXEPATCHBIOSWRITE,\s*\n\s*EXEPATCHBIOSWAIT,\s*\n\s*EXECOPYREAD,\s*\n\s*EXECOPYWRITE\s*\n")
DECLS = [
    re.compile(r"(?m)^\s*signal loadExe_latched\s*: std_logic := '0';\s*\n"),
    re.compile(r"(?m)^\s*signal exestep\s*: integer range 0 to 8;\s*\n"),
    re.compile(r"(?m)^\s*signal execopycnt\s*: unsigned\(31 downto 0\);\s*\n"),
    re.compile(r"(?m)^ *if \(loadExe = '1'\) then\s*\n\s*loadExe_latched <= '1';\s*\n\s*end if;\s*\n"),
]
BRANCH = re.compile(r"if \(loadExe_latched = '1'\) then\s*state\s*<= EXEPATCHBIOSWRITE;\s*exestep\s*<= 0;\s*"
                    r"execopycnt <= \(others => '0'\);\s*elsif \(")
ARMS_START = '-- #################################################\n-- ##################### EXE loading'
ARMS_END = '\n               when others => null;'

# Planted faults (bench only), applied to the transformed text
FAULTS = {
    # a write to SDRAM loses its upper byte enable
    'be-lost': ("ram_be        <= mem_writeMask;", "ram_be        <= mem_writeMask and \"0111\";"),
    # IDLE no longer services a request while the write FIFO is non-empty
    'fifo-ignored': (" or writeFifo_Empty = '0')) then", ")) then"),
}


def sub_once(pattern, new, text, what):
    text, n = pattern.subn(new, text)
    if n != 1:
        raise ValueError(f'{TAG}: expected one {what}, found {n}')
    return text


def transform(text):
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    text = sub_once(ENUM, 'WAITING\n', text, 'EXE enum block')
    for i, pattern in enumerate(DECLS):
        text = sub_once(pattern, '', text, f'declaration/latch {i}')
    text = sub_once(BRANCH, '-- lab-room-exe: no EXE side-load (loadExe tied to 0)\n                  if (', text, 'IDLE EXE branch')
    start = text.index(ARMS_START)
    if text.count(ARMS_START) != 1:
        raise ValueError(f'{TAG}: expected one EXE arm block')
    end = text.index(ARMS_END, start)
    text = text[:start] + text[end + 1:]
    for name in ('EXEPATCH', 'EXECOPY', 'execopycnt', 'exestep', 'loadExe_latched'):
        if name in text:
            raise ValueError(f'{TAG}: {name} left')
    return text


def fault_text(text, name):
    old, new = FAULTS[name]
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: fault {name}: expected one match, found {text.count(old)}')
    return text.replace(old, new)
