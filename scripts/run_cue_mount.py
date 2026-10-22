#!/usr/bin/env python3
"""Check psx_cue_mount (a .cue picked with Load disc) against a model APF host.

Each case writes a small SD card file system (cue, track files), the
expected one-file disc and its track table from make_pocket_instances.py's
own one_file_plan() and track_table(), then runs sim/pocket/tb_cue_mount.sv
with Icarus Verilog.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from tool_paths import ROOT, find_tool
sys.path.insert(0, str(ROOT/'scripts'))
from make_pocket_instances import LIBCRYPT_MSF, SECTOR, libcrypt_mask, one_file_plan, parse_cue, track_table

COMMON = '/Assets/playstation/common/'


def SERV_FILES(serv):
    """SERV's ISC-licensed RTL (serv_compdec.v, Apache-2.0, is for the unused compressed ISA)."""
    return sorted(p for p in serv.glob('serv_*.v') if p.name not in ('serv_compdec.v', 'serv_synth_wrapper.v'))


SYNC = b'\x00' + b'\xff'*10 + b'\x00'


def pattern(seed, sectors):
    """Raw sectors: the sync pattern (a .bin starts with 0x00), then filler."""
    data = bytearray(((seed*131 + n*7 + (n >> 8)*13) & 0xff) for n in range(sectors*SECTOR))
    for s in range(sectors): data[s*SECTOR:s*SECTOR + 12] = SYNC
    return bytes(data)


REDUMP = ('My Game (USA)', 'My Game (USA).cue',
          'REM a comment\r\nFILE "My Game (USA) (Track 1).bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'
          'FILE "My Game (USA) (Track 2).bin" BINARY\r\n  TRACK 02 AUDIO\r\n    INDEX 00 00:00:00\r\n    INDEX 01 00:02:00\r\n'
          'FILE "My Game (USA) (Track 3).bin" BINARY\r\n  TRACK 03 AUDIO\r\n    INDEX 00 00:00:00\r\n    INDEX 01 00:02:00\r\n',
          {'My Game (USA) (Track 1).bin': 10, 'My Game (USA) (Track 2).bin': 170, 'My Game (USA) (Track 3).bin': 155})

# A merged image with 25 tracks (more than a game JSON's per-track slots).
def msf(f): return f'{f//4500:02d}:{f//75%60:02d}:{f%75:02d}'
MANY = ''.join(f'  TRACK {k:02d} AUDIO\n    INDEX 00 {msf(30*k-45)}\n    INDEX 01 {msf(30*k-30)}\n' for k in range(2, 26))
MANY = 'FILE "Many.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' + MANY

