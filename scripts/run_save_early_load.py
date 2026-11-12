#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Routine-level bench of the early-load step (scripts/prepare_save_early_load.py): the routine's
own load-decision code (magic, page count, game-id, answer, remount, cd_ready, the answer at the
end) and the save's name copy, cut out of the routine text, assembled with its equates and macros
and run on the MIPS I model of run_save_mdec_tap.py against MODELS of the save device's disc
registers and of the CD controller (below). Seconds, not hours; the CPU RTL is not in it.

Disc model: DISC (0x180B) = the disc last selected; DISC_SET write: another disc than the selected
one starts a remount, which ends LOOKS reads of DISC_SET later -- with `served_after_answer`
only once FINISH has been written (the Pocket serves no data slot while a load is unanswered);
`wrong` = the cue reader cannot open another disc (settles with the wrong-disc bit, disc kept).
CD model: Getstat answers INT3 with the stat byte and closes the shell of a drive with a disc;
GetID answers INT3 then INT2 and starts the motor, or INT5 without a disc; Pause INT3 + INT2;
Setmode / Setfilter / Demute INT3. Flags must be acked before the next answer.

Usage: run_save_early_load.py [--routine DIR]    (DIR of prepare_save_early_load.py --routine)
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_save_early_load as step
from run_save_mdec_tap import Mips, Stop
from tool_paths import find_tool

EXP1 = 0xBF000000
PAGE = 0x1000
NAME = step.NAME_ADDRESS
ID_WORD = 198          # disc-id (prepare_save_disc_id.py)
DISC_IDS = (0x139023, 0x145566, 0x1FFFFF, 0x100001, 0x100002, 0x100003, 0x100004, 0x100005)
POLLS = 0x10000        # bench-only DISC_POLLS (the routine has 0x00800000)
LOOKS = 300            # DISC_SET reads a remount takes


def cut(text, start, end, inclusive=False):
    if text.count(start) != 1:
        raise ValueError(f'block start not unique ({text.count(start)}): {start[:60]!r}')
    a = text.index(start); b = text.index(end, a + len(start))
    return text[a:b + (len(end) if inclusive else 0)]


def blocks(text):
    """(code, base, symbols) of the harness around the routine's blocks."""
    text = text.replace('        .equ    DISC_POLLS, 0x00800000', f'        .equ    DISC_POLLS, 0x{POLLS:08X}')
    equates = ''.join(l + '\n' for l in text.splitlines() if re.match(r'\s+\.equ\s', l))
    macros = ''.join(m[0] + '\n' for m in re.finditer(r'^\s+\.macro\s+(STUB|TIMEOUT_START|TIMEOUT_STEP)\b.*?^\s+\.endm', text, flags=re.M | re.S))
    decide = cut(text, '        lw      $t0, PAGE($k0)\n        lui     $t1, %hi(MAGIC_R1)\n', '        # R1: memory control first')
    bad = cut(text, 'bad:\n', '# R1 load: a0 = disc - 1')
    mount = cut(text, 'disc_mount:\n', '# R1 save: v0 = 0 with the MDEC idle')
    cd = cut(text, '# a0 = command | parameter count << 8', '# end of a load (page 0 in the staging page')
    has_id = 'early-load game-id' in text
    save_start = '        # early-load game-id: the kernel\'s boot name'
    if 'disc-id' in text:                                 # the identity's read is the line before (or its planted fault)
        save_start = text[text.rindex('\n', 0, text.index(save_start) - 1) + 1:text.index(save_start)] + save_start
    save = cut(text, save_start, '        # R1: registers a cold boot') if has_id else ''
    tail = '        b       answer\n        li      $t0, 1\n' if 'answer:' in text else '        li      $t0, 1\n        sb      $t0, FINISH($k0)\n'
    source = ('        .set    noreorder\n        .set    noat\n        .text\n' + equates + macros +
              '        .globl  entry\nentry:\nblock_load:\n' + decide + '        break\n' +
              'block_end:\n' + tail + '        break\n' +
              'block_save:\n' + save + '        break\n' +
              'retry:\n        break\n' + bad + mount + cd +
              # get_page: the scratch page = `bad` (v1 marks it); another stored page (the waits step's look at
              # the CD FIFO count): word 0 of the staging page reads 0 until page 0 is asked for again
              'get_page:\n        beqz    $a0, 2f\n        li      $t8, SCRATCH\n        beq     $a0, $t8, 1f\n        nop\n'
              '        lw      $gp, PAGE($k0)\n        jr      $ra\n        sw      $zero, PAGE($k0)\n'
              '2:      beqz    $gp, 3f\n        nop\n        sw      $gp, PAGE($k0)\n3:      jr      $ra\n        move    $gp, $zero\n'
              '1:      jr      $ra\n        li      $v1, 0x0BAD\nrestore:\n        break\n'
              + ('cz_load:\n        jr      $ra\n        nop\n' if 'cz_load' in text else ''))
    link = 'SECTIONS { .text 0x80200000 : { *(.text) } /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.comment) } }\n'
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'b.S').write_text(source); (tmp / 'b.ld').write_text(link)
        tool = lambda n: find_tool(n) or n
        subprocess.run([tool('llvm-mc'), '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', str(tmp / 'b.S'), '-o', str(tmp / 'b.o')], check=True)
        subprocess.run([tool('ld.lld'), '-m', 'elf32ltsmip', '-T', str(tmp / 'b.ld'), '-e', 'entry', '--no-dynamic-linker', '-o', str(tmp / 'b.elf'), str(tmp / 'b.o')], check=True)
        subprocess.run([tool('llvm-objcopy'), '-O', 'binary', '-j', '.text', str(tmp / 'b.elf'), str(tmp / 'b.bin')], check=True)
        nm = subprocess.run([tool('llvm-nm'), str(tmp / 'b.elf')], check=True, capture_output=True, text=True).stdout
        code = (tmp / 'b.bin').read_bytes()
    return code, 0x80200000, {l.split()[2]: int(l.split()[0], 16) for l in nm.splitlines() if len(l.split()) == 3}


