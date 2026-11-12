#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Whole-GPU lockstep of gpu-req-hoist (prepare_gpu_req_hoist.py).

The GPU of a frozen project (entity gpu and everything below it, as its
fit-inputs.json lists them) is compiled twice with GHDL: library lref from
the reviewed sources, library lcand with the step applied to the eight files.
sim/reference/tb_gpu_req_hoist.vhd runs both with identical inputs and
compares every output port (and the seven internal request lines) at every
falling clk2x and clkvid edge. The environment follows the reference only: a
CPU bus and DMA driver fed from a stimulus file, a DDR/VRAM model with random
busy, read latency and gaps, random vram_pause, random ce and savestate_busy.

Stimulus (--programs N, --seed S): random GP0/GP1 traffic: fills, CPU-to-VRAM
uploads (bus and DMA), VRAM-to-CPU readbacks (bus and DMA), VRAM-to-VRAM
copies (also wide and wrapping), flat/shaded/textured triangles and quads,
rectangles and sprites, lines and polylines, with random semi-transparency,
mask, texture page, window, clip, offset and display settings, render24,
texture filter and noTexture, GP1 resets, FIFO clears and hard resets, and
back-pressure settings that change every few programs.

Simulation-only edits, the same on both sides (sim_text): translate_off
sections removed (as Quartus reads the files), 'Z' defaults on the inout
divider ports of gpu_poly / gpu_line, the signed span-start comparison of
gpu_poly, and one debug output port dbg_req on gpu.vhd (the seven gated
request lines). Vendor RAMs, the hold cell, the video config CDC and the two
DSP accumulators are models (sim/reference/gpu_req_hoist_models.vhd,
gpu_poly_dsp_models.vhd, MiSTer's sim dpram.vhd).

--fault NAME plants one of prepare_gpu_req_hoist.FAULTS in the candidate; the
run then must report a MISMATCH (exit 0 when the fault is caught).
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path

from prepare_gpu_req_hoist import FAULTS, SOURCE_SHA, fault_texts, reviewed, sha, transform
from prepare_resource_targets import hardware_text
from tool_paths import ROOT

PROJECT = Path(os.environ.get('PSX_REQ_HOIST_PROJECT', ROOT / 'build/fit/gpu-req-hoist/base'))
BENCH = ROOT / 'sim/reference/tb_gpu_req_hoist.vhd'
MODELS = [ROOT / 'sim/reference/gpu_poly_dsp_models.vhd', ROOT / 'sim/reference/gpu_req_hoist_models.vhd']
SIM_RAM = ROOT / 'references/PSX_MiSTer/sim/system/src/mem/dpram.vhd'
LOAD_LIMIT = float(os.environ.get("PSX_LOAD_LIMIT", 24.0))
# Compile order inside each GPU library.
GPU_FILES = ['pGPU.vhd', 'mul9s.vhd', 'divider.vhd', 'psx_gpu_recip_unit.vhd', 'psx_gpu_read_math.vhd',
             'gpu_fillVram.vhd', 'gpu_cpu2vram.vhd', 'gpu_vram2vram.vhd', 'gpu_vram2cpu.vhd', 'gpu_line.vhd',
             'gpu_rect.vhd', 'gpu_poly.vhd', 'gpu_dither.vhd', 'gpu_pixelpipeline.vhd', 'gpu_overlay.vhd',
             'gpu_crosshair.vhd', 'justifier_sensor.vhd', 'gpu_videoout_sync.vhd', 'gpu_videoout_async.vhd',
             'gpu_videoout.vhd', 'gpu.vhd']
MEM_FILES = ['SyncFifo.vhd', 'SyncFifoFallThrough.vhd']


def project_sources(project):
    rows = json.loads((project / 'fit-inputs.json').read_text())['quartus_sources']
    found = {}
    for name in GPU_FILES + MEM_FILES:
        match = [Path(r['path']) for r in rows if Path(r['path']).name == name]
        if len(match) != 1:
            raise SystemExit(f'{project}: expected one {name}, found {len(match)}')
        found[name] = match[0]
    return found


# ---- simulation-only edits (identical on both sides) -----------------------
PORT_OLD = ' : inout div_type; \n'
PORT_NEW = " : inout div_type := ('Z', 'Z', (others => 'Z'), (others => 'Z'), (others => 'Z'), (others => 'Z'));\n"
SPAN_OLD = 'to_integer(xStart(43 downto 32)) > drawingAreaRight'
SPAN_NEW = 'xStart(43 downto 32) > signed(resize(drawingAreaRight, 12))'
LAST_PORT = '      SS_Idle              : out std_logic\n   );\nend entity;\n'
DBG_AT = '   reqVRAMEnable <= vram2vram_reqVRAMEnable or '
DBG = ('   dbg_req <= videoout_reqVRAMEnable & pipeline_reqVRAMEnable & poly_reqVRAMEnable & rect_reqVRAMEnable '
       '& line_reqVRAMEnable & vram2cpu_reqVRAMEnable & vram2vram_reqVRAMEnable;\n')


def sim_text(name, text):
    text = hardware_text(text)
    if name in ('gpu_poly.vhd', 'gpu_line.vhd'):
        # An inout record port drives its default on the fields the entity never
        # assigns; 'U' would override the divider's answer in a VHDL simulation.
        if text.count(PORT_OLD) != 6: raise SystemExit(f'{name}: divider ports changed')
        text = text.replace(PORT_OLD, PORT_NEW)
    if name == 'gpu_poly.vhd':
        # A negative span start is not a NATURAL (run_gpu_dy_ram.hardware()).
        if text.count(SPAN_OLD) != 1: raise SystemExit('gpu_poly span-start comparison changed')
        text = text.replace(SPAN_OLD, SPAN_NEW)
    if name in ('gpu_videoout_async.vhd', 'gpu_videoout_sync.vhd'):
        # Low nine bits of a possibly negative line number (lines outside the displayed
        # area): to_unsigned of a negative integer stops the simulator; hardware
        # truncates (run_current_gpu_dy.adapt_video()).
        text, count = re.subn(r'to_unsigned\(([^;\n]*\b(?:vdispNew|vposNew|lineMax)\b[^;\n]*), 9\)',
                              r'resize(unsigned(to_signed(\1, 12)), 9)', text)
        if not count: raise SystemExit(f'{name}: line-number conversions changed')
    if name == 'gpu_videoout.vhd':
        # GHDL wants a locally static subtype on these integer actuals (run_gpu_dy_ram.hardware()).
        for expression, count in {'to_integer(videoout_request_clkvid.lineDisp)': 9,
                                  'to_integer(Gun1Y_screen)': 2, 'to_integer(Gun2Y_screen)': 2}.items():
            if text.count(expression) != count: raise SystemExit('gpu_videoout scalar associations changed')
            text = text.replace(expression, "integer'(" + expression + ')')
        text, count = re.subn(r'(i_pixel_out_x\s*=> )videoout_request_clkvid.xpos,',
                              r"\1integer'(videoout_request_clkvid.xpos),", text)
        if count != 5: raise SystemExit('gpu_videoout overlay association changed')
    if name == 'gpu.vhd':
        if text.count(LAST_PORT) != 1 or text.count(DBG_AT) != 1: raise SystemExit('gpu.vhd entity changed')
        text = text.replace(LAST_PORT, LAST_PORT.replace('\n   );', ';\n      dbg_req              : out std_logic_vector(6 downto 0)\n   );'))
        text = text.replace(DBG_AT, DBG + DBG_AT)
    return text


# ---- stimulus --------------------------------------------------------------
class Stim:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.lines = []
        self.programs = 0
        self.kinds = {}

    def op(self, code, a=0, b=0):
        self.lines.append(f'{code:08X} {a & 0x7FFFFFFF:08X} {b & 0xFFFFFFFF:08X}')

    def gp0(self, word): self.op(1, 0, word)
    def gp1(self, word): self.op(1, 4, word)
    def dma(self, word): self.op(8, 0, word)
    def idle(self, kiloticks=100): self.op(4, kiloticks)
    def delay(self, ticks): self.op(5, ticks)
    def setting(self, index, value): self.op(6, index, value)

    def count(self, kind):
        self.kinds[kind] = self.kinds.get(kind, 0) + 1

    # -- pieces
    def xy(self, x, y): return (y & 0xFFFF) << 16 | (x & 0xFFFF)

    def point(self):
        r = self.rng
        if r.random() < 0.9: return r.randint(-8, 340), r.randint(-8, 250)
        return r.randint(-200, 1100), r.randint(-100, 560)

    def texpage(self):
        r = self.rng
        return (r.randint(0, 15) | r.getrandbits(1) << 4 | r.randint(0, 3) << 5 | r.choice((0, 0, 1, 2)) << 7
                | r.getrandbits(1) << 9 | r.getrandbits(1) << 10 | (r.random() < 0.05) << 11)

    def clut(self):
        r = self.rng
        return r.randint(0, 63) | r.randint(0, 511) << 6

    def env(self):
        r = self.rng
        self.setting(16, r.choice((0, 0, 16, 64, 160, 230)))
        self.setting(17, r.choice((0, 1, 3, 10, 40)))
        self.setting(18, r.choice((0, 0, 20, 200, 2000, 8000)))
        self.setting(19, r.choice((2, 10, 60)))
        low = r.choice((1, 1, 2, 5, 15))
        self.setting(20, low)
        self.setting(21, low + r.choice((0, 3, 20)))
        self.setting(22, r.choice((0, 0, 30, 128)))
        # ce is never low while the GPU works: psx_top gates ce only after SS_Idle, and
        # gpu_poly loses a divider done pulse that arrives with ce low (it then waits for
        # its 2^20-clock watchdog; a property of the existing design).
        self.setting(23, 0)
        self.setting(24, r.choice((0, 40, 128, 250)))
        self.setting(25, r.choice((0, 0, 30, 600)))
        self.count('env')

    def inputs(self):
        """Core option inputs; changed only while the GPU is idle."""
        r = self.rng
        self.idle()
        self.setting(0, int(r.random() < 0.2))          # render24
        self.setting(1, int(r.random() < 0.1))          # noTexture
        self.setting(2, r.choice((0, 0, 0, 1, 2, 3)))   # textureFilter
        self.setting(3, r.getrandbits(2))
        self.setting(4, r.getrandbits(1))
        self.setting(5, r.getrandbits(1))               # ditherOff
        self.setting(6, r.getrandbits(1))               # dither24
        self.setting(7, int(r.random() < 0.1))          # drawSlow
        self.setting(8, int(r.random() < 0.1))          # oldGPU
        self.setting(9, int(r.random() < 0.08))         # interlaced480pHack
        self.setting(11, int(r.random() < 0.2))         # DMA_GPU_waiting
        self.count('inputs')

    def draw_config(self):
        r = self.rng
        self.gp0(0xE1000000 | self.texpage())
        if r.random() < 0.3: self.gp0(0xE2000000 | (r.getrandbits(20) if r.random() < 0.5 else 0))
        if r.random() < 0.4:
            if r.random() < 0.7:
                left, top = r.randint(0, 100), r.randint(0, 80)
                right, bottom = left + r.randint(0, 400), top + r.randint(0, 300)
            else:
                left, top, right, bottom = 0, 0, 1023, 511
            self.gp0(0xE3000000 | left | top << 10)
            self.gp0(0xE4000000 | min(right, 1023) | min(bottom, 511) << 10)
        if r.random() < 0.3:
            ox, oy = (r.randint(-40, 40), r.randint(-40, 40)) if r.random() < 0.8 else (0, 0)
            self.gp0(0xE5000000 | (ox & 0x7FF) | (oy & 0x7FF) << 11)
        if r.random() < 0.4: self.gp0(0xE6000000 | r.choice((0, 0, 1, 2, 3)))
        self.count('draw_config')

    def display_config(self):
        r = self.rng
        k = r.random()
        if k < 0.25: self.gp1(0x05000000 | r.randint(0, 700) | r.randint(0, 270) << 10)
        elif k < 0.40: self.gp1(0x06000000 | 0x260 | 0xC60 << 12)
        elif k < 0.55: self.gp1(0x07000000 | 0x010 | 0x100 << 10)
        elif k < 0.80:
            self.gp1(0x08000000 | r.randint(0, 3) | (r.random() < 0.3) << 2 | (r.random() < 0.15) << 4
                     | (r.random() < 0.2) << 5 | (r.random() < 0.1) << 6)
        elif k < 0.93: self.gp1(0x03000000 | int(r.random() < 0.3))
        else: self.gp1(0x04000000 | r.randint(0, 3))
        self.count('display_config')

    def fill(self):
        r = self.rng
        self.gp0(0x02000000 | r.getrandbits(24))
        self.gp0(self.xy(r.randint(0, 1023), r.randint(0, 511)))
        self.gp0(self.xy(r.choice((1, 16, 17, 40, 64, 200)), r.randint(1, 12)))
        self.count('fill')

    def upload(self):
        r = self.rng
        w, h = (r.randint(1, 24), r.randint(1, 5)) if r.random() < 0.9 else (r.randint(60, 120), 1)
        x = r.randint(0, 1023) if r.random() < 0.8 else r.randint(1010, 1023)
        words = [0xA0000000, self.xy(x, r.randint(0, 511)), self.xy(w, h)]
        words += [r.getrandbits(32) for _ in range((w * h + 1) // 2)]
        by_dma = r.random() < 0.3
        for i, word in enumerate(words):
            (self.dma if by_dma and i >= 3 else self.gp0)(word)
        self.count('upload_dma' if by_dma else 'upload')

    def readback(self):
        r = self.rng
        k = r.random()
        if k < 0.75: w, h = r.randint(1, 40), r.randint(1, 4)
        elif k < 0.85: w, h = r.randint(500, 1023), 1
        elif k < 0.90: w, h = 1024, 1
        else: w, h = r.randint(700, 1023), r.randint(3, 5)   # more than the read FIFO's near-full level (524 words)
        x = r.randint(0, 1023) if r.random() < 0.7 else r.randint(1000, 1023)
        words = (w * h + 1) // 2
        by_dma = r.random() < 0.3
        if by_dma: self.gp1(0x04000003)
        self.gp0(0xC0000000); self.gp0(self.xy(x, r.randint(0, 511))); self.gp0(self.xy(w & 1023, h))
        if r.random() < 0.3: self.op(3)                 # GPUSTAT read in between
        if r.random() < 0.4 or h >= 3: self.delay(r.choice((2000, 6000)) * (4 if h >= 3 else 1))   # a slow reader: the read FIFO fills
        self.op(9 if by_dma else 2, words)
        if by_dma: self.gp1(0x04000000 | r.choice((0, 2)))
        self.idle()
        self.count('readback_dma' if by_dma else 'readback')

    def copy(self):
        r = self.rng
        k = r.random()
        if k < 0.8: w, h = r.randint(1, 64), r.randint(1, 6)
        elif k < 0.93: w, h = r.randint(400, 1023), r.randint(1, 2)
        else: w, h = 1024, 1
        sx = r.randint(0, 1023) if r.random() < 0.7 else r.randint(990, 1023)
        dx = r.randint(0, 1023) if r.random() < 0.7 else r.randint(990, 1023)
        self.gp0(0x80000000); self.gp0(self.xy(sx, r.randint(0, 511))); self.gp0(self.xy(dx, r.randint(0, 511)))
        self.gp0(self.xy(w & 1023, h))
        self.count('copy')

    def polygon(self):
        r = self.rng
        shade, quad, tex = r.getrandbits(1), r.getrandbits(1), int(r.random() < 0.6)
        semi, raw = int(r.random() < 0.4), int(r.random() < 0.3)
        ax, ay = self.point()
        size = r.randint(1, 12) if r.random() < 0.7 else r.randint(13, 70)
        colors = [r.getrandbits(24) for _ in range(4)]
        self.gp0((0x20 | shade << 4 | quad << 3 | tex << 2 | semi << 1 | raw) << 24 | colors[0])
        for i in range(4 if quad else 3):
            if i and shade: self.gp0(colors[i])
            self.gp0(self.xy(ax + r.randint(0, size), ay + r.randint(0, size)))
            if tex:
                extra = self.clut() if i == 0 else self.texpage() if i == 1 else r.getrandbits(16)
                self.gp0(extra << 16 | r.getrandbits(16))
        self.count('polygon_textured' if tex else 'polygon')

    def rectangle(self):
        r = self.rng
        size, tex = r.choice((0, 0, 1, 2, 3)), int(r.random() < 0.6)
        semi, raw = int(r.random() < 0.4), int(r.random() < 0.3)
        self.gp0((0x60 | size << 3 | tex << 2 | semi << 1 | raw) << 24 | r.getrandbits(24))
        self.gp0(self.xy(*self.point()))
        if tex: self.gp0(self.clut() << 16 | r.getrandbits(16))
        if size == 0:
            self.gp0(self.xy(r.randint(0, 40) if r.random() < 0.9 else r.randint(41, 300), r.randint(0, 24)))
        self.count('sprite' if tex else 'rectangle')

    def line(self):
        r = self.rng
        shade, poly, semi = r.getrandbits(1), int(r.random() < 0.25), int(r.random() < 0.4)
        x, y = self.point()
        self.gp0((0x40 | shade << 4 | poly << 3 | semi << 1) << 24 | r.getrandbits(24))
        self.gp0(self.xy(x, y))
        for _ in range(r.randint(2, 4) if poly else 1):
            if shade: self.gp0(r.getrandbits(24))
            x, y = x + r.randint(-40, 40), y + r.randint(-30, 30)
            self.gp0(self.xy(x, y))
        if poly: self.gp0(0x55555555)
        self.count('polyline' if poly else 'line')

    def control(self):
        r = self.rng
        k = r.random()
        if k < 0.35: self.gp1(0x01000000); self.count('fifo_clear')
        elif k < 0.55: self.gp1(0x00000000); self.gp1(0x03000000); self.count('gp1_reset')
        elif k < 0.70: self.gp0(0x01000000); self.count('cache_clear')
        elif k < 0.85: self.op(7, r.randint(1, 8)); self.gp1(0x03000000); self.count('hard_reset')
        else: self.gp0(0x1F000000); self.gp1(0x02000000); self.count('irq')

    def program(self):
        r = self.rng
        self.op(10)
        self.programs += 1
        if self.programs % 12 == 1: self.env()
        if self.programs % 40 == 20: self.inputs()
        k = r.random()
        if k < 0.10: self.draw_config()
        elif k < 0.14: self.display_config()
        elif k < 0.19: self.fill()
        elif k < 0.27: self.upload()
        elif k < 0.35: self.readback(); return
        elif k < 0.43: self.copy()
        elif k < 0.68: self.polygon()
        elif k < 0.85: self.rectangle()
        elif k < 0.97: self.line()
        else: self.control()
        k = r.random()
        if k < 0.55: self.idle()
        elif k < 0.65: self.idle(); self.delay(r.choice((50, 400, 3000)))   # idle time for the display fetch
        elif k < 0.85: self.delay(r.randint(0, 40))                          # the next program queues behind
        # else: nothing, the next program follows at once


def write_stimulus(path, programs, seed):
    stim = Stim(seed)
    # display on, 320x240, so videoout fetches every line; unclipped drawing area
    for word in (0x00000000, 0x03000000, 0x08000001, 0x05000000, 0x06C60260, 0x07040010):
        stim.gp1(word)
    for word in (0xE1000000, 0xE3000000, 0xE407FFFF, 0xE5000000, 0xE6000000):
        stim.gp0(word)
    stim.idle()
    for _ in range(programs):
        stim.program()
    stim.idle()
    path.write_text('\n'.join(stim.lines) + '\n')
    return stim.kinds


# ---- build and run ---------------------------------------------------------
def build(directory, project, fault, ghdl, log):
    sources = project_sources(project)
    reference = {name: sources[name].read_text() for name in GPU_FILES}
    for name in SOURCE_SHA:
        if not reviewed(name, sha(reference[name])): raise SystemExit(f'{name} of the project is not a reviewed input')
    candidate = dict(reference)
    for name in SOURCE_SHA:
        candidate[name] = transform(name, reference[name])
    if fault: candidate = fault_texts(candidate, fault)
    libraries = ('mem', 'lref', 'lcand', 'work')
    for library in libraries: (directory / ('obj-' + library)).mkdir(parents=True, exist_ok=True)
    flags = ['-O2', '--std=08', '-frelaxed'] + [f'-Pobj-{library}' for library in libraries]

    def analyse(library, *paths):
        subprocess.run([ghdl, '-a', *flags, f'--work={library}', f'--workdir=obj-{library}', *map(str, paths)],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=900, check=True)
    (directory / 'mem').mkdir(exist_ok=True)
    mem = []
    for name in MEM_FILES:
        (directory / 'mem' / name).write_text(hardware_text(sources[name].read_text()))
        mem.append(directory / 'mem' / name)
    analyse('mem', SIM_RAM, *mem)
    for library, texts in (('lref', reference), ('lcand', candidate)):
        (directory / library).mkdir(exist_ok=True)
        paths = []
        for name in GPU_FILES:
            (directory / library / name).write_text(sim_text(name, texts[name]))
            paths.append(directory / library / name)
        # pGPU first (the DSP models do not need it; the RAM models are self-contained).
        analyse(library, SIM_RAM, *MODELS, *paths)
    analyse('work', BENCH)
    subprocess.run([ghdl, '-e', *flags, '--workdir=obj-work', 'tb_gpu_req_hoist'],
                   cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=900, check=True)
    return flags, {name: sha(candidate[name]) for name in SOURCE_SHA}


def run(programs, seed, fault, project, timeout, syncvideo, label=None):
    ghdl = os.environ.get('PSX_GHDL') or shutil.which('ghdl')
    if not ghdl: raise SystemExit('GHDL required (LLVM build of the oss-cad-suite first in PATH)')
    waited = 0
    while os.getloadavg()[0] >= LOAD_LIMIT:
        if waited >= 7200: raise SystemExit(f'1-minute load {os.getloadavg()[0]:.1f} >= {LOAD_LIMIT:.0f} for two hours: not starting')
        if not waited: print(f'1-minute load {os.getloadavg()[0]:.1f} >= {LOAD_LIMIT:.0f}: waiting', flush=True)
        time.sleep(30); waited += 30
    name = label or (('fault-' + fault) if fault else f'candidate-s{seed}-v{syncvideo}')
    directory = ROOT / 'build/sim/gpu-req-hoist' / name
    directory.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with (directory / 'build.log').open('w') as log:
        flags, shas = build(directory, project, fault, ghdl, log)
    kinds = write_stimulus(directory / 'stim.txt', programs, seed)
    if os.environ.get('PSX_BUILD_ONLY'):
        print(f'{name}: built in {round(time.time() - started, 1)} s (PSX_BUILD_ONLY)')
        return True
    command = ['nice', '-n', '10', ghdl, '-r', *flags, '--workdir=obj-work', 'tb_gpu_req_hoist',
               '-gSTIM=stim.txt', f'-gSEED={seed}', f'-gSYNCVIDEO={syncvideo}', '--ieee-asserts=disable', '--unbuffered']
    with (directory / 'run.log').open('w') as log:
        result = subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    seconds = round(time.time() - started, 1)
    text = (directory / 'run.log').read_text(errors='replace')
    passed = re.search(r'PASS gpu req hoist programs=(\d+) cycles=(\d+) compares=(\d+) outputs=(\d+) timeouts=(\d+).*', text)
    mismatch = re.search(r'MISMATCH (\S+) cycle=(\d+) program=(\d+)', text)
    ok = bool(result.returncode == 0 and passed and not mismatch)
    print(f'{name}: exit={result.returncode} seconds={seconds} log={directory / "run.log"}', flush=True)
    print(f'  stimulus: {programs} programs, seed {seed}, syncVideoOut {syncvideo}; '
          + ' '.join(f'{k}={v}' for k, v in sorted(kinds.items())))
    print('  candidate sha256: ' + ' '.join(f'{k}={v[:12]}' for k, v in shas.items()))
    for pattern in (r'COVERAGE .*', r'REQUESTS .*'):
        found = re.search(pattern, text)
        if found: print('  ' + found[0].strip())
    if passed: print(f'  {passed[0].strip()}')
    if mismatch: print(f'  first {mismatch[0]}')
    if not passed and not mismatch: print('  ' + '\n  '.join(text.strip().splitlines()[-8:]))
    if fault:
        caught = bool(mismatch)
        print(f'{"PASS" if caught else "FAIL"} planted fault {fault} {"caught" if caught else "NOT DETECTED"}', flush=True)
        return caught
    print(f'{"PASS" if ok else "FAIL"} gpu req hoist lockstep'
          + (f': {passed[1]} programs, {passed[2]} clk2x cycles, {passed[3]} compared edges x {passed[4]} outputs' if passed else ''),
          flush=True)
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--programs', type=int, default=1500)
    parser.add_argument('--seed', type=int, default=20261111)
    parser.add_argument('--syncvideo', type=int, choices=(0, 1), default=0,
                        help='syncVideoOut input (0: the clkvid video path, as on the Pocket)')
    parser.add_argument('--fault', choices=sorted(FAULTS))
    parser.add_argument('--faults', action='store_true', help='every planted fault, one simulation at a time')
    parser.add_argument('--project', type=Path, default=PROJECT)
    parser.add_argument('--label')
    parser.add_argument('--timeout', type=int, default=12 * 3600)
    args = parser.parse_args()
    faults = sorted(FAULTS) if args.faults else [args.fault]
    results = [run(args.programs, args.seed, fault, args.project.resolve(), args.timeout, args.syncvideo, args.label)
               for fault in faults]
    if not all(results): raise SystemExit(1)


if __name__ == '__main__':
    main()
