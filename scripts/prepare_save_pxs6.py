#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Full-audio PXS6 save context as one recipe step (replaces the frozen chain to #1094).

Codex built the full-audio save graph as a chain of frozen projects
(test22a -> expanded context -> paired cyclic port -> pending feed). This step
applies the same qualified text transforms in that order to a project from
docs/ss-0.5.7-recipe.sh, so the graph comes from a clean checkout:

- CPU: private kernel-GPR context (prepare_save_kernel_gprs) and private
  breakpoint isolation (prepare_save_debug_context, the test24a entry-freeze
  fix). Their whole-file guards named a CPU that differed from the recipe's only
  in comments; here each edit's anchor must still match exactly once.
- EXP1 device: the SPU savestate-port extension at 0x1820-0x1822
  (prepare_save_full_context.exp1_text). The recipe's device already has the
  normal APF lifecycle that exp1_text's first edit restores from the test22a
  diagnostic, so an edit whose result is already present is skipped.
- psx_top: the SPU scan wiring (prepare_save_full_context.top_text).
- SPU: voice-record scan address, expanded context (prepare_spu_sample_save),
  the cyclic scalar port paired with PXS6 (prepare_spu_ring_paired) and the
  pending-write feed (prepare_spu_pending_feed). These keep their exact hash
  guards: the recipe's SPU is the one they were qualified on.
- Save ROM: staged_handler.S with the PXS6 edits (prepare_spu_ring_paired),
  built by build_staged_save_fw.

--check-against DIR compares every changed file with a frozen project (e.g.
#1094's) after normalising absolute paths and comment lines.
Usage: prepare_save_pxs6.py PROJECT [--check-against DIR]
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

FILES = {
    'cpu': 'cpu-div-shared/cpu.vhd',
    'exp1': 'save-exp1-pinned/psx_save_exp1.sv',
    'top': 'save-states/psx_top.vhd',
    'spu': 'save-states/spu.vhd',
    'rom': 'save-states/psx_save_rom40.hex',
}


def sha_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def anchored(once):
    """once() that skips an edit whose result is already in place (never a partial match)."""
    def tolerant(text, before, after):
        if text.count(before) == 1:
            return text.replace(before, after)
        if text.count(before) == 0 and after and after in text:
            return text
        return once(text, before, after)
    return tolerant


def cpu_text(text):
    import prepare_save_kernel_gprs as kg
    from prepare_save_debug_context import cpu_text as isolate_debug
    if 'exceptionTrap        <= \'0\';' in text:
        raise ValueError('CPU already has the kernel-GPR context')
    # prepare_save_kernel_gprs.cpu_text without its whole-file hash guard
    once = kg.once
    text = once(text, 'ce, execute_gte_readAddr, EXEBreakpoint)',
                'ce, execute_gte_readAddr, EXEBreakpoint, exceptionTrap)')
    anchor = '      calcMemAddr             := sharedSum(31 downto 0);\n'
    text = once(text, anchor, anchor + '''      -- Private zero-base addressing in the hidden handler only.
      if exceptionTrap = '1' and decodeSource1 = 0 then
         calcMemAddr(31 downto 16) := x"BF00";
      end if;
''')
    text = once(text, '            exception            <= unsigned(ss_in(24)(9 downto 5));\n',
                '            exception            <= unsigned(ss_in(24)(9 downto 5));\n'
                "            exceptionTrap        <= '0';\n")
    text = once(text, '               exceptionTrap       <= exceptionNew5 and trapRequest and not exceptionNew1;\n',
        '''               if exceptionNew /= 0 then
                  exceptionTrap <= exceptionNew5 and trapRequest and not exceptionNew1;
               elsif opcode1 = x"42000010" then -- RFE ends hidden context
                  exceptionTrap <= '0';
               end if;
''')
    return isolate_debug(text)


def exp1_text(text):
    import prepare_save_full_context as fc
    original = fc.once
    fc.once = anchored(original)
    try:
        return fc.exp1_text(text)
    finally:
        fc.once = original


def top_text(text):
    from prepare_save_full_context import top_text as wire
    return wire(text)


def spu_text(text):
    from prepare_save_full_context import once
    from prepare_spu_sample_save import transform as expanded
    import prepare_spu_global_ring as global_ring
    import prepare_spu_ring_paired as paired
    import prepare_spu_pending_feed as pending
    text = once(text, '   RamVoiceRecord_addrB(0)  <= to_unsigned(index, 5);',
                "   RamVoiceRecord_addrB(0)  <= SS_Adr(6 downto 2) when (save_spu_quiet = '1') else to_unsigned(index, 5);")
    text = expanded(text)
    if sha_text(text) != global_ring.BASE:
        raise ValueError('Expanded SPU differs from the qualified PXS5 SPU')
    text = paired.spu_text(text)
    if sha_text(text) != paired.PAIRED_SPU_SHA:
        raise ValueError('Paired SPU differs from the qualified PXS6 SPU')
    return pending.transform(text)


def rom_text(work):
    from prepare_spu_ring_paired import firmware_text
    from build_staged_save_fw import image_parts, rom1, rom2, rom40_hex, SRC
    fw = work / 'staged-pxs6.S'
    fw.write_text(firmware_text(SRC.read_text()))
    image, _ = image_parts(source=fw)
    return rom40_hex(rom1(image), rom2(image))


def normalise(text, root):
    text = text.replace(str(root), '<P>')
    text = re.sub(r'<P>/external/(?:home|Users)/[^/]+/Git/[^/"]+/', '<W>/', text)
    text = re.sub(r'/(?:home|Users)/[^/]+/Git/[^/"]+/', '<W>/', text)
    lines = [l for l in text.splitlines() if l.strip() and not re.match(r'\s*(--|//)', l)]
    return '\n'.join(re.sub(r'\s*(--|//).*$', '', l) for l in lines)


def prepare(project, check=None):
    project = Path(project).resolve()
    paths = {k: project / v for k, v in FILES.items()}
    for k, p in paths.items():
        if not p.is_file():
            raise ValueError(f'{FILES[k]} missing: run docs/ss-0.5.7-recipe.sh first')
    out = {
        'cpu': cpu_text(paths['cpu'].read_text()),
        'exp1': exp1_text(paths['exp1'].read_text()),
        'top': top_text(paths['top'].read_text()),
        'spu': spu_text(paths['spu'].read_text()),
        'rom': rom_text(project),
    }
    for k, text in out.items():
        paths[k].write_text(text)
    report = {FILES[k]: sha_text(t) for k, t in out.items()}
    if check:
        check = Path(check).resolve()
        for k, rel in FILES.items():
            a, b = normalise(out[k], project), normalise((check / rel).read_text(), check)
            if k == 'top':   # the recipe's psx_top declares/assigns save_cd_ce in another place
                a, b = sorted(a.splitlines()), sorted(b.splitlines())
            report['match ' + rel] = a == b
        if not all(v for k, v in report.items() if k.startswith('match ')):
            for k, v in report.items(): print(k, v)
            raise ValueError('PXS6 context differs from ' + str(check))
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('project', type=Path)
    ap.add_argument('--check-against', type=Path)
    a = ap.parse_args()
    for k, v in prepare(a.project, a.check_against).items():
        print(k, v)
