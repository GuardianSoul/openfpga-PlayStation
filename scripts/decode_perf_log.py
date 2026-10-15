#!/usr/bin/env python3
"""Decode a PSXEarly perf log (rtl/pocket/psx_perf_log.sv layout).

Per frame: clk1x cycles, SPU samples, cycles with a main-RAM or SPU-RAM request
outstanding (clk1x) and clk2x cycles of VRAM busy / first-read wait. The bridge
returns words one read late, so the header magic is found and aligned first.
"""
import argparse
from pathlib import Path
import struct

MAGIC = 0x50455246
CLK1X = 33_868_800
CLK3X = 3 * CLK1X


def summary(rows):
    """Averages over running frames (memory requests and SPU output; idle,
    paused or hung frames out). ns/request needs the request count (0 in old logs)."""
    busy = [r for r in rows[1:] if (r['ram'] or r['spu']) and r['samples']]
    reqs = sum(r['reqs'] for r in busy)
    tot = sum(r['cycles'] for r in busy) or 1
    return dict(frames=len(busy), main=100 * sum(r['ram'] for r in busy) / tot,
                spu=100 * sum(r['spu'] for r in busy) / tot, vram=100 * sum(r['vram_busy'] for r in busy) / (2 * tot),
                ms=sum(r['cycles'] for r in busy) / max(len(busy), 1) / CLK1X * 1000,
                reqs=reqs / max(len(busy), 1), ns=sum(r['ram'] for r in busy) / reqs / CLK1X * 1e9 if reqs else 0,
                **({k: sum(r[k] for r in busy) / reqs / CLK3X * 1e9 for k in ('adapter3', 'read3', 'write3')}
                   if reqs and busy and 'adapter3' in busy[0] else {}))


