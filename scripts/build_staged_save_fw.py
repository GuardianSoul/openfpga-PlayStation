#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Build the own staged PXS4 candidate into a fresh ignored output directory.

Uses the project's staged loader/LZSS implementation from the ss059 work,
with full SPU scanning and kernel/debug context. This requires the paired
CPU and 0x1820..0x1822 scan extension from prepare_save_full_context.py.
The existing production/diagnostic ROMs are not overwritten.
"""
import argparse
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from tool_paths import ROOT, find_tool

SRC = ROOT / 'rtl/pocket/save_fw/staged_handler.S'
HEX = ROOT / 'build/save-staged/psx_save_fw.hex'
SIZE = 0x2000           # the EXP1 window: ROM, staging page (gap), registers + second ROM
RAM_PAGES = 1024        # 2 MiB main RAM in 2 KiB pages
VRAM_ROWS = 512         # 1 MiB VRAM, one 1024-pixel row per 2 KiB page
SPU_PAGES = 256         # 512 KiB SPU RAM in 2 KiB pages
EXT_SIZE = 0x400        # save-rom-ext: the extension ROM (one M10K, 1024 x 8), shown at 0x1C00 while SAVE_MODE bit 1 is set
EXT_SYMBOL = 'rom_ext'  # a stub with this label reads the extension (prepare_save_rom_ext.firmware_text)
LZ2_SYMBOL = 'lz2'      # a stub with this label unpacks lz2_pack's stream (prepare_save_rom_room.firmware_text)


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
    """image_parts_ext() without the extension: (image, stage 2). Stops when the routine uses the extension ROM."""
    data, stage2, ext = image_parts_ext(source, ram_pages, vram_rows, spu_pages, diag, noop, probe, probe_ff)
    if any(ext):
        raise SystemExit('save handler: the packed stage 2 reaches into the extension ROM: use image_parts_ext')
    return data, stage2


def image_parts_ext(source=SRC, ram_pages=RAM_PAGES, vram_rows=VRAM_ROWS, spu_pages=SPU_PAGES, diag=False, noop=False, probe=False, probe_ff=False):
    """(image, stage 2 unpacked as it lands at STAGE2, extension ROM bytes). The 8 KiB EXP1 image: the stub (.text, linked at 0xBF000000) from 0,
    stage 2 (.stage2, linked at 0x80200000, where the stub copies it) after
    the stub up to 0xFF4 and on at 0x1C00 (the second ROM), stage 2's first
    word its stamp (CRC-32 of the rest), and words 0xFF4/0xFF8/0xFFC set to
    spu_pages/vram_rows/ram_pages.
    save-rom-ext: when the stub has the label rom_ext (it copies the second ROM's
    window a second time with SAVE_MODE bit 1 set), the packed stream goes on in
    the extension ROM's EXT_SIZE bytes; without the label, or when the stream ends
    before it, the extension is all zero and the image is what it was."""
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
        pack, unpack = (lz2_pack, lz2_unpack) if LZ2_SYMBOL in syms else (lz_pack, lz_unpack)
        if pack is lz2_pack and len(stage2) % 4:
            raise SystemExit('save handler: lz2 needs stage 2 in whole words')
        packed = pack(bytes(stage2))
        if unpack(packed, len(stage2)) != bytes(stage2):
            raise SystemExit('save handler: LZSS round trip failed')
        blob = bytes(stage2[0:4]) + len(stage2).to_bytes(4, 'little') + packed
    first = 0xFF4 - len(stub)                     # blob bytes in the first ROM
    room = first + 0x400 + (EXT_SIZE if EXT_SYMBOL in syms else 0)
    if len(blob) > room:
        raise SystemExit(f'save handler: packed stage 2 is {len(blob)} bytes, the ROM holds {room} after the stub')
    blob += bytes(first + 0x400 + EXT_SIZE - len(blob))
    data = bytearray(SIZE)
    data[0:len(stub)] = stub
    data[len(stub):0xFF4] = blob[:first]
    data[0x1C00:0x2000] = blob[first:first + 0x400]
    data[0xFF4:0xFF8] = spu_pages.to_bytes(4, 'little')
    data[0xFF8:0xFFC] = vram_rows.to_bytes(4, 'little')
    data[0xFFC:0x1000] = ram_pages.to_bytes(4, 'little')
    return bytes(data), bytes(stage2), bytes(blob[first + 0x400:])


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


def lz2_pack(data):
    """rom-room (docs/save-states-finish/rom-room.md): the stream of the stub with the label lz2.
    lz_pack's layout with two changes: a match is 2-17 bytes (length - 2 in the low four bits), and
    the stream is of data with the two halfwords of every word swapped (stream byte p lands at
    p ^ 2; a match reads its source at q ^ 2 too), so the high halfword of an instruction (opcode,
    registers) sits next to the previous instruction's. Optimal parse: the fewest bits (9 a
    literal, 17 a match), which is the fewest bytes up to the last flag byte."""
    return _pack_optimal(halfword_swap(data), 2, 17)


def lz2_unpack(packed, length):
    """lz2_pack's inverse, as the stub does it (the stream's byte p is stored at p ^ 2)."""
    out, p = bytearray(length), 0
    q = 0
    while q < length:
        flags = packed[p] | 0x100; p += 1
        while flags != 1 and q < length:
            if flags & 1:
                out[q ^ 2] = packed[p]; p += 1; q += 1
            else:
                dist = packed[p] | (packed[p + 1] >> 4) << 8; count = (packed[p + 1] & 15) + 2; p += 2
                for _ in range(count):
                    out[q ^ 2] = out[(q - dist) ^ 2]; q += 1
            flags >>= 1
    return bytes(out)


def halfword_swap(data):
    return bytes(data[i ^ 2] for i in range(len(data)))


def _pack_optimal(data, min_len, max_len):
    """lz_pack's token layout (distance 1-4095), matches of min_len..max_len, fewest bits."""
    n = len(data)
    longest, at = [(0, 0)] * n, {}
    for i in range(n):
        best_len = best_dist = 0
        key = data[i:i + min_len]
        if len(key) == min_len:
            for j in reversed(at.get(key, ())):
                if i - j > 4095:
                    break
                k = min_len
                while k < max_len and i + k < n and data[j + k] == data[i + k]:
                    k += 1
                if k > best_len:
                    best_len, best_dist = k, i - j
                    if k == max_len:
                        break
            at.setdefault(key, []).append(i)
        longest[i] = (best_len, best_dist)
    cost, take = [0] * (n + 1), [1] * (n + 1)
    for i in range(n - 1, -1, -1):
        cost[i] = 9 + cost[i + 1]
        for length in range(min_len, longest[i][0] + 1):
            if 17 + cost[i + length] < cost[i]:
                cost[i], take[i] = 17 + cost[i + length], length
    out, i, items = bytearray(), 0, []
    while i < n:
        items.append((data[i], 1) if take[i] == 1 else (longest[i][1], take[i]))
        i += take[i]
    for g in range(0, len(items), 8):
        flags_at, flags = len(out), 0
        out.append(0)
        for bit, (value, length) in enumerate(items[g:g + 8]):
            if length == 1:
                flags |= 1 << bit
                out.append(value)
            else:
                out += bytes((value & 0xFF, (value >> 8) << 4 | (length - min_len)))
        out[flags_at] = flags
    return bytes(out)


