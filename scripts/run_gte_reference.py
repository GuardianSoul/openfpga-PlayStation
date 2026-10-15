#!/usr/bin/env python3
"""GTE check: the adapted Laxer GTE against an independent model (psx-spx).

Every GTE command (sf 0/1, lm 0/1, all 64 MVMVA selector combinations) runs
on register files that are either realistic (rotation matrices, vertices in
front of the camera, typical projection, lighting and depth-cue settings) or
fully random, or built from boundary values (to reach the 44-bit MAC
overflows). After each command all 64
registers are read back through the CPU interface and compared with
scripts/reference_gte_model.py. A planted model fault must be detected.
--candidate checks the reset-only candidate's GTE (register-file RAM
variants and the multicycle enable) instead.
"""
import collections
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from prepare_gte_adapter import adapter_sources, UPSTREAM
from reference_gte_model import GTE
from tool_paths import ROOT

COMMANDS = {0x01: 'RTPS', 0x06: 'NCLIP', 0x0C: 'OP', 0x10: 'DPCS', 0x11: 'INTPL', 0x12: 'MVMVA', 0x13: 'NCDS',
            0x14: 'CDP', 0x16: 'NCDT', 0x1B: 'NCCS', 0x1C: 'CC', 0x1E: 'NCS', 0x20: 'NCT', 0x28: 'SQR',
            0x29: 'DCPL', 0x2A: 'DPCT', 0x2D: 'AVSZ3', 0x2E: 'AVSZ4', 0x30: 'RTPT', 0x3D: 'GPF', 0x3E: 'GPL',
            0x3F: 'NCCT'}
SKIP_WRITE = (15, 28, 29, 31)  # SXYP pushes, IRGB overwrites IR1-3, read-only registers


def pack(lo, hi):
    return (lo & 0xFFFF) | ((hi & 0xFFFF) << 16)


def realistic(rng):
    r = [0] * 64
    for n in range(3):
        r[2 * n] = pack(rng.randint(-800, 800), rng.randint(-800, 800)); r[2 * n + 1] = rng.randint(-800, 800) & 0xFFFF
    r[6] = rng.getrandbits(32)
    r[7] = rng.randrange(0x10000)
    r[8] = rng.randint(0, 0x1000)
    for i in (9, 10, 11): r[i] = rng.randint(-4096, 4096) & 0xFFFF
    for i in (12, 13, 14): r[i] = pack(rng.randint(-300, 300), rng.randint(-300, 300))
    for i in (16, 17, 18, 19): r[i] = rng.randint(100, 20000)
    for i in (20, 21, 22, 23): r[i] = rng.getrandbits(32)
    for i in (24, 25, 26, 27): r[i] = rng.getrandbits(32) & 0x00FFFFFF
    r[30] = rng.getrandbits(32)
    a, b, c = (rng.uniform(0, 2 * math.pi) for _ in range(3))
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    m = [[cb * cc, -cb * sc, sb], [sa * sb * cc + ca * sc, -sa * sb * sc + ca * cc, -sa * cb],
         [-ca * sb * cc + sa * sc, ca * sb * sc + sa * cc, ca * cb]]
    rt = [int(round(v * 4096)) for row in m for v in row]
    def matrix(base, values):
        r[base] = pack(values[0], values[1]); r[base + 1] = pack(values[2], values[3]); r[base + 2] = pack(values[4], values[5])
        r[base + 3] = pack(values[6], values[7]); r[base + 4] = values[8] & 0xFFFF
    matrix(32, rt)
    r[37] = rng.randint(-2000, 2000) & 0xFFFFFFFF; r[38] = rng.randint(-2000, 2000) & 0xFFFFFFFF
    r[39] = rng.randint(800, 8000)
    matrix(40, [rng.randint(-4096, 4096) for _ in range(9)])
    for i in (45, 46, 47): r[i] = rng.randint(0, 4096 * 64)
    matrix(48, [rng.randint(0, 4096) for _ in range(9)])
    for i in (53, 54, 55): r[i] = rng.randint(0, 255 * 16)
    r[56] = (160 << 16) + rng.randint(-0x8000, 0x8000); r[57] = (120 << 16) + rng.randint(-0x8000, 0x8000)
    r[58] = rng.randint(200, 1200)
    r[59] = rng.randint(-0x1000, 0) & 0xFFFF; r[60] = rng.randint(0x0100000, 0x1400000)
    r[61] = 0x155; r[62] = 0x100
    r[63] = 0
    return r


