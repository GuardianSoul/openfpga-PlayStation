#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""GPU VRAM request: the common idle term hoisted out of the request OR (clk2x timing).

cl-ssA4b-signoff-20261111-s6 missed clk2x setup on vram_pause / vram_WE /
vramState / ddr_busy -> gpu|vram_BURSTCNT[*] (-0.403 ns Slow 85C, -0.307 ns
Slow 0C, data delay 14.87 ns, the same for every bit: the late input is the
LAB-wide enable / sync load of the register, not its data).

Those four registers are the inputs of reqVRAMIdle. Today the idle term goes
into each of the seven requesters, each ANDs it with its own state, gpu.vhd
ORs the seven results (reqVRAMEnable) and ANDs the OR again with the same
idle terms in the IDLE branch of the VRAM state machine:

    VRAMIdle       = vramState = IDLE and (vram_WE = '0' or vram_BUSY = '0')
    videoout req   = Vraw and VRAMIdle and not vram_pause         (gpu_videoout)
    reqVRAMIdle    = VRAMIdle and not videoout req and not vram_pause
    module req i   = raw_i and reqVRAMIdle                        (six modules)
    reqVRAMEnable  = videoout req or (OR of module req i)
    IDLE branch    : (ce or (videoout req and not savestate_busy))
                     and (vram_WE = '0' or vram_BUSY = '0') and vram_pause = '0'
                     then if reqVRAMEnable ...

Under I = (vramState = IDLE and (vram_WE = '0' or vram_BUSY = '0') and
vram_pause = '0'): videoout req = Vraw, reqVRAMIdle = not Vraw, and
reqVRAMEnable = Vraw or (not Vraw and OR raw_i) = Vraw or OR raw_i. I is part
of the IDLE-branch condition and of fifoOut_Rd, so inside both the gated OR
can be replaced by the raw OR (reqVRAMEnableRaw) and the gated videoout
request by Vraw without changing either condition for any input value. This
is a Boolean identity of the two conditions, not a reachability argument.

reqVRAMXPos / YPos / Size are read only inside that branch (after the
condition). There the old value is (videoout data and Vraw) or OR (data_i and
state_i and not Vraw). The step drops the idle term from the modules' three
data lines and selects videoout's data with Vraw in gpu.vhd: the same value
wherever it is read.

Changes: one new output requestVRAMRaw per requester (gpu_vram2vram,
gpu_vram2cpu, gpu_line, gpu_rect, gpu_poly, gpu_pixelpipeline, gpu_videoout),
the idle term removed from their XPos/YPos/Size lines, and in gpu.vhd the
raw OR in the IDLE branch and in fifoOut_Rd. No register is added or removed
and no register's next-state function changes, so every gpu port is the same
in every clk2x cycle.

Check: scripts/run_gpu_req_hoist.py (whole-GPU lockstep of the reviewed
sources against this step's output, every output port every clk2x cycle,
planted faults --fault NAME).

Usage: SOURCE --output DIR [--seed N] [--baseline]
  Relocates the frozen project SOURCE (fit-inputs.json) into the fresh DIR
  and patches the eight files there. SOURCE is never written. --baseline
  writes the same snapshot without the patch.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'gpu-req-hoist'