def free_bytes(data, ext=None):
    """ROM bytes after stage 2's last non-zero byte (a rough room figure). ext: the extension ROM's
    bytes of a routine that reads it (its room counts too)."""
    if ext is not None:
        return len(ext) - len(bytes(ext).rstrip(b'\0')) if any(ext) else len(ext) + free_bytes(data)
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


def ext_hex(ext=bytes(EXT_SIZE)):
    """psx_save_rom_ext's HEX: EXT_SIZE bytes, one per line."""
    assert len(ext) == EXT_SIZE
    return byte_hex(ext)


def byte_hex(data):
    return ''.join(f'{b:02x}\n' for b in data)


def word_hex(data):
    return ''.join(f'{int.from_bytes(data[i:i + 4], "little"):08x}\n' for i in range(0, len(data), 4))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', type=Path, default=HEX.parent)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    data, stage2, ext = image_parts_ext()
    first, second = rom1(data), rom2(data)
    write_pair(args.output / 'psx_save_fw.hex', data)
    (args.output / 'psx_save_rom40.hex').write_text(rom40_hex(first, second))
    (args.output / 'psx_save_rom_ext.hex').write_text(ext_hex(ext))
    (args.output / 'stage2.bin').write_bytes(stage2)
    print('Built staged save candidate:', len(stage2), 'unpacked stage-2 bytes')


if __name__ == '__main__':
    main()
