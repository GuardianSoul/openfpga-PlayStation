#!/usr/bin/env python3
"""Opt-in SOTN write FIFO / short CPU write-request adaptation on copies.

Keep the pinned MiSTer CPU, queue logic and memory mux. Import only the
falling-edge RAM and FIFO storage option from the pinned Pocket SOTN port.
This does not select its other CPU, GPU or title-specific changes.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

from prepare_resource_targets import UPSTREAM, digest
from tool_paths import ROOT

SOTN = ROOT / 'references/pocket_sotn'
SOTN_PIN = 'b9fedc5dac4207a9447a8b169349e606cec99dbe'


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Write FIFO source changed near: ' + old.strip()[:80])
    return text.replace(old, new)


def cpu_text(text):
    if re.search(r'^\s*mem_writeRequest\s*:', text, re.M | re.I):
        raise ValueError('CPU already has a short write-request port')
    text = replace(text, '      mem_request           : out std_logic;\n',
                   '      mem_request           : out std_logic;\n'
                   '      mem_writeRequest      : out std_logic; -- short store-only path\n')
    marker = "   mem_isData        <= mem4_request;"
    return replace(text, marker,
        "   -- pocket_sotn pk20: equals mem_request and not mem_rnw.\n"
        "   mem_writeRequest <= '1' when (mem4_request = '1' and mem4_rnw = '0'\n"
        "      and (memoryMuxBusy = '0' or mem_done = '1')) else '0';\n" + marker)


def mux_text(text):
    if re.search(r'^\s*mem_in_writeRequest\s*:', text, re.M | re.I):
        raise ValueError('Memory mux already has a short write-request port')
    text = replace(text, '      mem_in_request       : in  std_logic;\n',
        '      mem_in_request       : in  std_logic;\n'
        '      mem_in_writeRequest  : in  std_logic;\n')
    pattern = r'(   iwritefifo: entity mem.SyncFifoFallThroughMLAB\n   generic map\n   \(.*?)(\n   \)\n   port map)'
    matches = list(re.finditer(pattern, text, re.S))
    if len(matches) != 1 or 'DATAWIDTH         => 70' not in matches[0][1]:
        raise ValueError('Expected one original 8x70 write FIFO')
    m = matches[0]
    text = text[:m.start()] + m[1] + ',\n      M10K              => true' + m[2] + text[m.end():]
    return replace(text, "ce = '1' and mem_in_request = '1' and mem_in_rnw = '0' and (pauseNext",
                   "ce = '1' and mem_in_writeRequest = '1' and (pauseNext")


def top_text(text):
    if re.search(r'^\s*signal mem_writeRequest\b', text, re.M | re.I):
        raise ValueError('Top already wires the short write request')
    text = replace(text, '   signal mem_request            : std_logic;\n',
        '   signal mem_request            : std_logic;\n'
        '   signal mem_writeRequest       : std_logic;\n')
    text = replace(text, '      mem_in_request       => mem_request,  \n',
        '      mem_in_request       => mem_request,  \n'
        '      mem_in_writeRequest  => mem_writeRequest,\n')
    return replace(text, '      mem_request       => mem_request,  \n',
        '      mem_request       => mem_request,  \n'
        '      mem_writeRequest  => mem_writeRequest,\n')


def reference_sources(directory):
    pin = subprocess.check_output(['git', '-C', str(SOTN), 'rev-parse', 'HEAD'], text=True).strip()
    if pin != SOTN_PIN or subprocess.run(['git', '-C', str(SOTN), 'diff', '--quiet', 'HEAD']).returncode:
        raise ValueError('Pocket SOTN reference differs from the recorded pin')
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    sources = []
    for name in ('RamM10Kn.vhd', 'SyncFifoFallThroughMLAB.vhd'):
        original = SOTN/'rtl'/name; path = directory/name
        shutil.copyfile(original, path)
        sources.append(dict(path=str(path), sha256=digest(path), library='mem',
                            original=str(original), original_sha256=digest(original)))
    shutil.copyfile(SOTN/'LICENSE', directory/'COPYING.GPL2')
    return sources


def prepare_project(qsf, directory):
    qsf = Path(qsf).resolve(); directory = Path(directory).resolve()
    q = qsf.read_text(); selected = {}
    for name in ('cpu.vhd', 'memorymux.vhd', 'psx_top.vhd', 'SyncFifoFallThroughMLAB.vhd'):
        paths = re.findall(r'^set_global_assignment -name VHDL_FILE "([^"]*/'+re.escape(name)+r')"[^\n]*$', q, re.M)
        if len(paths) != 1:
            raise ValueError('Expected one assigned ' + name)
        selected[name] = Path(paths[0])
    if digest(selected['SyncFifoFallThroughMLAB.vhd']) != digest(UPSTREAM/'rtl/SyncFifoFallThroughMLAB.vhd'):
        raise ValueError('Refusing to overwrite a modified shared FIFO implementation')
    if re.search(r'^set_global_assignment -name VHDL_FILE "[^"]*/RamM10Kn\.vhd"', q, re.M):
        raise ValueError('Project already assigns RamM10Kn; resolve the shared RAM implementation first')
    # Validate every transformation before writing any project changes.
    transformed = {n: f(selected[n].read_text()) for n, f in
                   (('cpu.vhd', cpu_text), ('memorymux.vhd', mux_text), ('psx_top.vhd', top_text))}
    directory.mkdir(parents=True, exist_ok=False)
    sources = reference_sources(directory)
    for name, text in transformed.items():
        path = directory/name; path.write_text(text)
        sources.append(dict(path=str(path), sha256=digest(path), library='work',
                            original=str(selected[name]), original_sha256=digest(selected[name])))
    for s in sources:
        if Path(s['path']).name == 'RamM10Kn.vhd':
            continue
        q = replace(q, '"'+str(selected[Path(s['path']).name])+'"', '"'+s['path']+'"')
    q += f'\nset_global_assignment -name VHDL_FILE "{directory / "RamM10Kn.vhd"}" -library mem\n'
    qsf.write_text(q)
    report = dict(reference_pin=SOTN_PIN, preparer_sha256=digest(Path(__file__)), sources=sources,
                  qsf=str(qsf), qsf_sha256=digest(qsf), opt_in=True, guest_cycles_added=0,
                  required_contract='FIFO input/control stable at falling edge; consumer uses rising edge; related half-cycle paths remain timed',
                  fit_verified=False, timing_verified=False, hardware_verified=False, game_verified=False)
    (directory/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('qsf', type=Path); p.add_argument('output', type=Path)
    a = p.parse_args(); print(json.dumps(prepare_project(a.qsf, a.output), indent=2))
