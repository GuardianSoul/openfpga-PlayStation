"""prepare --pal-line-rate: PAL lines and frames at the PAL console's rate.

A PAL PlayStation's GPU runs at 53.203425 MHz and an NTSC one at 53.693175
MHz; both CPUs run at 33.8688 MHz. MiSTer retunes its video PLL to match
(PSX.sv pll_cfg on isPal), but the Pocket's video_pll stays at 53.693166 MHz,
so MiSTer's 3406-clock PAL line came out 0.9% short: 15,764 Hz lines and
50.21 Hz frames instead of 15,620 Hz and 49.75 Hz (hardware perf logs:
"50.20 fps" with 313 HS per VS). Games pace themselves on VBlank/HBlank,
which the CPU and timers see against the unchanged CPU clock, so PAL games
ran 0.9% fast. A 3437-clock line at 53.693166 MHz is 64.012 us (PAL 64.017):
15,622 Hz lines, 49.75 Hz progressive frames (console 49.747). Display ranges (hDisplayRange,
at most 3,168 clocks for PAL) and the pixel dividers are unchanged, so every
line keeps its pixels; only the horizontal blank is 31 clocks longer. NTSC
and pal60 keep 3413.
"""
import re
import shutil
from pathlib import Path

UPSTREAM = Path(__file__).resolve().parents[1]/'references/PSX_MiSTer'

EDITS = [('   signal htotal                       : integer range 3406 to 3413;',
          '   signal htotal                       : integer range 3413 to 3437;  -- PSXEarly --pal-line-rate'),
         ('               htotal <= 3406;',
          '               htotal <= 3437;  -- PSXEarly --pal-line-rate: 64.01 us at 53.693166 MHz (PAL 3406 at 53.203425 MHz)')]


def patch(text):
    for old, new in EDITS:
        if text.count(old) != 1: raise ValueError(f'--pal-line-rate: gpu_videoout_async changed: {old.strip()}')
        text = text.replace(old, new)
    return text


def prepare_project(out):
    """Patch whichever gpu_videoout_async the project compiles (after the CDC copies)."""
    out = Path(out); qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
    vids = re.findall(r'"([^"]*/gpu_videoout_async[^"/]*\.vhd)"', q)
    if len(vids) != 1: raise ValueError('--pal-line-rate: expected one gpu_videoout_async in the project')
    copy = out/'pal-line-rate'/'gpu_videoout_async.vhd'; copy.parent.mkdir(exist_ok=True)
    copy.write_text(patch(Path(vids[0]).read_text())); shutil.copyfile(UPSTREAM/'LICENSE', copy.parent/'COPYING.GPL2')
    qsf_path.write_text(q.replace(f'"{vids[0]}"', f'"{copy}"'))