# The reviewed inputs: build/ss/d2 of the M project (2026-11-11).
SOURCE_SHA = {
    'gpu.vhd': 'dc24042ef6d7a329bef3eb6b50b5e948724dc74fa4a4a17fbae2bfbc61ac453f',
    'gpu_vram2vram.vhd': '8556c4bb56174e60642aebb89caba20085dd35c09bfc6122e683ba5947d71bba',
    'gpu_vram2cpu.vhd': 'e4a03f0d5455e0de7416a82198c72b48748f623ff636840be333ae2933a8c18c',
    'gpu_line.vhd': '154b7b7d46184f69775c9f614c6b8d867e125dc4bea5ff568e14e53090ead946',
    'gpu_rect.vhd': '66f7b54a983c69c5878137668a0bc8509302eb8fca70a61da24b10c850037ad6',
    'gpu_poly.vhd': '91a8f86f9e5140a406f297a67432554c83656b3cc180cc9030dbe612c7d46647',
    'gpu_pixelpipeline.vhd': 'b2edf175031553ea433e3f796f1b75c676b2d13d85392e66c526dbc8517310be',
    'gpu_videoout.vhd': '5eb2088ff58617a53224a58b094249b6cacaeb03192cc8a50a6d3b08260bd1b5',
}
# Also accepted (candidate E, build/ss/e of the timing-paths worktree): the same file after
# another reviewed step whose edits are textually disjoint from this step's anchors and edits, so
# hoist(E file) = other-step(hoist(D2 file)). scripts/run_gpu_req_hoist.py --project runs on either.
ALSO_SHA = {
    # gpu-rect-yend-parallel (prepare_gpu_rect_yend.py): declarations after line 108, a process after
    # line 128, the PROCPIXELS test at line 337. This step: port line 56, request lines 122-125.
    'gpu_rect.vhd': ('3f21805f5a8d16e7f80eff3b964aa5af9506032f865444895800a226db689dd2',),
    # prepare_main_hold_cells.py --tex: signals after line 292, hold cells after line 376, stage4
    # texture registers at lines 886-894. This step: port line 53, request lines 380-383.
    'gpu_pixelpipeline.vhd': ('2c0afc9bfca9c0d2d7ab0cc07d485bbfd660f49fd2b590a25163549e1312aa73',),
}


def reviewed(name, digest):
    return digest == SOURCE_SHA[name] or digest in ALSO_SHA.get(name, ())


MODULES = ('vram2vram', 'vram2cpu', 'line', 'rect', 'poly', 'pipeline')   # gpu.vhd signal prefixes
IDLE_TERMS = (" and requestVRAMIdle = '1'", "requestVRAMIdle = '1' and ")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected exactly one match of: {old.strip()[:80]}')
    return text.replace(old, new)


def _without_idle(line):
    """One request line with its idle term removed (exactly one term)."""
    if line.count('requestVRAMIdle') != 1:
        raise ValueError(f'{TAG}: idle term not found once in: {line.strip()[:80]}')
    for term in IDLE_TERMS:
        if term in line:
            return line.replace(term, '')
    raise ValueError(f'{TAG}: idle term changed in: {line.strip()[:80]}')


def module_text(name, text):
    """A requester with requestVRAMRaw and ungated XPos/YPos/Size."""
    lines = text.split('\n')
    ports = [i for i, l in enumerate(lines) if re.match(r'\s+requestVRAMIdle\s+: in  std_logic;\s*$', l)]
    if len(ports) != 1:
        raise ValueError(f'{TAG}: {name}: requestVRAMIdle port not found once')
    request = {key: [i for i, l in enumerate(lines) if re.match(rf'   requestVRAM{key}\s*<= ', l)]
               for key in ('Enable', 'XPos', 'YPos', 'Size')}
    if any(len(v) != 1 for v in request.values()):
        raise ValueError(f'{TAG}: {name}: request lines changed')
    e, x, y, s = (request[k][0] for k in ('Enable', 'XPos', 'YPos', 'Size'))
    if (x, y, s) != (e + 1, e + 2, e + 3):
        raise ValueError(f'{TAG}: {name}: request lines are not one block')
    # Every other use of the idle input is in the module's own clocked state machine.
    if len(re.findall(r'\brequestVRAMRaw\b', text)):
        raise ValueError(f'{TAG}: already applied')
    raw = _without_idle(lines[e])
    if not raw.startswith('   requestVRAMEnable <= '):
        raise ValueError(f'{TAG}: {name}: enable line changed')
    raw = '   requestVRAMRaw    <= ' + raw[len('   requestVRAMEnable <= '):] + f' -- {TAG}: the request without the idle input'
    for i in (x, y, s):
        lines[i] = _without_idle(lines[i]) + f' -- {TAG}: idle applied in gpu.vhd'
    lines.insert(s + 1, raw)
    port = lines[ports[0]]
    indent = re.match(r'\s+', port)[0]
    width = port.index(':') - len(indent)
    lines.insert(ports[0], indent + 'requestVRAMRaw'.ljust(width) + f': out std_logic; -- {TAG}')
    return '\n'.join(lines)


