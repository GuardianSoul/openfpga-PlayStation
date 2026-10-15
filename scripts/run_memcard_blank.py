#!/usr/bin/env python3
"""Check psx_memcard_blank against the psx-spx blank-card format.

The reference below is written from the psx-spx "Memory Card Data Format"
description, independently of the RTL: header frame 0 and write-test frame
63 ("MC"), fifteen free directory frames, twenty empty broken-sector frames,
zeroed frames 36-62, 0xFF data blocks, and XOR checksums in byte 127 of
every header-area frame (0-63 except the replacement/unused area, which is
zero). All 131,072 bytes must match.
"""
import subprocess

from tool_paths import ROOT, find_tool


def reference():
    card = bytearray(b'\xff' * 131072)
    def frame(n, content):
        data = bytearray(128); data[:len(content)] = content
        if n < 36 or n == 63: data[127] = 0
        if n < 36 or n == 63:
            x = 0
            for b in data[:127]: x ^= b
            data[127] = x
        card[n * 128:(n + 1) * 128] = data
    frame(0, b'MC')
    for n in range(1, 16): frame(n, bytes([0xA0, 0, 0, 0, 0, 0, 0, 0, 0xFF, 0xFF]))
    for n in range(16, 36): frame(n, bytes([0xFF, 0xFF, 0xFF, 0xFF, 0, 0, 0, 0, 0xFF, 0xFF]))
    for n in range(36, 63): frame(n, b'')
    frame(63, b'MC')
    return card


def main():
    iverilog = find_tool('iverilog'); vvp = str(__import__('pathlib').Path(iverilog).with_name('vvp'))
    directory = ROOT / 'build/sim/memcard-blank'; directory.mkdir(parents=True, exist_ok=True)
    subprocess.run([iverilog, '-g2012', '-o', str(directory / 'blank.vvp'), str(ROOT / 'rtl/pocket/psx_memcard_blank.sv'),
                    str(ROOT / 'sim/reference/tb_memcard_blank.sv')], check=True)
    subprocess.run([vvp, 'blank.vvp'], cwd=directory, check=True, capture_output=True)
    words = [int(l, 16) for l in (directory / 'blank_words.txt').read_text().split()]
    rtl = b''.join(w.to_bytes(8, 'little') for w in words)
    ref = reference()
    bad = [i for i in range(131072) if rtl[i] != ref[i]]
    if len(words) != 16384 or bad:
        raise SystemExit(f'Blank card mismatch at bytes {bad[:8]} (frame {bad[0] // 128 if bad else -1})')
    assert ref[0:2] == b'MC' and ref[127] == 0x0E and ref[128] == 0xA0 and ref[255] == 0xA0
    print('PASS blank memory card: 131,072 bytes match the psx-spx format')


if __name__ == '__main__': main()
