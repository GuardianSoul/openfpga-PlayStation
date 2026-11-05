#!/usr/bin/env python3
"""Release MDEC with the rewritten Mednafen arithmetic (Beetle PSX mdec.c @ e57860d8).

Release projects take psx_top pre-built from build/claude-fit/F-nopool
(2026-10-14), so their mdec.vhd carries the previous mdec-mednafen transform:
upstream mdec.vhd -> mdec-mednafen (old) -> mdec-idct-pipe -> dpram-sdp, the
last in PROJECT/dpram-sdp/sources/N/mdec.vhd. This step rebuilds that chain
from the pinned upstream mdec.vhd with today's mednafen_text(), the same
idct-pipe text (and its header line) and the same dpram-sdp substitution
(prepare_dpram_sdp.inventory: each eligible dpram instance renamed to
pocket_dpram_sdp / _reverse by orientation), writes PROJECT/mdec-beetle/mdec.vhd
and repoints the QSF. It refuses a project whose mdec.vhd is not the old
chain's output (OLD_CHAIN) or whose upstream mdec.vhd is not the pinned one.
Run after prepare_dpram_sdp.py (any order with the other post-prepare steps).
Usage: prepare_mdec_beetle.py PROJECT
"""
import hashlib
from pathlib import Path
import re
import shutil
import sys

from prepare_dpram_sdp import inventory
from prepare_mdec_idct_pipe import idct_pipe_text
from prepare_mdec_mednafen import mednafen_text
from tool_paths import ROOT

MARK = 'mdec-beetle'
UPSTREAM_MDEC = 'references/PSX_MiSTer/rtl/mdec.vhd'
UPSTREAM_SHA = '3ad9c49f5ff2fc3d'   # pinned upstream mdec.vhd (sha256 prefix)
OLD_CHAIN = 'e6a662e56d02d121'      # old mednafen -> idct-pipe -> dpram-sdp, in every release since 0.5.x
PIPE_HEADER = '-- GPL-2.0 MDEC specialization; registered IDCT multiplier operands.\n'


def sha(text): return hashlib.sha256(text.encode()).hexdigest()[:16]


def sdp_text(text):
    """prepare_dpram_sdp's substitution on one file; returns (text, [(instance, orientation)])."""
    rows = [r for r in inventory(text) if r['orientation']]
    for row in sorted(rows, key=lambda r: r['start'], reverse=True):
        name = 'pocket_dpram_sdp_reverse' if row['orientation'] == 'Bwrite-Aread' else 'pocket_dpram_sdp'
        text = text[:row['start']] + name + text[row['end']:]
    return text, [(r['instance'], r['orientation']) for r in rows]


def chain_text(upstream, mednafen=mednafen_text):
    """The release mdec.vhd for an upstream text and a mednafen transform."""
    return sdp_text(PIPE_HEADER + idct_pipe_text(mednafen(upstream)))


def renamed(text):
    """Instances the dpram-sdp step renamed in a file, with their orientation."""
    return [(m[1], 'Bwrite-Aread' if m[2] else 'Awrite-Bread')
            for m in re.finditer(r'(\w+)\s*:\s*entity\s+(?:work|mem)\.pocket_dpram_sdp(_reverse)?\b', text, re.I)]


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = sorted(set(re.findall(r'"([^"]*/mdec\.vhd)"', q)))
    if len(hits) != 1: raise ValueError(f'{MARK}: expected one mdec.vhd in the project, found {hits}')
    current = Path(hits[0]).read_text()
    if sha(current) != OLD_CHAIN:
        raise ValueError(f'{MARK}: project mdec.vhd {sha(current)} is not the old release chain {OLD_CHAIN}')
    upstream = (ROOT/UPSTREAM_MDEC).read_text()
    if sha(upstream) != UPSTREAM_SHA: raise ValueError(f'{MARK}: upstream mdec.vhd {sha(upstream)} is not {UPSTREAM_SHA}')
    text, rows = chain_text(upstream)
    if rows != renamed(current) or rows != renamed(text):
        raise ValueError(f'{MARK}: dpram-sdp instances differ: new {rows}, project {renamed(current)}')
    for helper in ('pocket_dpram_sdp', 'pocket_dpram_sdp_reverse'):
        if not re.search(rf'"[^"]*/{helper}\.vhd" -library work', q): raise ValueError(f'{MARK}: {helper}.vhd not in the project')
    out = project/MARK/'mdec.vhd'; out.parent.mkdir(exist_ok=True)
    out.write_text('-- GPL-2.0 MiSTer PSX_MiSTer mdec.vhd; constants and arithmetic after Mednafen psx/mdec.c\n'
                   '-- (Beetle PSX e57860d8, GPL-2.0-or-later).\n' + text)
    shutil.copy2(ROOT/'references/PSX_MiSTer/LICENSE', out.parent/'COPYING.GPL2')
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