OR_OLD = {
    'X': '   reqVRAMXPos   <= vram2vram_reqVRAMXPos   or vram2cpu_reqVRAMXPos   or line_reqVRAMXPos   or rect_reqVRAMXPos   or poly_reqVRAMXPos   or pipeline_reqVRAMXPos   or videoout_reqVRAMXPos  ;  \n',
    'Y': '   reqVRAMYPos   <= vram2vram_reqVRAMYPos   or vram2cpu_reqVRAMYPos   or line_reqVRAMYPos   or rect_reqVRAMYPos   or poly_reqVRAMYPos   or pipeline_reqVRAMYPos   or videoout_reqVRAMYPos  ;  \n',
    'S': '   reqVRAMSize   <= vram2vram_reqVRAMSize   or vram2cpu_reqVRAMSize   or line_reqVRAMSize   or rect_reqVRAMSize   or poly_reqVRAMSize   or pipeline_reqVRAMSize   or videoout_reqVRAMSize  ;  \n',
}
ENABLE_OLD = ('   reqVRAMEnable <= vram2vram_reqVRAMEnable or vram2cpu_reqVRAMEnable or line_reqVRAMEnable or rect_reqVRAMEnable '
              'or poly_reqVRAMEnable or pipeline_reqVRAMEnable or videoout_reqVRAMEnable;\n')
ENABLE_RAW = (f'   -- {TAG}: the same OR without the idle terms; equal to reqVRAMEnable wherever\n'
              "   -- vramState = IDLE, (vram_WE = '0' or vram_BUSY = '0') and vram_pause = '0' hold\n"
              '   reqVRAMEnableRaw <= vram2vram_reqVRAMRaw or vram2cpu_reqVRAMRaw or line_reqVRAMRaw or rect_reqVRAMRaw '
              'or poly_reqVRAMRaw or pipeline_reqVRAMRaw or videoout_reqVRAMRaw;\n')
FIFO_OLD = ("   fifoOut_Rd <= '1' when (ce = '1' and vramState = IDLE and (vram_WE = '0' or vram_BUSY = '0') and fifoOut_Empty = '0' "
            "and reqVRAMEnable = '0' and vram_pause = '0' and (fifoOut_Dout(86) = '0' or maskReady = '1')) else '0';\n")
FIFO_NEW = FIFO_OLD.replace("reqVRAMEnable = '0'", "reqVRAMEnableRaw = '0'")
BRANCH_OLD = ("               when IDLE =>\n"
              "                  if ((ce = '1' or (videoout_reqVRAMEnable = '1' and savestate_busy = '0')) and (vram_WE = '0' or vram_BUSY = '0') and vram_pause = '0') then\n"
              "                     if (reqVRAMEnable = '1') then\n")
BRANCH_HOLD = "(vram_WE = '0' or vram_BUSY = '0') and vram_pause = '0') then\n"
BRANCH_NEW = ("               when IDLE =>\n"
              f"                  -- {TAG}: raw requests; the idle terms are this condition's own\n"
              "                  if ((ce = '1' or (videoout_reqVRAMRaw = '1' and savestate_busy = '0')) and " + BRANCH_HOLD +
              "                     if (reqVRAMEnableRaw = '1') then\n")


def _data(key):
    """videoout's data when it requests, else the OR of the six modules' data."""
    name = {'X': 'XPos', 'Y': 'YPos', 'S': 'Size'}[key]
    return (f"   reqVRAM{name}   <= videoout_reqVRAM{name} when (videoout_reqVRAMRaw = '1') else -- {TAG}\n"
            '                    (' + ' or '.join(f'{m}_reqVRAM{name}' for m in MODULES) + ');\n')