def words_of(path):
    """Log words with the bridge's one-read lag undone."""
    raw = path.read_bytes()
    words = list(struct.unpack(f'<{len(raw) // 4}I', raw[:len(raw) // 4 * 4]))
    shift = next((s for s in range(0, 3) if words[s] == MAGIC), None)
    if shift is not None: words = words[shift:] + [0] * shift
    else: words = [MAGIC] + words[:-1]   # the read lag dropped the magic
    return words


def load(path):
    """Frame rows of one log, oldest first. Header word 1 bit 31 marks 8-word
    records (sync PSRAM builds: clk3x cycles adapter busy / pair reading /
    pair writing), 511 frames; otherwise 4-word records, 1023 frames."""
    words = words_of(path)
    stages = words[1] >> 31
    recorded, slot = words[1] & 0xffff, words[3]
    stride, frames = (8, 511) if stages else (4, 1023)
    n = frames if recorded >= frames else recorded   # recorded wraps at 65536
    start = slot if recorded >= frames else 0
    rows = []
    for i in range(n):
        base = 4 + stride * ((start + i) % frames)
        w0, ram, spu, w3 = words[base:base + 4]
        reqs = (spu >> 20) << 12 | ram >> 20   # 0 in logs from before the request count
        row = dict(cycles=w0 & 0xfffff, samples=w0 >> 22, ram=ram & 0xfffff, spu=spu & 0xfffff, reqs=reqs,
                   vram_busy=(w3 >> 16) * 16, vram_wait=(w3 & 0xffff) * 16)
        if stages: row.update(adapter3=words[base + 4], read3=words[base + 5], write3=words[base + 6])
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--every', type=int, default=30, help='print one frame in N')
    parser.add_argument('--compare', type=Path, help='a second log (e.g. SDRAM baseline): print both summaries')
    args = parser.parse_args()
    if args.compare:
        for path in (args.log, args.compare):
            s = summary(load(path))
            print(f"{path.name}: {s['frames']} busy frames, {s['ms']:.2f} ms/frame, main RAM busy {s['main']:.1f}%, "
                  f"SPU RAM {s['spu']:.1f}%, VRAM {s['vram']:.1f}%"
                  + (f", {s['reqs']:.0f} main-RAM requests/frame, {s['ns']:.0f} ns each" if s['reqs'] else '')
                  + (f" (per request: main adapter busy {s['adapter3']:.0f} ns, PSRAM reading {s['read3']:.0f} ns,"
                     f" writing {s['write3']:.0f} ns; PSRAM time includes SPU/loader)" if 'adapter3' in s else ''))
        a, b = summary(load(args.log)), summary(load(args.compare))
        if b['main']: print(f"main-RAM busy time {a['main'] / b['main']:.2f}x of {args.compare.name}")
        return
    words = words_of(args.log)
    recorded, status, slot = words[1] & 0xffff, words[2], words[3]
    frames = 511 if words[1] >> 31 else 1023
    if status != 1023:   # older logs held FRAMES here
        print(f"status: bios passed {status >> 31 & 1}, failed {status >> 30 & 1}, loading {status >> 29 & 1}, "
              f"memory ready {status >> 28 & 1}, PSRAM init {status >> 27 & 1}, SDRAM init {status >> 26 & 1}, "
              f"BIOS words committed {status & 0x3ffff}")
        debug = status >> 18 & 0xff
        if debug & 0xf == 0xb:   # --logo-skip-setting diagnostic byte with the settings-write bit
            print(f"logo skip at save: setting {debug >> 7 & 1}, patch written {debug >> 6 & 1}, CPU released {debug >> 5 & 1}, "
                  f"any settings write (0x5xxxxxxx) seen {debug >> 4 & 1}")
        elif debug & 0x1f == 0x15:   # --logo-skip-setting diagnostic byte (bc78b88)
            print(f"logo skip at save: setting {debug >> 7 & 1}, patch written {debug >> 6 & 1}, CPU released {debug >> 5 & 1}")
        elif debug:   # split builds: {main 3x state, PSRAM pair state, arbiter busy} when the log was saved
            main = ['IDLE', 'ISSUE', 'WAIT', 'CACHE', 'CACHE_DRAIN', 'RETURN', '6', '7'][debug >> 5]
            pair = ['INIT', 'CFG0', 'CFG_WAIT', 'IDLE', 'WR', 'WR_WAIT', 'RD', 'RD_DATA', 'RSP', 'SETTLE', 'GAP',
                    '11', '12', '13', '14', '15'][debug >> 1 & 15]
            print(f"memory state at save: main adapter {main}, PSRAM pair {pair}, arbiter {'busy' if debug & 1 else 'free'}")
    rows = load(args.log)
    n = len(rows)
    print(f'frames recorded {recorded}, ring {frames}, decoded {n}')
    print('frame   ms   samples  main-RAM%  SPU-RAM%  VRAM-busy%  VRAM-wait%  main-reqs  ns/req')
    for i, r in enumerate(rows):
        if i % args.every and i != len(rows) - 1: continue
        c = max(r['cycles'], 1)
        print(f"{i:5} {c / CLK1X * 1000:6.2f} {r['samples']:6}   {100 * r['ram'] / c:7.1f}  {100 * r['spu'] / c:7.1f}"
              f"   {100 * r['vram_busy'] / (2 * c):8.1f}   {100 * r['vram_wait'] / (2 * c):8.1f}"
              + (f"  {r['reqs']:9}  {r['ram'] / r['reqs'] / CLK1X * 1e9:6.0f}" if r['reqs'] else ''))
    if rows:
        tot = sum(r['cycles'] for r in rows[1:]) or 1
        print(f"\naverage: {sum(r['samples'] for r in rows[1:]) / max(len(rows) - 1, 1):.1f} samples/frame "
              f"(44100 Hz at {CLK1X / (tot / max(len(rows) - 1, 1)):.2f} fps = "
              f"{44100 / (CLK1X / (tot / max(len(rows) - 1, 1))):.1f}), "
              f"main-RAM {100 * sum(r['ram'] for r in rows[1:]) / tot:.1f}%, "
              f"SPU-RAM {100 * sum(r['spu'] for r in rows[1:]) / tot:.1f}%, "
              f"VRAM busy {100 * sum(r['vram_busy'] for r in rows[1:]) / (2 * tot):.1f}%")
        m = summary(rows)
        if m['reqs']:
            print(f"running frames: {m['reqs']:.0f} main-RAM requests/frame, {m['ns']:.0f} ns each"
                  + (f"; per request main adapter busy {m['adapter3']:.0f} ns, PSRAM reading {m['read3']:.0f} ns,"
                     f" writing {m['write3']:.0f} ns (PSRAM time includes SPU/loader)" if 'adapter3' in m else ''))


if __name__ == '__main__':
    main()
