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


def load(path, dma=False):
    """Frame rows of one log, oldest first. Header word 1 bit 31 marks 8-word
    records (sync PSRAM builds: clk3x cycles adapter busy / pair reading /
    pair writing), 511 frames; otherwise 4-word records, 1023 frames."""
    words = words_of(path)
    stages = words[1] >> 31
    video = (words[1] >> 30) & 1   # psx_apf_video_wide LOG
    video2 = video and (words[1] >> 29) & 1   # 9-word change-driven records (2026-10-21)
    recorded, slot = words[1] & 0xffff, words[3] & 0x3ff
    tag = (words[3] >> 20) & 0x3ff   # word 3: tag in 29:20, FRAMES-1 in 19:10, slot in 9:0
    ring = (words[3] >> 10) & 0x3ff  # published since 2026-10-20 (0 in older logs)
    cd = (words[1] >> 28) & 1 and not stages and not video   # --perf-cd: 5-word records, word 4 = CD counters
    allm = cd and tag in (10, 11)   # combined diagnosis builds (CD=4): tag 10 = 7-word records, tag 11 = 8 words (+ VRAM write times)
    stride, frames = (8, 511) if stages else (9, 227) if video2 else (7, 511) if video else ((8, 255) if tag == 11 else (7, 292)) if allm else (5, 408) if cd else (4, 1023)
    if ring + 1 >= 8: frames = ring + 1   # the log says how deep its own ring is
    n = frames if recorded >= frames else recorded   # recorded wraps at 65536
    # A truncated save (the file is ring-shaped: 4 + PHASES x FRAMES words) must
    # not index past the data: clamp to the records the file really holds.
    n = min(n, max(0, (len(words) - 4) // stride))
    start = slot if recorded >= frames else 0
    rows = []
    for i in range(n):
        base = 4 + stride * ((start + i) % frames)
        w0, ram, spu, w3 = words[base:base + 4]
        reqs = (spu >> 20) << 12 | ram >> 20   # 0 in logs from before the request count
        row = dict(cycles=w0 & 0xfffff, samples=w0 >> 22, ram=ram & 0xfffff, spu=spu & 0xfffff, reqs=reqs,
                   vram_busy=(w3 >> 16) * 16, vram_wait=(w3 & 0xffff) * 16)
        if stages: row.update(adapter3=words[base + 4], read3=words[base + 5], write3=words[base + 6])
        if allm:   # w4 CD, w5 {dma_cycles/16, dma_ram_wait/16}, w6 {cache_wait/16, cache_reqs}
            w4, w5, w6 = (words[base + k] if base + k < len(words) else 0 for k in (4, 5, 6))
            row.update(cd_sectors=w4 >> 24, cd_seeks=w4 >> 20 & 15, cd_wait=w4 & 0xfffff,
                       dma_cycles=(w5 >> 16) * 16, dma_ram_wait=(w5 & 0xffff) * 16,
                       cache_wait=(w6 >> 16) * 16, cache_reqs=w6 & 0xffff)
            if tag == 11:   # w7 = {first VRAM write, last VRAM write}: clk2x cycles after the field tick / 32
                w7 = words[base + 7] if base + 7 < len(words) else 0
                row.update(first_we=(w7 >> 16) * 32, last_we=(w7 & 0xffff) * 32)
        elif cd:
            w4 = words[base + 4] if base + 4 < len(words) else 0
            if dma:   # perf3 builds (CD=2): word 4 = {dma_cycles[19:4], dma_ram_wait[19:4]}
                row.update(dma_cycles=(w4 >> 16) * 16, dma_ram_wait=(w4 & 0xffff) * 16)
            else:
                row.update(cd_sectors=w4 >> 24, cd_seeks=w4 >> 20 & 15, cd_wait=w4 & 0xfffff)
        if video2:
            w4, w5, w6, w7, w8 = words[base + 4:base + 9]
            row.update(lines=w4 >> 22, hash=w4 >> 12 & 0x3ff, slot_used=w4 >> 9 & 7,
                       pal=w4 >> 8 & 1, i480mode=w4 >> 7 & 1, bob=w4 >> 6 & 1, woven=w4 >> 5 & 1,
                       fit=w4 >> 4 & 1, fill=w4 >> 3 & 1, hide_overscan=w4 >> 2 & 1,
                       field=w4 >> 1 & 1, pal_frame=w4 & 1,
                       out=w5 >> 22, first_kept=w5 >> 12 & 0x3ff, last_kept=w5 >> 2 & 0x3ff,
                       hs=w6 >> 22, line_w=w6 >> 12 & 0x3ff, pic_x0=w6 >> 2 & 0x3ff,
                       pic_y0=w7 >> 22, pic_y1=w7 >> 12 & 0x3ff, pic_x1=w7 >> 2 & 0x3ff,
                       frame=w8 >> 12, alternating=w8 >> 1 & 1, heartbeat=w8 & 1)
        elif video:
            w4, w5, w6 = words[base + 4:base + 7]
            row.update(lines=w4 >> 22, crop_top=w4 >> 12 & 0x3ff, slot_used=w4 >> 9 & 7,
                         pal=w4 >> 8 & 1, i480mode=w4 >> 7 & 1, bob=w4 >> 6 & 1, woven=w4 >> 5 & 1,
                         fit=w4 >> 4 & 1, fill=w4 >> 3 & 1, hide_overscan=w4 >> 2 & 1,
                         field=w4 >> 1 & 1, pal_frame=w4 & 1,
                         out=w5 >> 22, first_kept=w5 >> 12 & 0x3ff, last_kept=w5 >> 2 & 0x3ff,
                         hs=w6 >> 22, out_w=w6 >> 12 & 0x3ff, line_w=w6 >> 2 & 0x3ff)
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--every', type=int, help='print one record in N (default 30; every record for --video-log logs)')
    parser.add_argument('--dma', action='store_true', help='perf3 builds (CD=2): word 4 holds DMA stall counters, not CD counters')
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
    recorded, status, slot = words[1] & 0xffff, words[2], words[3] & 0x3ff
    # word 3: tag in 29:20, FRAMES-1 in 19:10 (published since 2026-10-20), slot in 9:0
    tag, ring = (words[3] >> 20) & 0x3ff, (words[3] >> 10) & 0x3ff
    frames = ring + 1 if ring >= 7 else (511 if (words[1] >> 31) or (words[1] >> 30) & 1 else 1023)
    video2 = (words[1] >> 30) & 1 and (words[1] >> 29) & 1
    if status not in (1023, 0):   # older logs held FRAMES in word 3
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
    rows = load(args.log, dma=args.dma)
    n = len(rows)
    print(f'frames recorded {recorded}, ring {frames}, decoded {n}')
    if video2:
        print(f'build tag in header word 3: {tag:03d} (prepare --video-log-tag)')
        print('A record is a frame unlike both frames before it (new), or every 256th (beat); "beat alt" means the')
        print('picture still changed from the frame before (e.g. two fields alternating). Lines and pixels are source indices;')
        print('top/bot = black lines above/below the picture in what the scaler got, of "out" lines.')
        print('  frame  kind  lines slot pal 480i bob fit fill hide fld  out kept     picture y  x          top bot  pic%  hs  line_w hash')
    elif words[1] >> 30 & 1:
        print(f'build tag in header word 3: {tag:03d} (prepare --video-log-tag)')
        print('frame   ms  lines crop slot pal 480i bob i480 fit fill hide fld  out first last   hs  outw line_w  main-RAM%  SPU-RAM%')
    else:
        print('frame   ms   samples  main-RAM%  SPU-RAM%  VRAM-busy%  VRAM-wait%  main-reqs  ns/req')
    for i, r in enumerate(rows):
        if i % (args.every or (1 if video2 else 30)) and i != len(rows) - 1: continue
        c = max(r['cycles'], 1)
        if 'frame' in r:
            # alternating on a heartbeat: the picture still changes every frame
            kind = 'new     ' if not r['heartbeat'] else 'beat alt' if r['alternating'] else 'beat    '
            if r['pic_y0'] > r['pic_y1']:
                pic, bars = '   (black)          ', '   -   -     -'
            else:
                top = max(r['pic_y0'], r['first_kept']) - r['first_kept']
                bot = r['last_kept'] - min(r['pic_y1'], r['last_kept'])
                kept = r['last_kept'] - r['first_kept'] + 1   # source lines (a doubled Fill frame sends each twice)
                pct = 100 * (kept - top - bot) / max(kept, 1)
                pic = f"{r['pic_y0']:3}..{r['pic_y1']:3} {r['pic_x0']:3}..{r['pic_x1']:3}"
                bars = f"{top:4} {bot:3} {pct:5.1f}"
            print(f"{r['frame']:7} {kind} {r['lines']:5} {r['slot_used']:4} {r['pal']:3} {r['i480mode']:4} {r['bob']:3} "
                  f"{r['fit']:3} {r['fill']:4} {r['hide_overscan']:4} {r['field']:3} {r['out']:4} "
                  f"{r['first_kept']:3}..{r['last_kept']:3}  {pic}  {bars} {r['hs']:4} {r['line_w']:6} {r['hash']:4}")
            continue
        if 'out' in r:
            print(f"{i:5} {c / CLK1X * 1000:6.2f} {r['lines']:5} {r['crop_top']:4} {r['slot_used']:4} "
                  f"{r['pal']:3} {r['i480mode']:4} {r['bob']:3} {r['woven']:4} {r['fit']:3} {r['fill']:4} "
                  f"{r['hide_overscan']:4} {r['field']:3} "
                  f"{r['out']:4} {r['first_kept']:5} {r['last_kept']:4} {r['hs']:4} {r['out_w']:4} {r['line_w']:6}"
                  f"   {100 * r['ram'] / c:7.1f}  {100 * r['spu'] / c:7.1f}")
            continue
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