def name_words(name):
    data = name.encode().ljust(20, b'\0')[:20]
    return [int.from_bytes(data[i:i + 4], 'little') for i in range(0, 20, 4)]


class Machine:
    def __init__(self, magic, state_disc=0, menu_disc=0, state_name=None, ram_name=None, served_after_answer=True, wrong=False,
                 motor=True, shell_open=False, has_disc=True, irqena=0x1F, index=1, pages=(3, 2, 2), state_id=None, disc_ids=DISC_IDS):
        self.page = bytearray(0x800)
        # disc-id: the identity tap reads the mounted disc's; the state has its own disc's unless given (0: an old state)
        self.disc_ids = disc_ids
        self.page[4 * ID_WORD:4 * ID_WORD + 4] = (disc_ids[state_disc & 7] if state_id is None else state_id).to_bytes(4, 'little')
        self.page[0:4] = (magic + state_disc).to_bytes(4, 'little')
        self.regs = {18: pages[0] + 2, 19: pages[1], 20: pages[2]}                      # s2 = N + 2, s3 = R, s4 = S
        self.page[4 * 38:4 * 38 + 4] = (sum(pages) + 9).to_bytes(4, 'little')          # no page left out
        if state_name is not None:
            for i, w in enumerate(name_words(state_name)):
                self.page[4 * (step.NAME_WORD + i):4 * (step.NAME_WORD + i) + 4] = w.to_bytes(4, 'little')
        self.ram = dict(zip(range(NAME, NAME + 20, 4), name_words(ram_name or '')))
        self.selected, self.drive, self.setting, self.silent = menu_disc, menu_disc, menu_disc, 0
        self.swap_left, self.wrong_bit, self.wrong, self.served_after_answer = 0, 0, wrong, served_after_answer
        self.finishes, self.disc_set_writes, self.commands, self.writes_before_decision = [], [], [], 0
        self.motor, self.shell_open, self.has_disc = motor, shell_open, has_disc
        self.index, self.irqena, self.flag, self.resp, self.queue, self.params, self.wait = index, irqena, 0, [], [], [], 0
        self.other_writes = []

    # ---- disc
    def settled(self):
        return self.swap_left == 0 and self.setting == self.selected

    def disc_look(self):
        if self.swap_left and (self.finishes or not self.served_after_answer):
            self.swap_left -= 1
            if self.swap_left == 0:
                if self.wrong: self.wrong_bit = 1
                else: self.drive = self.selected; self.wrong_bit = 0
        return (self.wrong_bit << 1) | int(self.settled())

    # ---- CD
    def stat(self):
        return (2 if self.motor else 0) | (0x10 if self.shell_open else 0)

    def cd_step(self):
        if self.flag == 0 and self.queue:
            if self.wait: self.wait -= 1; return
            kind, action = self.queue.pop(0)
            if action: action()
            self.flag = kind; self.resp = [self.stat() | (1 if kind == 5 else 0)] + ([0x80] if kind == 5 else [])
            self.wait = 3

    def command(self, c):
        self.commands.append(c); self.params = []; self.wait = 5
        if c == 0x01:
            self.queue.append((3, None))
            if self.has_disc: self.queue.append((0, lambda: setattr(self, 'shell_open', False)))
        elif c == 0x1A:
            if self.has_disc: self.queue += [(3, None), (2, lambda: setattr(self, 'motor', True))]
            else: self.queue.append((5, None))
        elif c == 0x09: self.queue += [(3, None), (2, None)]
        elif c in (0x0E, 0x0D, 0x0C, 0x02, 0x06): self.queue.append((3, None))
        else: raise Stop(f'unexpected CD command {c:02x}')

    def cd_tick(self):
        # kind 0 entries are silent state changes after an answer
        while self.queue and self.queue[0][0] == 0 and self.flag == 0:
            self.queue.pop(0)[1]()
        self.cd_step()

    def read(self, address, size):
        if size > 1:
            if address in self.ram: return self.ram[address]
            if 0x80000000 <= address < 0x80200000: return 0x5A5A5A5A      # other kernel RAM
            return sum(self.read(address + i, 1) << 8 * i for i in range(size))
        if NAME <= address < NAME + 20:
            return (self.ram[address & ~3] >> 8 * (address & 3)) & 0xFF
        if 0x1F801800 <= address <= 0x1F801803:
            a = address & 3
            self.cd_tick()
            if a == 0: return 0x18 | (0x20 if self.resp else 0) | self.index
            if a == 1: return self.resp.pop(0) if self.resp else 0
            if a == 3: return 0xE0 | (self.flag if self.index & 1 else self.irqena)
            return 0
        offset = address - EXP1
        if PAGE <= offset < PAGE + 0x800: return self.page[offset - PAGE]
        if offset == 0x180B: return self.selected
        if offset == 0x1823: return self.disc_look()
        if offset == 0x181F:
            self.cd_tick()
            return int(bool(self.queue))
        if offset == 0x1818: return self.stat()
        if 0x1819 <= offset <= 0x181E: return 0                # mode, filter, LBA: not looked at by these blocks
        if 0x1828 <= offset <= 0x182B: return (self.disc_ids[self.drive] >> 8 * (offset - 0x1828)) & 0xFF
        raise Stop(f'read of {address:08x}')

    def write(self, address, size, value):
        if size > 1:
            for i in range(size): self.write(address + i, 1, (value >> 8 * i) & 0xFF)
            return
        if 0x1F801800 <= address <= 0x1F801803:
            a = address & 3
            if a == 0: self.index = value & 3
            elif a == 1 and self.index == 0: self.command(value)
            elif a == 2 and self.index == 0: self.params.append(value)
            elif a == 2 and self.index == 1: self.irqena = value & 0x1F
            elif a == 3 and self.index == 1: self.flag &= ~value & 0x1F
            return
        offset = address - EXP1
        if PAGE <= offset < PAGE + 0x800: self.page[offset - PAGE] = value
        elif offset == 0x1803: self.finishes.append(value)
        elif offset == 0x1823:
            self.disc_set_writes.append(value); self.silent = value >> 3 & 1
            if value & 7 != self.setting:
                self.setting = value & 7; self.selected = self.setting; self.swap_left = LOOKS; self.wrong_bit = 0
        else: self.other_writes.append((address, value))