def gpu_text(text):
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    # The inventory the argument rests on: where the gated OR and the request data are read.
    counts = {r'\breqVRAMEnable\b': 4, r'\breqVRAMXPos\b': 7, r'\breqVRAMYPos\b': 3, r'\breqVRAMSize\b': 3,
              r'\breqVRAMIdle\b': 8, r'\bvideoout_reqVRAMEnable\b': 7}
    for pattern, count in counts.items():
        if len(re.findall(pattern, text)) != count:
            raise ValueError(f'{TAG}: gpu.vhd use count of {pattern} is {len(re.findall(pattern, text))}, reviewed {count}')
    start = text.index(BRANCH_OLD) if text.count(BRANCH_OLD) == 1 else -1
    end = text.find('                     elsif (fifoOut_Empty = \'0\' and fifoOut_Dout(86) = \'1\' and maskReady = \'0\') then')
    if start < 0 or end < start:
        raise ValueError(f'{TAG}: IDLE branch changed')
    # All reads of the request position and size sit between the two markers.
    outside = text[:start] + text[end:]
    for pattern, count in ((r'\breqVRAMXPos\b', 2), (r'\breqVRAMYPos\b', 2), (r'\breqVRAMSize\b', 2)):
        if len(re.findall(pattern, outside)) != count:      # declaration + the OR line
            raise ValueError(f'{TAG}: {pattern} is read outside the IDLE request branch')
    for prefix in (*MODULES, 'videoout'):
        text, n = re.subn(rf'(?m)^(   signal {prefix}_reqVRAMEnable\s*: std_logic;)\n',
                          lambda m: m[1] + '\n' + f'   signal {prefix}_reqVRAMRaw'.ljust(36) + f': std_logic; -- {TAG}\n', text)
        if n != 1:
            raise ValueError(f'{TAG}: signal {prefix}_reqVRAMEnable not found once')
        text, n = re.subn(rf'(?m)^(      requestVRAMEnable(\s*)=> {prefix}_reqVRAMEnable,)\n',
                          lambda m: m[1] + '\n      requestVRAMRaw   ' + m[2] + f'=> {prefix}_reqVRAMRaw, -- {TAG}\n', text)
        if n != 1:
            raise ValueError(f'{TAG}: port map of {prefix}_reqVRAMEnable not found once')
    text = _once(text, '   signal reqVRAMEnable             : std_logic;\n',
                 '   signal reqVRAMEnable             : std_logic;\n'
                 f'   signal reqVRAMEnableRaw          : std_logic; -- {TAG}\n')
    text = _once(text, ENABLE_OLD, ENABLE_OLD + ENABLE_RAW)
    for key, old in OR_OLD.items():
        text = _once(text, old, _data(key))
    text = _once(text, FIFO_OLD, FIFO_NEW)
    text = _once(text, BRANCH_OLD, BRANCH_NEW)
    return text


def transform(name, text):
    """One reviewed source with the step applied. Refuses any other input."""
    if name not in SOURCE_SHA:
        raise ValueError(f'{TAG}: {name} is not part of the step')
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    if not reviewed(name, sha(text)):
        raise ValueError(f'{TAG}: {name} is not a reviewed input (sha256 {sha(text)[:16]}, want {SOURCE_SHA[name][:16]}'
                         + ''.join(' or ' + s[:16] for s in ALSO_SHA.get(name, ())) + ')')
    return gpu_text(text) if name == 'gpu.vhd' else module_text(name, text)


