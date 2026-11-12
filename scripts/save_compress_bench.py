#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""CPU bench support for the save routine step "compress" (scripts/prepare_save_compress.py).

Used by run_save_project_cpu.py --routine-step compress (after the big build's steps):
  firmware_text / FAULTS / fault_text   the step and its planted faults on the routine text
  bench_text    sim/reference/tb_staged_save_cpu.vhd (after the other steps' transforms, rom-ext among
                them: the routine needs the extension ROM) with
                  the SDRAM above main RAM widened from 0x200000-0x20FFFF to 0x200000-0x23FFFF:
                  stage 2, the hash table (0x210000) and the shadow of the fixture's stored
                  pages (0x220000 on); an access above 0x23FFFF in save mode still stops the run
  wrap_simulate every run with a save dumps the buffer (DUMP_BUF) and the state is checked:
                magic "pXSy", pages written = 2 + ceil(word 39 / 2048), the stream decodes to
                word 38 - 2 pages, the reference compressor makes the same stream of them, and
                the last page is filled with zeros. A failed check fails the run. The run's
                cycle bound is raised to COMPRESS_MAX_CYCLES (default 8,000,000).
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_save_compress as step

FAULTS = step.FAULTS
firmware_text = step.firmware_text
fault_text = step.fault_text
PAGE = 2048


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'compress bench anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def bench_text(text):
    text = once(text, '            assert a(22 downto 16) = "0100000" report "save mode access outside stage 2\'s SDRAM" severity failure;\n',
                '            assert a(22 downto 18) = "01000" report "save mode access outside stage 2\'s SDRAM, the hash table and the shadow" severity failure;   -- compress\n')
    text = once(text, '            return high(to_integer(unsigned(a(15 downto 2))));\n',
                '            return high(to_integer(unsigned(a(17 downto 2))));\n')
    text = once(text, '                     index := to_integer(unsigned(adr(15 downto 2)));\n',
                '                     index := to_integer(unsigned(adr(17 downto 2)));\n')
    text = once(text, "      variable high : words(0 to 16383) := (others => (others => '0'));\n",
                "      variable high : words(0 to 65535) := (others => (others => '0'));   -- compress: 0x200000-0x23FFFF\n")
    return text


def state_problems(savebuf):
    """savebuf.hex of a save: what is wrong with the compressed state (empty: nothing), and its figures."""
    lines = savebuf.read_text().split()
    pages = int(lines[1], 16)
    data = b''.join(int(w, 16).to_bytes(4, 'little') for w in lines[2:2 + pages * 512])
    word = lambda n: int.from_bytes(data[4 * n:4 * n + 4], 'little')
    figures = dict(pages=pages, stored_pages=word(38), stream_bytes=word(39), magic=data[:4].decode('latin-1'))
    found = []
    if (word(0) & ~7) != 0x79535870:
        found.append(f'magic {word(0):08X} is not "pXSy" + disc')
    if pages != 2 + (word(39) + PAGE - 1) // PAGE:
        found.append(f'{pages} pages written, word 39 = {word(39)} bytes needs {2 + (word(39) + PAGE - 1) // PAGE}')
    if found:
        return found, figures
    stream = data[2 * PAGE:2 * PAGE + word(39)]
    try:
        stored = step.decompress(stream, (word(38) - 2) * PAGE)
    except step.BadStream as error:
        return [f'the stream does not decode: {error}'], figures
    if step.compress(stored) != stream:
        found.append('the stream is not the reference compressor\'s stream of the same pages')
    if any(data[2 * PAGE + word(39):]):
        found.append('the last page is not filled with zeros')
    return found, figures


def wrap_simulate(simulate):
    def wrapped(directory, generics, tag):
        generics = dict(generics)
        saving = 'SAVE_AT' in generics
        if saving:
            generics['DUMP_BUF'] = 'true'
        if 'MAX_CYCLES' in generics and (saving or 'LOAD_AT' in generics):
            # a save with the step is several times longer than the runner's 3,000,000-cycle bound allows for
            generics['MAX_CYCLES'] = max(int(generics['MAX_CYCLES']), int(os.environ.get('COMPRESS_MAX_CYCLES', '8000000')))
        savebuf = directory / 'savebuf.hex'
        planted = str(generics.get('PLANT_BUF', '')).lower() == 'true'
        if saving and not planted and savebuf.exists():
            savebuf.unlink()
        ok, values, image, why = simulate(directory, generics, tag)
        if saving and savebuf.exists() and values.get('saves') == '1':
            found, figures = state_problems(savebuf)
            print(f"  compress: state of '{tag}': {figures}" + ('' if not found else ' PROBLEM: ' + '; '.join(found)), flush=True)
            if found:
                return False, values, image, 'compress state: ' + found[0]
        return ok, values, image, why
    return wrapped