def run(code, base, sym, entry, machine, regs=None, limit=4_000_000):
    m = Mips(code, base, machine)
    m.r[26] = EXP1; m.r[25] = 0x1F800000; m.r[27] = 4          # k0, t9, k1 = a load
    for k, v in machine.regs.items(): m.r[k] = v
    for k, v in (regs or {}).items(): m.r[k] = v
    m.run(sym[entry], limit)
    return m


def load_case(built, **kw):
    """One load decision: (verdict, machine). verdict: 'go' (reached R1's first write), 'refused' (FINISH 2
    and back to the game), 'quiet' (back to the game without an error answer)."""
    code, base, sym, magic = built
    machine = Machine(magic, **kw)
    m = run(code, base, sym, 'block_load', machine)
    went_bad = m.r[3] == 0x0BAD
    if not went_bad:
        # the end of the load: the answer
        m2 = Mips(code, base, machine); m2.r[26] = EXP1; m2.r[27] = m.r[27]; m2.run(sym['block_end'])
        return 'go', machine
    return ('refused' if machine.finishes and machine.finishes[-1] == 2 else 'quiet'), machine


def magic_of(text):
    v = {}
    for l in text.splitlines():
        m = re.match(r'\s+\.equ\s+(\w+),\s*([^#]+)', l)
        if m and m[1] in ('MAGIC', 'MAGIC_R1'):
            v[m[1]] = eval(m[2].strip(), {}, dict(v))
    return v['MAGIC_R1']