# Planted faults for the lockstep bench: file -> (old, new) on the transformed text.
FAULTS = {
    # the hold-during-write term dropped from the IDLE branch: a request is taken while a write is held
    'hold_dropped': [('gpu.vhd', "(videoout_reqVRAMRaw = '1' and savestate_busy = '0')) and " + BRANCH_HOLD,
                      "(videoout_reqVRAMRaw = '1' and savestate_busy = '0')) and vram_pause = '0') then\n")],
    # the pause term dropped from the IDLE branch: a request is taken while the port is paused
    'pause_dropped': [('gpu.vhd', "(videoout_reqVRAMRaw = '1' and savestate_busy = '0')) and " + BRANCH_HOLD,
                       "(videoout_reqVRAMRaw = '1' and savestate_busy = '0')) and (vram_WE = '0' or vram_BUSY = '0')) then\n")],
    # videoout's priority lost on the data: its position is ORed with a waiting module's
    'videoout_or': [('gpu.vhd', _data('X'),
                     '   reqVRAMXPos   <= videoout_reqVRAMXPos or ' + ' or '.join(f'{m}_reqVRAMXPos' for m in MODULES) + ';\n')],
    # the polygon's raw request ignores pipeline_stall
    'poly_stall_ignored': [('gpu_poly.vhd',
                            "   requestVRAMRaw    <= '1'                        when (state = REQUESTLINE and pipeline_stall = '0') else '0';",
                            "   requestVRAMRaw    <= '1'                        when (state = REQUESTLINE) else '0';")],
    # vram2cpu's raw request ignores its FIFO level
    'cpu_fifo_ignored': [('gpu_vram2cpu.vhd',
                          "   requestVRAMRaw    <= '1'      when (state = READVRAM and Fifo_NearFull = '0') else '0';",
                          "   requestVRAMRaw    <= '1'      when (state = READVRAM) else '0';")],
    # the FIFO read looks at the modules only: a write is popped in the clock videoout is granted
    'fifo_rd_videoout': [('gpu.vhd', 'or pipeline_reqVRAMRaw or videoout_reqVRAMRaw;\n', 'or pipeline_reqVRAMRaw;\n'),
                         ('gpu.vhd', "                     if (reqVRAMEnableRaw = '1') then\n",
                          "                     if ((reqVRAMEnableRaw or videoout_reqVRAMRaw) = '1') then\n")],
}


def fault_texts(texts, fault):
    texts = dict(texts)
    for name, old, new in FAULTS[fault]:
        texts[name] = _once(texts[name], old, new)
    return texts


def prepare(source, output, seed=None, baseline=False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    for name, want in SOURCE_SHA.items():
        rows = [r for r in manifest['quartus_sources'] if Path(r['path']).name == name]
        if len(rows) != 1 or not reviewed(name, rows[0]['sha256']):
            raise SystemExit(f'{TAG}: {source} does not compile the reviewed {name}')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    paths = {name: next(Path(r['path']) for r in before if Path(r['path']).name == name) for name in SOURCE_SHA}
    if any(not reviewed(n, sha(p.read_text())) for n, p in paths.items()):
        raise SystemExit(f'{TAG}: snapshot differs from the reviewed inputs')
    if baseline:
        return paths, seed
    for name, path in paths.items():
        new = transform(name, path.read_text())
        try:
            transform(name, new)
        except ValueError as error:
            if 'already applied' not in str(error):
                raise
        else:
            raise SystemExit(f'{TAG}: idempotence guard failed')
        path.write_text(new)
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != {str(p) for p in paths.values()}:
        raise SystemExit(f'{TAG}: expected only the eight GPU request files to change')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    return paths, seed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--baseline', action='store_true',
                        help='snapshot only, sources unchanged (same-layout baseline for the block fit)')
    args = parser.parse_args()
    try:
        paths, seed = prepare(args.source, args.output, args.seed, args.baseline)
    except ValueError as error:
        raise SystemExit(str(error))
    print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
    for name, path in paths.items():
        print(f'{TAG}: {"unchanged" if args.baseline else "patched"} {path} (sha256 {sha(path.read_text())})')
    if not args.baseline:
        print(f'{TAG}: 0 new register bits; 7 new requestVRAMRaw ports; fit-inputs.json rewritten')
