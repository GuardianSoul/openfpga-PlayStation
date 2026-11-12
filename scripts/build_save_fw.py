#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Build the save-state handler (rtl/pocket/save_fw) into rtl/pocket/psx_save_fw.hex.

psx_save_exp1's 4 KiB ROM, one byte per line (the stub, linked at 0xBF000000,
the EXP1 window the CPU trap vectors to, then stage 2, linked at 0x80200000
where the stub copies it), and its second ROM (EXP1 0x1C00-0x1FFF, the rest of
stage 2) in psx_save_fw2.hex next to it. The hex is committed, so building the
core needs no MIPS toolchain; run this after changing the handler (llvm-mc,
ld.lld, llvm-objcopy). --check rebuilds into a temporary file and fails if
the committed hex differs.
"""
import argparse
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from tool_paths import ROOT, find_tool

SRC = ROOT / 'rtl/pocket/save_fw/save_handler.S'
HEX = ROOT / 'rtl/pocket/psx_save_fw.hex'
SIZE = 0x2000           # the EXP1 window: ROM, staging page (gap), registers + second ROM
RAM_PAGES = 1024        # 2 MiB main RAM in 2 KiB pages
VRAM_ROWS = 512         # 1 MiB VRAM, one 1024-pixel row per 2 KiB page
SPU_PAGES = 256         # 512 KiB SPU RAM in 2 KiB pages


LINK = """PHDRS { s2 PT_LOAD; stub PT_LOAD; }
SECTIONS {
  .stage2 0x80200000 : { *(.stage2) } :s2
  .text 0xBF000000 : { *(.text) } :stub
  /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.comment) }
}
"""


def image(source=SRC, ram_pages=RAM_PAGES, vram_rows=VRAM_ROWS, spu_pages=SPU_PAGES, diag=False, noop=False, probe=False, probe_ff=False):
    """image_parts()'s EXP1 image."""
    return image_parts(source, ram_pages, vram_rows, spu_pages, diag, noop, probe, probe_ff)[0]


def image_parts(source=SRC, ram_pages=RAM_PAGES, vram_rows=VRAM_ROWS, spu_pages=SPU_PAGES, diag=False, noop=False, probe=False, probe_ff=False):
    """(image, stage 2 unpacked as it lands at STAGE2). The 8 KiB EXP1 image: the stub (.text, linked at 0xBF000000) from 0,
    stage 2 (.stage2, linked at 0x80200000, where the stub copies it) after
    the stub up to 0xFF4 and on at 0x1C00 (the second ROM), stage 2's first
    word its stamp (CRC-32 of the rest), and words 0xFF4/0xFF8/0xFFC set to
    spu_pages/vram_rows/ram_pages."""
    tools = [find_tool(t) for t in ('llvm-mc', 'ld.lld', 'llvm-objcopy', 'llvm-nm')]
    if not all(tools):
        raise SystemExit('llvm-mc, ld.lld, llvm-objcopy and llvm-nm are needed for the save handler')
    mc, ld, objcopy, nm = tools
    with tempfile.TemporaryDirectory() as tmp:
        obj, elf, script = Path(tmp) / 'h.o', Path(tmp) / 'h.elf', Path(tmp) / 'h.ld'
        script.write_text(LINK)
        subprocess.run([mc, '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', *(['--defsym=DIAG=1'] if diag else []),
                        *(['--defsym=NOOP=1'] if noop else []), *(['--defsym=PROBE=1'] if probe or probe_ff else []), *(['--defsym=PROBE_FF=1'] if probe_ff else []),
                        str(source), '-o', str(obj)], check=True)
        subprocess.run([ld, '-m', 'elf32ltsmip', '-T', str(script), '-e', 'entry', '--no-dynamic-linker',
                        '-o', str(elf), str(obj)], check=True)
        parts = {}
        for section in ('.text', '.stage2'):
            out = Path(tmp) / (section[1:] + '.bin')
            subprocess.run([objcopy, '-O', 'binary', '-j', section, str(elf), str(out)], check=True)
            parts[section] = bytearray(out.read_bytes())
        syms = dict((n, int(a, 16)) for a, _, n in (l.split() for l in subprocess.run(
            [nm, str(elf)], check=True, capture_output=True, text=True).stdout.splitlines() if len(l.split()) == 3))
    stub, stage2 = parts['.text'], parts['.stage2']
    if not stage2:                                # PROBE builds: the stub alone
        if len(stub) > 0xFF4:
            raise SystemExit('save handler: the probe runs past 0xFF4')
        blob = b''
    else:
        if syms.get('stage2_rom', 0) - 0xBF000000 != len(stub) or len(stub) % 4:
            raise SystemExit('save handler: stage2_rom must end the stub, word-aligned')
        stage2[0:4] = (zlib.crc32(bytes(stage2[4:])) or 1).to_bytes(4, 'little')
        packed = lz_pack(bytes(stage2))
        if lz_unpack(packed, len(stage2)) != bytes(stage2):
            raise SystemExit('save handler: LZSS round trip failed')
        blob = bytes(stage2[0:4]) + len(stage2).to_bytes(4, 'little') + packed
    first = 0xFF4 - len(stub)                     # blob bytes in the first ROM
    if len(blob) > first + 0x400:
        raise SystemExit(f'save handler: packed stage 2 is {len(blob)} bytes, the ROM holds {first + 0x400} after the stub')
    blob += bytes(first + 0x400 - len(blob))
    data = bytearray(SIZE)
    data[0:len(stub)] = stub
    data[len(stub):0xFF4] = blob[:first]
    data[0x1C00:0x2000] = blob[first:]
    data[0xFF4:0xFF8] = spu_pages.to_bytes(4, 'little')
    data[0xFF8:0xFFC] = vram_rows.to_bytes(4, 'little')
    data[0xFFC:0x1000] = ram_pages.to_bytes(4, 'little')
    return bytes(data), bytes(stage2)