# A two-disc game in redump's layout (one folder per disc), from a Pocket
# Sync/pupdate game JSON for disc 1; the menu's Disc list switches discs.
D1, D2 = 'Two (Disc 1)/Two (Disc 1)', 'Two (Disc 2)/Two (Disc 2)'
DISC1 = (D1 + '.cue', 'FILE "Two (Disc 1).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
DISC2 = (D2 + '.cue', 'FILE "Two (Disc 2) (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
         'FILE "Two (Disc 2) (Track 2).bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n')

# A redump .sbi next to the cue: three LibCrypt sectors and one other.
def sbi_bytes():
    entries = [LIBCRYPT_MSF[0], 0x012345, LIBCRYPT_MSF[7], LIBCRYPT_MSF[15]]
    return b'SBI\0' + b''.join(m.to_bytes(3, 'big') + b'\x01' + bytes(10) for m in entries)

# A cheat list (slot 98): comments, libretro's form, writes, a condition
# that holds and one that doesn't, a byte condition, an increment (three
# frames on) and a serial code. CHEAT_RAM: halfword index, value after.
CHEATS = ('# Cheats for My Game: 80000000 1111 is a comment\n; 80000002 2222 too\n'
          'cheat0_desc = "Max HP"\ncheat0_code = "80010000+1234"\n30010003 00AB\n'
          'D0010000 1234\n80010010 5555\nD0010000 9999\n80010020 6666\nE0010003 00AB\n80010030 7777\n'
          '10010040 0001\n50000402 0010\n80010050 0100\n')
# libretro's .cht with enable flags: only cheat 1's two codes apply.
RETRO = ('cheats = 3\n\ncheat0_desc = "Infinite Lives"\ncheat0_code = "80010060+1111"\ncheat0_enable = false\n\n'
         'cheat1_desc = "Max Money"\ncheat1_code = "80010062+2222+80010064+3333"\ncheat1_enable = true\n\n'
         'cheat2_desc = "Moon Jump"\ncheat2_code = "80010066+4444"\ncheat2_enable = false\n')
RETRO_RAM = [(0x8030, 0), (0x8031, 0x2222), (0x8032, 0x3333), (0x8033, 0)]
CHEAT_RAM = [(0, 0), (1, 0), (0x8000, 0x1234), (0x8001, 0xAB00), (0x8008, 0x5555), (0x8010, 0), (0x8018, 0x7777),
             (0x8020, 0x0103), (0x8028, 0x0100), (0x8029, 0x0110), (0x802A, 0x0120), (0x802B, 0x0130), (0x802C, 0)]

# (name, folder, cue name, cue text, {file: sectors}, expectation[, mode[, other files]])
# Other files are (name, text or bytes); a "(Disc 2)" cue among them is the
# menu's Disc 2. A picked .m3u's disc 1 is the first cue among them.
# mode 'lc': a core built with LibCrypt on (prepare --cue-libcrypt); 'ch':
# with the cheat port (prepare --cheats), the list in the game JSON's slot 98.
# A name with a / is a path under the folder.
# 'launch': picked from the launch list (slot 0, read before the stream's
# allcomplete) instead of with Load disc (slot 101). 'json-cue': a game JSON
# from the launch list with the cue in slot 100 and no track table (Pocket
# Sync, pupdate); 'json-trk': one with the track table in slot 102
# (make_pocket_instances.py), left to the stream.
CASES = [
    ('redump', *REDUMP, 'ok'),
    # BOM, LF only, lower case keywords, merged image with INDEX 00 inside
    # it, no final newline and a size of 4n+3 (the last word read alone).
    ('merged', 'B', 'Merged.cue',
     '﻿FILE "Merged.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    INDEX 00 00:00:20\n'
     '    INDEX 01 00:02:20\n  TRACK 03 AUDIO\n    INDEX 00 00:04:00\n    INDEX 01 00:06:01',
     {'Merged.bin': 500}, 'ok'),
    ('unquoted', 'Abc/Sub folder', 'Game.cue', 'FILE Game.bin BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
     {'Game.bin': 12}, 'ok'),
    ('plain-bin', 'Plain', 'Plain.bin', None, {'Plain.bin': 4}, 'bin'),
    # PREGAP before a file's first track: silence not in the files.
    ('pregap', 'Gap', 'Gap.cue', 'FILE "Gap.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
     'FILE "Gap2.bin" BINARY\n  TRACK 02 AUDIO\n    PREGAP 00:02:00\n    INDEX 01 00:00:00\n'
     'FILE "Gap3.bin" BINARY\n  TRACK 03 AUDIO\n    PREGAP 00:00:10\n    INDEX 01 00:00:00\n', {'Gap.bin': 5, 'Gap2.bin': 9, 'Gap3.bin': 4}, 'ok'),
    # Track 1's pregap inside the first file (INDEX 00 at the start, INDEX 01
    # two seconds in): the disc starts at INDEX 01.
    ('lead', 'Lead', 'Lead.cue', 'FILE "Lead.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n'
     'FILE "Lead2.bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n', {'Lead.bin': 160, 'Lead2.bin': 170}, 'ok'),
    # One inside a file (ImgBurn-style merged cues: track 2's gap is not in
    # the .bin): silence inserted before that track's INDEX 01.
    ('pregap-mid', 'Gap', 'Gap.cue', 'FILE "Gap.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
     '  TRACK 02 AUDIO\n    PREGAP 00:02:00\n    INDEX 01 00:00:02\n', {'Gap.bin': 5}, 'ok'),
    # The tester's layout (Rayman, Tomb Raider), scaled down: one merged
    # .bin, PREGAP on track 2, INDEX 00/01 on the later tracks, from a
    # Pocket Sync game JSON.
    ('json-imgburn', 'Ray', 'Ray.cue', 'FILE "Ray.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
     '  TRACK 02 AUDIO\n    PREGAP 00:02:00\n    INDEX 01 00:00:40\n'
     + ''.join(f'  TRACK {t:02} AUDIO\n    INDEX 00 00:{t:02}:10\n    INDEX 01 00:{t:02}:60\n' for t in range(3, 9)),
     {'Ray.bin': 700}, 'ok', 'json-cue'),
    ('pick-imgburn', 'Ray', 'Ray.cue', 'FILE "Ray.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
     '  TRACK 02 AUDIO\n    PREGAP 00:02:00\n    INDEX 01 00:00:40\n  TRACK 03 AUDIO\n    PREGAP 00:01:00\n    INDEX 01 00:03:00\n',
     {'Ray.bin': 300}, 'ok'),
    # A PREGAP with an INDEX 00 in the same track can't be placed.
    ('pregap-index0', 'Gap', 'Gap.cue', 'FILE "Gap.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
     '  TRACK 02 AUDIO\n    INDEX 00 00:00:02\n    PREGAP 00:02:00\n    INDEX 01 00:00:03\n', {'Gap.bin': 5}, 'fail'),
    ('cooked', 'Cooked', 'Cooked.cue', 'FILE "Cooked.bin" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n', {'Cooked.bin': 3}, 'fail'),
    # A one-file cue renamed with its .bin (the cue still names the old
    # file): "<cue name>.bin" is opened instead.
    ('renamed', 'Ren', 'Renamed.cue', 'FILE "Old Name.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Renamed.bin': 6}, 'ok'),
    # A game JSON's one-file cue naming a file the core can't open by path
    # (Doom's merged layout, scaled down): read from the JSON's slot 101.
    ('json-path', 'Doom', 'Doom.cue', 'FILE "Elsewhere/Doom.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
     + ''.join(f'  TRACK {t:02} AUDIO\n    INDEX 00 00:{t:02}:14\n    INDEX 01 00:{t:02}:50\n' for t in range(2, 9)),
     {'Doom (Single Bin).bin': 800}, 'ok', 'json-cue'),
    ('missing', 'Missing', 'Missing.cue', 'FILE "Nope.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {}, 'fail'),
    ('launch-cue', *REDUMP, 'ok', 'launch'),
    ('launch-bin', 'Plain', 'Plain.bin', None, {'Plain.bin': 4}, 'bin', 'launch'),
    ('launch-json', 'x', 'Game.json', '{"instance": {}}\n', {}, 'json', 'launch'),
    ('launch-none', 'x', 'none', None, {}, 'json', 'launch'),
    ('json-redump', *REDUMP, 'ok', 'json-cue'),
    ('json-many', 'Many', 'Many.cue', MANY, {'Many.bin': 800}, 'ok', 'json-cue'),
    ('json-trk', *REDUMP, 'json', 'json-trk'),
    ('launch-bad', 'Cooked', 'Cooked.cue', 'FILE "Cooked.bin" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n', {'Cooked.bin': 3}, 'fail', 'launch'),
    # A cold card (the first launch after a restart or card writes): the
    # first two tries' reads fail (TRIES, COLD), the third mounts.
    ('json-cold', *REDUMP, 'ok', 'json-cue'),
    # A 0.5.2 game JSON for a three-file game: cue in 100, the .bin files in
    # 101-103 (track 2's .bin where 0.5.3 has its track table), read there.
    ('json-052', *REDUMP, 'ok', 'json-052'),
    ('json-multi', 'Two', *DISC1, {D1 + '.bin': 40, D2 + ' (Track 1).bin': 30, D2 + ' (Track 2).bin': 170}, 'ok', 'json-cue', (DISC2,)),
    # A DuckStation-style playlist from the launch list: a comment, a
    # backslash and trailing spaces.
    ('launch-m3u', 'Two', 'Two.m3u', '#EXTM3U\r\nTwo (Disc 1)/Two (Disc 1).cue\r\nTwo (Disc 2)\\Two (Disc 2).cue  \r\n',
     {D1 + '.bin': 40, D2 + ' (Track 1).bin': 30, D2 + ' (Track 2).bin': 170}, 'ok', 'launch', (DISC1, DISC2)),
    ('json-libcrypt', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', 'json-cue-lc',
     (('Lc.sbi', sbi_bytes()),)),
    # A game JSON whose slots have no path (APF on hardware): the cue's
    # .sbi comes from the JSON's slot 99.
    ('json-sbi99', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', 'json-sbi99-lc',
     (('Lc.sbi', sbi_bytes()),)),
    ('json-cheats', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', 'json-cue-ch-lc',
     (('Lc.sbi', sbi_bytes()), ('Lc.cht', CHEATS))),
    ('json-cheats-retro', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', 'json-cue-ch-lc',
     (('Lc.cht', RETRO),)),
    # The Chip32 loader's size block (+sizes): the boot mount skips its probes.
    ('json-sizes', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', 'json-sbi99-lc',
     (('Lc.sbi', sbi_bytes()),)),
    ('json-sizes-swap', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', 'json-sbi99-lc',
     (('Lc.sbi', sbi_bytes()),)),
    ('json-cheats-sizes', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', 'json-cue-ch-lc',
     (('Lc.cht', RETRO),)),
    ('launch-sizes', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', 'launch-lc',
     (('Lc.sbi', sbi_bytes()),)),
    ('launch-libcrypt', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', 'launch-lc',
     (('Lc.sbi', sbi_bytes()),)),
    ('pick-libcrypt', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', '-lc',
     (('Lc.sbi', sbi_bytes()),)),
    # The Pocket refused the open of a path as getfile wrote it (4): the
    # firmware tries the bytes swapped in each word, then a second struct.
    ('libcrypt-off', 'Lc', 'Lc.cue', 'FILE "Lc.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', {'Lc.bin': 8}, 'ok', '',
     (('Lc.sbi', sbi_bytes()),)),
]


def words_hex(data):
    data = data + bytes(-len(data) % 4)
    return ''.join(f'{int.from_bytes(data[i:i+4], "little"):08x}\n' for i in range(0, len(data), 4))


def gap_plan(cue):
    """one_file_plan() extended as the firmware is: a PREGAP is zero
    sectors on the disc that are not in the files, before its track's INDEX 01
    (before the file when it is the file's first track), and a first file
    holding track 1's pregap (INDEX 01 past its start) starts the disc at
    INDEX 01. (image bytes, sources, tracks)."""
    files = []   # [name, [[index00 or None, index01, pregap]]]
    msf = lambda t: (lambda m, s, f: (m*60 + s)*75 + f)(*map(int, t.split(':')))
    for line in cue.read_text().splitlines():
        w = line.split()
        if w[0] == 'FILE': files.append([line.split('"')[1], []])
        elif w[0] == 'TRACK': files[-1][1].append([None, None, 0])
        elif w[0] == 'PREGAP': files[-1][1][-1][2] = msf(w[1])
        elif w[0] == 'INDEX': files[-1][1][-1][int(w[1])] = msf(w[2])
    lead = files[0][1][0][1]
    image, sources, tracks = b'', [], []
    for k, (name, ts) in enumerate(files):
        data = (cue.parent/name).read_bytes()[(lead if k == 0 else 0)*SECTOR:]
        at, start = 0, len(image)//SECTOR   # file sectors emitted; the disc sector it began at
        for i0, i1, gap in ts:
            if k == 0: i0, i1 = max((i0 if i0 is not None else i1) - lead, 0), i1 - lead
            else: i0 = i1 if i0 is None else i0
            if gap:   # the file's data up to INDEX 01, then the silence
                image += data[at*SECTOR:i1*SECTOR]; at = i1
                here = len(image)//SECTOR; image += bytes(gap*SECTOR)
                tracks.append((here, here + gap))
            else:
                here = len(image)//SECTOR
                tracks.append((here + i0 - at, here + i1 - at))
        image += data[at*SECTOR:]
        sources.append((cue.parent/name, len(image)//SECTOR - start))
    return image, sources, tracks


def expected(case, disk, cue, suffix='', mask=0):
    """Write the one-file image, table and test sectors of a cue; (bytes, sectors)."""
    text = (disk/cue).read_text()
    named = [(l.split('"')[1] if '"' in l else l.split()[1]) for l in text.splitlines() if l.startswith('FILE')]
    bins = sorted(f.name for f in (disk/cue).parent.glob('*.bin'))
    if len(named) == 1 and not ((disk/cue).parent/named[0]).is_file() and len(bins) == 1:
        # The file the firmware falls back to: <cue name>.bin, or a game
        # JSON's slot 101.
        cue = cue + '.model'
        (disk/cue).write_text(text.replace(named[0], bins[0]))
    if 'PREGAP' in text or 'lead' in case.name:
        image, sources, tracks = gap_plan(disk/cue)
    else:
        plan = one_file_plan(parse_cue(disk/cue), (disk/cue).parent)
        if isinstance(plan, str): raise SystemExit(f'{case.name}: generator plan failed: {plan}')
        sources, tracks = plan
        image = b''.join(p.read_bytes() for p, _ in sources)
    sectors = len(image)//SECTOR
    table = track_table(tracks, sectors, region=0, mask=mask)
    (case/f'table{suffix}.hex').write_text(''.join(f'{w:08x}\n' for w in table))
    (case/f'image{suffix}.hex').write_text(''.join(f'{b:02x}\n' for b in image))
    bounds, at = [0, sectors - 1], 0
    for _, n in sources[:-1]:
        at += n; bounds += [at - 1, at]
    bounds += [t[1] for t in tracks]
    bounds = list(dict.fromkeys(bounds))[:15]
    bounds = bounds[2:] + bounds[:2]   # out of order: forces reopens
    (case/f'bounds{suffix}.hex').write_text(''.join(f'{b:08x}\n' for b in bounds + [0xffffffff]*(16 - len(bounds))))
    return len(image), sectors


# Launch tries (reads of slot 0) a case takes: a failed launch mount is
# tried three times, the stream's allcomplete held until the last. Other
# launches take one.
TRIES = {'launch-bad': 3, 'launch-json': 3, 'launch-none': 3, 'json-cold': 3}
COLD = {'json-cold': 6}   # commands the host fails first: slot 0, slot 102 and the cue in slot 100, twice

# The pause before a launch retry (start.S retry_wait at 0x10, in wait loop
# turns of 104 cycles) is shortened for the bench in a copy of the firmware;
# a retry must still come at least BENCH_WAIT * MIN_TURN cycles after the
# last command (without the pause launch-bad's retries come after ~169,000).
RETRY_WAIT_AT, BENCH_WAIT, MIN_TURN = 0x10, 3000, 100


# Cases given the Chip32 loader's size block, and the most reads their boot
# mount may take (the size probes alone are 13-19 reads each).
SIZE_ARGS = {'json-sizes': ['+sizes', '+maxreads=8'], 'json-sizes-swap': ['+sizes_swap', '+maxreads=8'],
             'json-cheats-sizes': ['+sizes', '+maxreads=8'], 'launch-sizes': ['+sizes', '+maxreads=36']}


def bench_firmware(work):
    """A copy of rtl/pocket/psx_cue_fw0-3.hex with retry_wait set to BENCH_WAIT; its prefix."""
    lanes = [(ROOT/f'rtl/pocket/psx_cue_fw{k}.hex').read_text().split() for k in range(4)]
    want = int(re.search(r'^retry_wait:\s*\.word\s+(\w+)', (ROOT/'rtl/pocket/cue_fw/start.S').read_text(), re.M)[1], 0)
    word = RETRY_WAIT_AT//4
    got = sum(int(lanes[k][word], 16) << 8*k for k in range(4))
    if got != want: raise SystemExit(f'firmware word at {RETRY_WAIT_AT:#x} is {got:#x}, not retry_wait {want}: run scripts/build_cue_fw.py')
    for k in range(4):
        lanes[k][word] = f'{BENCH_WAIT >> 8*k & 0xff:02x}'
        (work/f'psx_cue_fw{k}.hex').write_text(''.join(b + '\n' for b in lanes[k]))
    return work/'psx_cue_fw'


def run_case(work, iverilog, vvp, name, folder, cue_name, cue_text, sizes, expect, mode='', other=()):
    case = work/name; case.mkdir()
    disk = case/'disk'; disk.mkdir()
    files = []   # (path on the card, bytes)
    def put(fname, data):
        (disk/fname).parent.mkdir(parents=True, exist_ok=True)
        (disk/fname).write_bytes(data)
        files.append((COMMON + folder + '/' + fname, data))
    for n, (fname, sectors) in enumerate(sizes.items()):
        put(fname, pattern(n + 1, sectors))
    for fname, data in other: put(fname, data if isinstance(data, bytes) else data.encode('utf-8'))
    sbi = next((disk/f for f, _ in other if f.endswith('.sbi')), None)
    disc1 = next(f for f, _ in other if f.endswith('.cue')) if cue_name.endswith('.m3u') else cue_name
    disc2 = next((f for f, _ in other if '(Disc 2)' in f and f.endswith('.cue')), None)
    lc = mode.endswith('-lc'); mode = mode.removesuffix('-lc')
    ch = mode.endswith('-ch'); mode = mode.removesuffix('-ch')
    form = mode[-1] if '-of' in mode else ''   # '-ofN': the host takes only open form N
    mode = mode.split('-of')[0]
    if cue_text is not None: put(cue_name, cue_text.encode('utf-8'))
    picked = len(files) - 1 if cue_text is not None else 0 if files else -1
    extra = []
    if mode.startswith('json'):
        extra.append(f'+cue100={picked}')
        if mode == 'json-sbi99':
            extra += ['+nogetfile', f'+slot99={next(i for i, (p, _) in enumerate(files) if p.endswith(".sbi"))}']
        if mode in ('json-cue', 'json-052', 'json-sbi99') and (len(sizes) == 1 or mode == 'json-052'):
            # pupdate/Pocket Sync (and 0.5.2's layout): the .bin files in 101, 102, ...
            for k, fn in enumerate(sizes):
                extra.append(f'+slot{101 + k}={next(i for i, (p, _) in enumerate(files) if p.endswith("/" + fn))}')
        if ch:
            extra += ['+cheats', '+cheatcheck=1', f'+slot98={next(i for i, (p, _) in enumerate(files) if p.endswith(".cht"))}']
            ram = RETRO_RAM if 'enable' in next(d for p, d in files if p.endswith('.cht')).decode() else CHEAT_RAM
            (case/'cheatcheck.hex').write_text(''.join(f'{a:08x}\n{v:08x}\n' for a, v in ram) + 'ffffffff\n')
        files.append((COMMON + folder + '/Game.json', b'{"instance": {}}\n'))
        if mode == 'json-trk':
            extra.append(f'+trk102={len(files)}')
            files.append((COMMON + folder + '/Game.trk', bytes(1600)))
        picked = len(files) - (2 if mode == 'json-trk' else 1)
    blob, meta, paths = bytearray(), [], []
    for path, data in files:
        meta += [len(blob), len(data)]
        blob += data
        p = path.encode() + b'\0'
        paths.append(p + bytes(256 - len(p)))
    (case/'fs.hex').write_text(''.join(f'{b:02x}\n' for b in blob))
    (case/'meta.hex').write_text(''.join(f'{v:08x}\n' for v in meta))
    (case/'paths.hex').write_text(''.join(words_hex(p) for p in paths))
    args = [f'+base={case}', f'+nfiles={len(files)}', f'+picked={picked}'] + (['+launch'] if mode else []) + extra + ([f'+openform={form}'] if form else [])
    if mode:
        tries = TRIES.get(name, 1)
        args += [f'+tries={tries}', f'+cold={COLD.get(name, 0)}'] + ([f'+minpause={BENCH_WAIT*MIN_TURN}'] if tries > 1 else [])
    if expect == 'ok':
        size, sectors = expected(case, disk, disc1, mask=libcrypt_mask(sbi) if lc and sbi else 0)
        args += [f'+size={size}', f'+sectors={sectors}']
        if disc2:
            size, sectors = expected(case, disk, disc2, '2')
            args += [f'+size2={size}', f'+sectors2={sectors}']
    elif expect == 'json':
        args.append('+json')
    elif expect == 'bin':
        args.append('+bin')
    else:
        args.append('+fail')
    args += SIZE_ARGS.get(name, [])
    out = subprocess.run([vvp, str(work/('tb_ch.vvp' if ch else 'tb_lc.vvp' if lc else 'tb.vvp')), *args], capture_output=True, text=True, timeout=1800)
    text = out.stdout + out.stderr
    ok = 'PASS' in text and 'FAIL' not in text
    print(f'{name}: {"pass" if ok else "FAIL"}  ' + ' '.join(l for l in text.splitlines() if l.startswith(("launch", "mount", "menu", "probe", "cheats", "stack"))), flush=True)
    if not ok: print(text[-3000:])
    return ok


def main():
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    if not iverilog or not vvp: raise SystemExit('iverilog/vvp not found')
    parent = ROOT/'build/sim/cue-mount'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent)); print(work)
    serv = ROOT/'references/serv/rtl'
    if not (serv/'serv_rf_top.v').is_file(): raise SystemExit('references/serv is missing: git submodule update --init references/serv')
    fw = bench_firmware(work)
    for vvp_name, lc, ch in (('tb.vvp', 0, 0), ('tb_lc.vvp', 1, 0), ('tb_ch.vvp', 1, 1)):
        subprocess.run([iverilog, '-g2012', '-o', str(work/vvp_name), f'-Ptb_cue_mount.FW="{fw}"',
                        f'-Ptb_cue_mount.LC={lc}', f'-Ptb_cue_mount.CH={ch}', *[str(p) for p in SERV_FILES(serv)], str(ROOT/'rtl/pocket/psx_cue_mount.sv'),
                        str(ROOT/'sim/pocket/tb_cue_mount.sv')], check=True)
    only = sys.argv[1:]   # case names, or all
    results = [run_case(work, iverilog, vvp, *case) for case in CASES if not only or case[0] in only]
    print(f'{sum(results)}/{len(results)} cases pass')
    if not all(results): raise SystemExit(1)


if __name__ == '__main__':
    main()