def random_state(rng):
    return [rng.getrandbits(32) for _ in range(64)]


def extreme_state(rng):
    """Boundary values everywhere, to reach the 44-bit MAC overflows."""
    halves = (0x7FFF, 0x8000, 0x7FFE, 0x8001, 0, 1, 0xFFFF)
    words = (0x7FFFFFFF, 0x80000000, 0x7FFFF000, 0x80000FFF, 0, 0xFFFFFFFF, 0x00010000)
    return [rng.choice(words) if rng.random() < 0.5 else (rng.choice(halves) | (rng.choice(halves) << 16))
            for _ in range(64)]


PROFILES = {'realistic': realistic, 'random': random_state, 'extreme': extreme_state}


def cases(seed, count):
    rng = random.Random(seed)
    out = []
    for n in range(count):
        op = rng.choice(list(COMMANDS))
        command = op | (rng.randrange(2) << 19) | (rng.randrange(2) << 10)
        if op == 0x12:
            command |= rng.randrange(64) << 13
        profile = ('realistic', 'random', 'extreme')[n % 3]
        out.append((profile, command, PROFILES[profile](rng)))
    # Every MVMVA selector combination with both sf/lm, realistic and random.
    for selector in range(64):
        for profile in PROFILES:
            command = 0x12 | (selector << 13) | (rng.randrange(2) << 19) | (rng.randrange(2) << 10)
            out.append((profile, command, PROFILES[profile](rng)))
    return out


def vectors(case_list):
    """Each case writes every writable register (so the state is complete), runs the command and reads all 64."""
    lines = []
    for profile, command, state in case_list:
        lines += [f'W {index:02x} {state[index]:08x}' for index in range(64) if index not in SKIP_WRITE]
        lines.append(f'C 00 {command:08x}')
        lines += [f'R {index:02x} 00000000' for index in range(64)]
    return lines


def candidate_transform(adapted):
    """The reset-only candidate's GTE: control/data RAM, matrix RAM, add
    table, vector RAM and the multicycle enable, as prepare_full_pocket."""
    from prepare_gte_control_ram import control_ram_text, data_ram_text
    from prepare_gte_matrix_ram import compute_path_text, engine_text
    from prepare_gte_add_table import add_table_text
    from prepare_gte_vector_ram import vector_ram_text
    from prepare_gte_multicycle import adapter_text
    transforms = {'GTERegs.sv': lambda x: data_ram_text(control_ram_text(x)),
                  'GTEComputePath.sv': lambda x: vector_ram_text(add_table_text(compute_path_text(x))),
                  'GTEEngine.sv': engine_text, 'laxer_mister_adapter.sv': adapter_text}
    done = set()
    for s in adapted:
        path = Path(s['path'])
        if path.name in transforms and s['assignment'] == 'SYSTEMVERILOG_FILE' and (s.get('original') or path.name.endswith('adapter.sv')):
            path.write_text(transforms[path.name](path.read_text())); done.add(path.name)
    if done != set(transforms): raise SystemExit(f'Candidate GTE transforms missing: {set(transforms) - done}')


def build(directory, candidate=False):
    verilator = shutil.which('verilator') or str(ROOT / 'build/tools/oss-cad-suite/bin/verilator')
    adapted = adapter_sources(directory / 'adapted')
    if candidate:
        candidate_transform(adapted)
    originals = sorted(path for path in (UPSTREAM / 'rtl').iterdir() if path.suffix in ('.v', '.sv'))
    sources = [str(path) for path in originals] + [s['path'] for s in adapted
               if s['assignment'] == 'SYSTEMVERILOG_FILE' and (s.get('original') or s['path'].endswith('adapter.sv'))]
    obj = directory / 'obj_dir'
    with (directory / 'build.log').open('w') as log:
        subprocess.run([verilator, '--cc', '--exe', '--build', '-O3', '-Wno-fatal', '-j', '4', '--top-module', 'gte_adapter_top',
                        '-I' + str(UPSTREAM / 'rtl'), '--Mdir', str(obj), *sources,
                        str(ROOT / 'sim/reference/gte_adapter_top.sv'), str(ROOT / 'sim/reference/gte_reference_probe.cpp')],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=900, check=True)
    return obj / 'Vgte_adapter_top'