def lz_pack(data):
    """LZSS for the stub's unpacker: per 8 items a flag byte (bit 0 first),
    1 = a literal byte, 0 = a match of 2 bytes: distance (1-4095) bits 7:0,
    then distance bits 11:8 << 4 | (length - 3), length 3-18. Greedy."""
    out, i, n = bytearray(), 0, len(data)
    while i < n:
        flags_at = len(out); out.append(0); flags = 0
        for bit in range(8):
            if i >= n:
                break
            best_len = best_dist = 0
            for j in range(max(0, i - 4095), i):
                k = 0
                while k < 18 and i + k < n and data[j + k] == data[i + k]:
                    k += 1
                if k > best_len:
                    best_len, best_dist = k, i - j
            if best_len >= 3:
                out += bytes((best_dist & 0xFF, (best_dist >> 8) << 4 | (best_len - 3)))
                i += best_len
            else:
                flags |= 1 << bit
                out.append(data[i]); i += 1
        out[flags_at] = flags
    return bytes(out)


def lz_unpack(packed, length):
    out, p = bytearray(), 0
    while len(out) < length:
        flags = packed[p] | 0x100; p += 1
        while flags != 1 and len(out) < length:
            if flags & 1:
                out.append(packed[p]); p += 1
            else:
                dist = packed[p] | (packed[p + 1] >> 4) << 8; count = (packed[p + 1] & 15) + 3; p += 2
                for _ in range(count):
                    out.append(out[-dist])
            flags >>= 1
    return bytes(out)


def free_bytes(data):
    """ROM bytes after stage 2's last non-zero byte (a rough room figure)."""
    tail = bytes(data[0x1C00:0x2000])
    return 0x400 - len(tail.rstrip(b'\0')) if tail.rstrip(b'\0') else 0x400 + 0xFF4 - len(bytes(data[:0xFF4]).rstrip(b'\0'))


def rom1(data):
    return data[:0x1000]


def rom2(data):
    """The second ROM's 1 KiB (EXP1 0x1C00-0x1FFF)."""
    return data[0x1C00:0x2000]


def write_pair(path, data):
    path.write_text(byte_hex(rom1(data)))
    path.with_name(path.stem + '2' + path.suffix).write_text(byte_hex(rom2(data)))


def rom40_hex(rom1_bytes, rom2_bytes):
    """psx_save_exp1's ROM_HEX: 1024 entries of 40 bits, entry i = {second
    ROM byte i, first ROM word i (bytes 4i..4i+3, little-endian)}."""
    assert len(rom1_bytes) == 4096 and len(rom2_bytes) == 1024
    return ''.join(f'{rom2_bytes[i]:02x}{int.from_bytes(rom1_bytes[4 * i:4 * i + 4], "little"):08x}\n' for i in range(1024))


def rom40_from_byte_hex(rom1_path, rom2_path):
    """rom40_hex from the two committed byte-per-line hex files."""
    read = lambda path: bytes(int(line, 16) for line in Path(path).read_text().split())
    return rom40_hex(read(rom1_path), read(rom2_path))


def byte_hex(data):
    return ''.join(f'{b:02x}\n' for b in data)


def word_hex(data):
    return ''.join(f'{int.from_bytes(data[i:i + 4], "little"):08x}\n' for i in range(0, len(data), 4))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='fail if the committed hex is stale')
    parser.add_argument('--diag', action='store_true', help='hardware diagnosis build: step blocks on screen, waits time out (writes psx_save_fw_diag.hex)')
    parser.add_argument('--noop', action='store_true', help='hardware diagnosis build: report a save done at once without copying anything, refuse loads (writes psx_save_fw_noop.hex)')
    parser.add_argument('--probe', action='store_true', help='hardware diagnosis build: any request fills VRAM red (save) or blue (load) and stops (writes psx_save_fw_probe.hex)')
    parser.add_argument('--probe-ff', action='store_true', help='the probe with EXP1 0x04-0xFF reading 0xFF as an empty port (the BIOS licence bytes; writes psx_save_fw_probeff.hex)')
    args = parser.parse_args()
    if args.probe_ff:
        out = HEX.with_name('psx_save_fw_probeff.hex'); write_pair(out, image(probe_ff=True))
        print(f'{out.relative_to(ROOT)} written'); return
    if args.probe:
        out = HEX.with_name('psx_save_fw_probe.hex'); write_pair(out, image(probe=True))
        print(f'{out.relative_to(ROOT)} written'); return
    if args.noop:
        out = HEX.with_name('psx_save_fw_noop.hex'); write_pair(out, image(noop=True))
        print(f'{out.relative_to(ROOT)} written'); return
    if args.diag:
        out = HEX.with_name('psx_save_fw_diag.hex'); write_pair(out, image(diag=True))
        print(f'{out.relative_to(ROOT)} written'); return
    data = image()
    hex2 = HEX.with_name(HEX.stem + '2' + HEX.suffix)
    if args.check:
        if (not HEX.exists() or HEX.read_text() != byte_hex(rom1(data)) or
                not hex2.exists() or hex2.read_text() != byte_hex(rom2(data))):
            raise SystemExit('rtl/pocket/psx_save_fw.hex / psx_save_fw2.hex are stale: run scripts/build_save_fw.py')
        print('save handler hex is current')
        return
    write_pair(HEX, data)
    print(f'{HEX.relative_to(ROOT)} and {hex2.relative_to(ROOT)} written')


if __name__ == '__main__':
    sys.exit(main())
