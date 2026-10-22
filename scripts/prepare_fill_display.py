#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fill centred on the GPU's display range (psx_apf_video_wide FILL_DISP).

Post-prepare step for a project built with --fill-center. MiSTer's GPU knows
which lines it shows from VRAM (the vertical display range a game sets with
GP1(07h)), but with the fixed TV window (fixedVBlank) its vblank output is the
fixed window instead. This exports the range as one more video signal,
dispblank (high outside it: the GPU's inVsync),
through the existing video path, and turns on FILL_DISP so Fill centres on the
range rather than on lit picture lines (docs/pocket-video-log-2026-10-22-probe9.md,
Crash's Naughty Dog screen and title on rc1a).

Files, each taken as the project's QSF assigns it (edited in place when it is
already a project copy, else copied into PROJECT/fill-display/):
  pGPU.vhd               tvideoout_out gets dispblank
  gpu_videoout_async.vhd drives it: inVsync (the range; MiSTer's non-fixed vblank
                         also caps it at vDisplayMax lines, but that counter is
                         otherwise dead with the fixed window and cost 8-15 LABs,
                         rc1b; the fixed window clips the range anyway)
  gpu_videoout_sync.vhd  drives it: inVsync (its own vblank)
  gpu.vhd                video_dispblank output
  psx_top.vhd            dispblank output
  psx_mister_psx_top_resource.vhd   dispblank output
  core_top.sv            psx_dispblank to the video module, FILL_DISP(1), FILL_LEAD(1)
                         (480i Fill: the lead line is the line above the window, not black)
Same clock as vblank all the way (clkvid); no new registers outside the video
module. Usage: prepare_fill_display.py PROJECT
"""
import re
import shutil
import sys
from pathlib import Path

UPSTREAM = Path(__file__).resolve().parents[1]/'references/PSX_MiSTer'

EDITS = {
    'pGPU.vhd': [('      hResMode          : std_logic_vector(2 downto 0);\n   end record; \n',
                  '      hResMode          : std_logic_vector(2 downto 0);\n'
                  '      dispblank         : std_logic;   -- PSXEarly fill display: outside the display range\n   end record; \n')],
    'gpu_videoout_async.vhd': [('   videoout_out.vblank         <= vblankFixed when',
                                '   -- PSXEarly fill display: the GPU\'s own vblank (the display range), also with the fixed window\n'
                                '   videoout_out.dispblank      <= videoout_reports.inVsync;\n'
                                '   videoout_out.vblank         <= vblankFixed when')],
    'gpu_videoout_sync.vhd': [('   videoout_out.vblank         <= videoout_reports.inVsync;\n',
                               '   videoout_out.vblank         <= videoout_reports.inVsync;\n'
                               '   videoout_out.dispblank      <= videoout_reports.inVsync;   -- PSXEarly fill display\n')],
    'gpu.vhd': [("      video_vblank         : out std_logic := '0';\n",
                 "      video_vblank         : out std_logic := '0';\n"
                 "      video_dispblank      : out std_logic := '1';   -- PSXEarly fill display\n"),
                ('   video_vblank         <= videoout_out.vblank;',
                 '   video_dispblank      <= videoout_out.dispblank;   -- PSXEarly fill display\n'
                 '   video_vblank         <= videoout_out.vblank;')],
    'psx_top.vhd': [('      vblank                : out std_logic;\n',
                     '      vblank                : out std_logic;\n'
                     '      dispblank             : out std_logic;   -- PSXEarly fill display\n'),
                    ('      video_vblank         => vblank,\n',
                     '      video_vblank         => vblank,\n'
                     '      video_dispblank      => dispblank,   -- PSXEarly fill display\n')],
    'psx_mister_psx_top_resource.vhd': [('    vblank : out std_logic;\n',
                                         '    vblank : out std_logic;\n    dispblank : out std_logic;   -- PSXEarly fill display\n'),
                                        ('        vblank => vblank,\n',
                                         '        vblank => vblank,\n        dispblank => dispblank,\n')],
    'core_top.sv': [('    wire psx_vblank;\n', '    wire psx_vblank;\n    wire psx_dispblank;   // --fill-display: outside the GPU display range\n'),
                    ('\n.vblank(psx_vblank),\n', '\n.vblank(psx_vblank),\n.dispblank(psx_dispblank),\n'),
                    ('.vblank(psx_vblank), .interlace(video_interlace),',
                     '.vblank(psx_vblank), .dispblank(psx_dispblank), .interlace(video_interlace),'),
                    ('    psx_apf_video_wide #(', '    psx_apf_video_wide #(.FILL_DISP(1), .FILL_LEAD(1), ')],
}


def patch(name, text):
    for old, new in EDITS[name]:
        if text.count(old) != 1:
            raise ValueError(f'--fill-display: {name} anchor not unique: {old.strip()[:70]}')
        text = text.replace(old, new)
    return text


def prepare_project(out):
    out = Path(out).resolve(); qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
    if '.FILL_CENTER(1)' not in (out/'core_top.sv').read_text():
        raise ValueError('--fill-display needs --fill-center')
    folder = out/'fill-display'
    for name in EDITS:
        hits = re.findall(rf'"([^"]*/{re.escape(name)})"', q)
        if name == 'core_top.sv' and not hits: hits = [str(out/'core_top.sv')]
        if len(hits) != 1: raise ValueError(f'--fill-display: expected one {name} in the project, found {hits}')
        src = Path(hits[0])
        if src.resolve().is_relative_to(out):
            src.write_text(patch(name, src.read_text()))
        else:
            folder.mkdir(exist_ok=True); dst = folder/name
            dst.write_text(patch(name, src.read_text()))
            q = q.replace(f'"{hits[0]}"', f'"{dst}"')
    if folder.is_dir(): shutil.copyfile(UPSTREAM/'LICENSE', folder/'COPYING.GPL2')
    qsf_path.write_text(q)


if __name__ == '__main__':
    prepare_project(sys.argv[1])