REGISTER_NAMES = (['VXY0', 'VZ0', 'VXY1', 'VZ1', 'VXY2', 'VZ2', 'RGBC', 'OTZ', 'IR0', 'IR1', 'IR2', 'IR3', 'SXY0',
                   'SXY1', 'SXY2', 'SXYP', 'SZ0', 'SZ1', 'SZ2', 'SZ3', 'RGB0', 'RGB1', 'RGB2', 'RES1', 'MAC0', 'MAC1',
                   'MAC2', 'MAC3', 'IRGB', 'ORGB', 'LZCS', 'LZCR'] +
                  ['RT11RT12', 'RT13RT21', 'RT22RT23', 'RT31RT32', 'RT33', 'TRX', 'TRY', 'TRZ', 'L11L12', 'L13L21',
                   'L22L23', 'L31L32', 'L33', 'RBK', 'GBK', 'BBK', 'LR1LR2', 'LR3LG1', 'LG2LG3', 'LB1LB2', 'LB3',
                   'RFC', 'GFC', 'BFC', 'OFX', 'OFY', 'H', 'DQA', 'DQB', 'ZSF3', 'ZSF4', 'FLAG'])


def compare(binary, directory, case_list, model_factory=GTE):
    lines = vectors(case_list)
    path = directory / 'vectors.txt'; path.write_text('\n'.join(lines) + '\n')
    result = subprocess.run([str(binary), str(path)], capture_output=True, text=True, timeout=3600)
    if result.returncode or 'DONE' not in result.stdout:
        sys.exit('GTE probe failed: ' + (result.stderr or result.stdout)[-500:])
    reads = [int(l.split()[2], 16) for l in result.stdout.splitlines() if l.startswith('R ')]
    mismatches = collections.defaultdict(list)
    for n, (profile, command, state) in enumerate(case_list):
        model = model_factory()
        for index in range(64):
            if index not in SKIP_WRITE:
                model.write(index, state[index])
        model.execute(command)
        for index in range(64):
            got, want = reads[n * 64 + index], model.read(index)
            if got != want:
                mismatches[(COMMANDS[command & 0x3F], profile, REGISTER_NAMES[index])].append((n, command, got, want))
    return mismatches


def main():
    candidate = '--candidate' in sys.argv
    directory = ROOT / ('build/sim/gte-reference-candidate' if candidate else 'build/sim/gte-reference')
    directory.mkdir(parents=True, exist_ok=True)
    binary = build(directory, candidate)
    count = int(os.environ.get('PSX_GTE_CASES', '3000'))
    case_list = cases(int(os.environ.get('PSX_GTE_SEED', '1')), count)
    mismatches = compare(binary, directory, case_list)
    total = len(case_list)
    if not mismatches:
        print(f'PASS GTE reference: {total} commands, {total * 64} register reads equal the psx-spx model')
    else:
        for (name, profile, register), items in sorted(mismatches.items()):
            n, command, got, want = items[0]
            print(f'DIFF {name:6s} {profile:9s} {register:8s} {len(items):5d} cases; first case {n} cmd {command:08x} '
                  f'got {got:08x} model {want:08x}')
    # A model with a planted fault (wrong UNR table entry) must disagree.
    class Faulty(GTE):
        def divide(self):
            return min(0x1FFFF, super().divide() + 1) if (self.r[58] & 0xFFFF) < (self.r[19] & 0xFFFF) * 2 else super().divide()
    planted = compare(binary, directory, [c for c in case_list if c[1] & 0x3F in (0x01, 0x30)][:200], Faulty)
    print('Planted divide fault ' + ('rejected' if planted else 'NOT detected'))
    if mismatches or not planted:
        sys.exit('GTE reference check failed')


if __name__ == '__main__':
    main()
