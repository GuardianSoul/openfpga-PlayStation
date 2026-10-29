#!/usr/bin/env python3
"""Pin project copies of timer.vhd and gpu_cpu2vram.vhd.

Without save states a release project compiles these two straight from
references/PSX_MiSTer. prepare_timer_dotclock.py and
prepare_gpu_cpu2vram_pace.py patch only files inside the project, so this
step copies each into PROJECT/ref-pinned/ and points the QSF at the copy.
The copies are byte-identical; the later steps change them.

Usage: prepare_pin_refs.py PROJECT  (post-prepare step, before those two)
"""
import re
import shutil
import sys
from pathlib import Path

NAMES = ('timer.vhd', 'gpu_cpu2vram.vhd')


def prepare_project(project):
    project = Path(project).resolve(); qsf = project / 'apf_top.qsf'; q = qsf.read_text()
    out = project / 'ref-pinned'; out.mkdir()
    for name in NAMES:
        old = re.findall(rf'"([^"]*/references/[^"]*/{re.escape(name)})"', q)
        if len(old) != 1: raise SystemExit(f'pin-refs: expected one references/ {name}, found {old}')
        shutil.copyfile(old[0], out / name)
        q = q.replace(f'"{old[0]}"', f'"{out / name}"')
    qsf.write_text(q)
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print('pinned', prepare_project(sys.argv[1]))