def build(text):
    code, base, sym = blocks(text)
    return code, base, sym, magic_of(text)


FF7, FF8, FF7D2 = 'cdrom:\\SCUS_941.63;1', 'cdrom:\\SLUS_008.92;1', 'cdrom:\\SCUS_941.64;1'


def checks(text, old_text=None):
    """Every case on the routine text: list of (name, passed, detail)."""
    b = build(text)
    rows = []
    def row(name, ok, detail): rows.append((name, bool(ok), detail))
    def case(name, want, expect, **kw):
        verdict, mc = load_case(b, **kw)
        detail = dict(verdict=verdict, finishes=mc.finishes, disc=mc.selected, disc_set=mc.disc_set_writes, commands=[f'{c:02x}' for c in mc.commands],
                      irqena=mc.irqena, index=mc.index)
        row(name, verdict == want and all(detail[k] == v for k, v in expect.items()), detail)
    has = lambda part: f'early-load {part}' in text
    # (d) answer
    if has('answer'):
        case('other disc, served only after the answer: early OK, remount, load', 'go', dict(finishes=[1], disc=0), state_disc=0, menu_disc=1)
        case('other disc that cannot be opened: early OK, menu disc back, no load, no second answer', 'quiet', dict(finishes=[1], disc=1),
             state_disc=0, menu_disc=1, wrong=True)
        case('same disc: one answer, at the end', 'go', dict(finishes=[1], disc_set=[0]), state_disc=0, menu_disc=0)
        case('bad magic: clean error', 'refused', dict(finishes=[2], disc_set=[], commands=[]), state_disc=9)
    else:
        case('TODAY other disc, served only after the answer: times out, error', 'refused', dict(finishes=[2], disc=1), state_disc=0, menu_disc=1)
        case('TODAY other disc, served at once (the old bench model): loads', 'go', dict(finishes=[1], disc=0), state_disc=0, menu_disc=1, served_after_answer=False)
    # (a) ready
    if has('ready'):
        case('drive ready: no GetID', 'go', dict(commands=['01', '0e', '0d', '0c'], irqena=0), motor=True)
        case('motor off (BIOS stopped the drive): GetID, then load', 'go', dict(commands=['01', '1a', '01', '0e', '0d', '0c']), motor=False)
        case('fresh drive, shell flag still set (a wake): GetID, then load', 'go', dict(finishes=[1]), motor=False, shell_open=True)
        case('no disc: clean error, IRQENA and index back', 'refused', dict(finishes=[2], irqena=0x1F, index=1), motor=False, shell_open=True, has_disc=False)
        case('no disc with IRQENA 0x1D, index 2: back as they were', 'refused', dict(irqena=0x1D, index=2), motor=False, has_disc=False, irqena=0x1D, index=2)
    # (e) game-id
    if has('game-id'):
        refuse = dict(finishes=[2], disc_set=[], commands=[])
        case('same game: loads', 'go', {}, state_name=FF7, ram_name=FF7)
        case('foreign game, same disc number: refused before any write', 'refused', refuse, state_name=FF7, ram_name=FF8)
        case('same letters, other serial, same disc number: refused', 'refused', refuse, state_name=FF7, ram_name=FF7D2)
        case('old state (no name): loads', 'go', {}, state_name=None, ram_name=FF8)
        case('no boot name in RAM yet (a wake): loads', 'go', {}, state_name=FF7, ram_name='')
        case('RAM name not a serial: loads', 'go', {}, state_name=FF7, ram_name='cdrom:\\MAIN.EXE;1')
        case('state name not a serial: loads', 'go', {}, state_name='cdrom:\\PSX.EXE;1', ram_name=FF8)
        case('same four letters, other disc: accepted, goes to the disc switch', 'go', dict(disc=0, disc_set=[8, 0]), state_name=FF7, ram_name=FF7D2, state_disc=0, menu_disc=1)
        case('other letters, other disc: refused', 'refused', dict(finishes=[2], disc_set=[]), state_name=FF7, ram_name=FF8, state_disc=0, menu_disc=1)
        # the save's copy
        code, base, sym, magic = b
        mc = Machine(magic, ram_name=FF7)
        run(code, base, sym, 'block_save', mc)
        mc2 = Machine(magic, ram_name=FF8)
        run(code, base, sym, 'block_save', mc2)
        got2 = bytes(mc2.page[4 * step.NAME_WORD:4 * step.NAME_WORD + 20])
        row('save stores another game\'s name too', got2 == FF8.encode().ljust(20, b'\0'), dict(stored=got2.decode('latin1')))
        got = bytes(mc.page[4 * step.NAME_WORD:4 * step.NAME_WORD + 20])
        row('save stores the boot name in words 193-197', got == FF7.encode().ljust(20, b'\0'), dict(stored=got.decode('latin1')))
    # disc identity (prepare_save_disc_id.py): the fallback while the running machine has no boot name yet
    if 'disc-id' in text:
        other = 0x123456
        case('BIOS (no running name), foreign Memory, same disc number: refused before any drive access, clean error', 'refused',
             dict(finishes=[2], commands=[], disc=0), state_name=FF8, ram_name='', state_id=other)
        case('BIOS, same game (identity equal): loads', 'go', dict(finishes=[1]), state_name=FF7, ram_name='')
        case('BIOS, old Memory (no identity): refused', 'refused', dict(finishes=[2], commands=[]), state_name=None, ram_name='', state_id=0)
        case('BIOS, Memory with a name but no identity: refused', 'refused', dict(finishes=[2], commands=[]), state_name=FF7, ram_name='', state_id=0)
        case('in game, old Memory (no name, no identity): loads as before', 'go', {}, state_name=None, ram_name=FF7, state_id=0)
        case('in game, same name, another identity: the name decides, loads', 'go', {}, state_name=FF7, ram_name=FF7, state_id=other)
        case('in game, foreign name, equal identity: the name decides, refused', 'refused', dict(finishes=[2], commands=[]), state_name=FF7, ram_name=FF8)
        case('in game, running name not a serial, another identity: as before, loads', 'go', {}, state_name=FF7, ram_name='cdrom:\\MAIN.EXE;1', state_id=other)
        case('BIOS, other disc of the same game: early OK, switch, identity equal, loads', 'go', dict(finishes=[1], disc=0),
             state_name=FF7, ram_name='', state_disc=0, menu_disc=1)
        case('BIOS, other disc number, identity differs after the switch: menu disc back, no load, no second answer', 'quiet',
             dict(finishes=[1], disc=1, commands=[]), state_name=FF7, ram_name='', state_disc=0, menu_disc=1, state_id=other)
        case('BIOS, no table yet (identity 0) against a Memory with one: refused', 'refused', dict(finishes=[2], commands=[]),
             state_name=FF7, ram_name='', state_id=other, disc_ids=(0,) * 8)
        code, base, sym, magic = b
        for drive in (0, 1):
            mc = Machine(magic, ram_name=FF7, menu_disc=drive, state_id=0)
            try:
                run(code, base, sym, 'block_save', mc)
                got = int.from_bytes(mc.page[4 * ID_WORD:4 * ID_WORD + 4], 'little')
            except Stop as error:
                got = str(error)
            row(f'save stores the identity of disc {drive + 1} in word 198', got == DISC_IDS[drive], dict(stored=got if isinstance(got, str) else f'{got:06X}'))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    from prepare_save_final_routine import tail_text      # SAVE_FINAL_ROUTINE=f|g: the cases on the final routine
    base = step.base_text()
    plain = step.firmware_text(base)
    new = tail_text(plain, 'early-load')
    report = {'routine': os.environ.get('SAVE_FINAL_ROUTINE') or 'c2', 'routine_sha256': hashlib.sha256(new.encode()).hexdigest()}
    ok = True
    for name, text in (('today', base), ('early-load', new)):
        report[name] = [dict(case=n, passed=p, **d) for n, p, d in checks(text)]
        for r in report[name]:
            print(f"{name}: {'ok  ' if r['passed'] else 'FAIL'} {r['case']}: {r['verdict'] if 'verdict' in r else r}  finishes={r.get('finishes')} commands={r.get('commands')}")
            ok &= r['passed']
    report['faults'] = {}
    planted = [(fault, tail_text(step.fault_text(plain, fault), 'early-load')) for fault in step.FAULTS]
    if 'disc-id' in new:                                  # the disc identity step's faults, on the final routine
        import prepare_save_disc_id as disc_id
        planted += [(fault, disc_id.fault_text(new, fault)) for fault in disc_id.FAULTS]
    for fault, text in planted:
        failed = [n for n, p, _ in checks(text) if not p]
        report['faults'][fault] = failed
        print(f"fault {fault}: {'caught by ' + str(len(failed)) + ' case(s): ' + failed[0] if failed else 'NOT caught'}")
        ok &= bool(failed)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=1) + '\n')
    print('PASS save early load (routine model)' if ok else 'FAIL save early load (routine model)')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
