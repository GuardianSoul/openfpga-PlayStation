#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""cd_top: CD-ROM timing and GetlocP as a real PS1 measures them (opt-in, cd-timing-ps1).

The reference is ps1-tests' cdrom/timing log from a real console (system
clock ticks; 1x sectors average 446k against 451,584 per 1/75 s) and its
cdrom/getloc log. Upstream cd_top keeps emulator constants. Each part is a
switch (--only):

- getlocp: GetlocP's relative time on a data track. Upstream subtracts the
  2 s pregap only for audio tracks, so on data track 1 relative = absolute
  (00:02:09 / 00:02:09); the console answers 00:00:11 / 00:02:11 after a
  seek to 00:02:16. Index 0 (the pregap countdown) already matches.
- seek: seek time in absolute clocks (seekCycles) instead of the speed's
  sector time times seekTimeMul, so 2x no longer halves it. Short seeks
  (under 4,500 sectors) take (12 + d/256) x 28,224 clocks: a 100-sector seek
  is 0.34M, and with the first sector the console's 0.67-1.02M (1x and 2x).
  Longer seeks are not measured: (29 + d/1024) x 28,224, continuous, 0.93M
  to 7.7M (0.23 s) for the whole disc, under upstream's cap; the 1.8 s sled
  start from LBA 0 after Stop stays.
  The first sector comes two sector periods after the seek; at 1x those
  are already 0.9M, so a short 1x seek adds only the distance term
  (d/256 x 28,224): bench 0.90M at 1x, 0.79M-1.05M at 2x.
- speed: a Setmode speed change waits 400,000 clocks instead of 27.1M (2x)
  or 33.9M (1x): the console's first 2x sector after Setmode 0x80 arrives
  554k-1.02M after ReadN, seek included.
- pause: Pause while reading completes after 1,012,000 clocks at 1x and
  1,037,000 at 2x (console: 1.006M-1.022M and 1.029M-1.046M); upstream waits one rotation with psx-spx's
  minimums (2,157,295 / 1,066,874), 2.7M at 1x. Pause while playing keeps
  the rotation target.
- ack: the first response after a command, the console's average per
  command: Nop/Mute/Demute/Setmode and the rest 48,000, Setloc 58,000,
  Init 75,000, ReadN/ReadS 77,000, Pause 36,000. Upstream: 25,000 for all.
- ackjitter (instead of ack, about +50 ALMs, not in the default set): the
  first response after a command per command, with jitter from a
  free-running 16-bit LFSR: Nop/Mute/Setmode and the rest 24,000 + 0..32,767
  (+65,536 one time in eight) (console: Nop 24k-137k, average 46k);
  Setloc 32,000 + the same; Init 69,000 + 0..16,383 (69k-94k); ReadN/ReadS
  34,000 + 0..16,383 (+75,000 one time in two: the console's 34k-46k and
  107k-131k); Pause 28,000 + 0..16,383. Upstream: 25,000 for all.
- init: Init completes 476,000 clocks after its ACK (console: 138k-600k,
  average 476k); upstream adds 4,000,000 (MiSTer's fix for Virtual Pool 3
  and Disruptor's logo, which this undoes: test those).
- initspin: after Init the drive is busy until Init completes (476k + 20k)
  instead of 0.5 s (16.9M); console: no wait in ps1-tests' order (first 1x
  sector 668k-984k about 5M clocks after the last Init). Init in 2x keeps its
  extra second.
Usage: prepare_cd_timing_ps1.py PROJECT_OR_cd_top.vhd [--only getlocp,seek,...]
"""
import argparse
from pathlib import Path
import re

PARTS = ('getlocp', 'seek', 'speed', 'pause', 'ack', 'init', 'initspin')   # the default set
ALL_PARTS = PARTS + ('ackjitter',)   # ackjitter replaces ack (about +50 ALMs)

EDITS = {
    'getlocp': [
        ('''                     if (isAudio = '0') then
                        subchannelLBAwork    <= subchannelSector - startLBA;
                     else
                        subchannelLBAwork    <= subchannelSector - startLBA - PREGAPSIZE;
                     end if;
''', '''                     -- cd-timing-ps1: relative to INDEX 01 for data tracks too
                     subchannelLBAwork    <= subchannelSector - startLBA - PREGAPSIZE;
'''),
    ],
    'seek': [
        ('''   signal seekTimeMul               : integer range 0 to 137;''',
         '''   signal seekTimeMul               : integer range 0 to 137;
   signal seekCycles                : integer range 0 to 134217727 := 0;   -- cd-timing-ps1'''),
        ('''               -- todo: research more accurate values
               if (diffLBA < 3) then
                  seekTimeMul <= 3;
               elsif (diffLBA < 5) then
                  seekTimeMul <= diffLBA;
               elsif (diffLBA < 32) then
                  seekTimeMul <= 5;
               elsif (diffLBA < 75) then
                  seekTimeMul <= 5 + diffLBA / 8; -- 5 .. 14
               elsif (diffLBA < 4500) then
                  seekTimeMul <= 14 + diffLBA / 256; -- 14 .. 31

               elsif (diffLBA < 250000) then
                  seekTimeMul <= 31 + diffLBA / 8192; -- 31 .. 73
               else
                  -- extreme sled seek only when starting from inner position after Stop
                  if (currentLBA = 0) then
                     seekTimeMul <= 137;
                  else
                     seekTimeMul <= 31 + diffLBA / 8192;
                  end if;
               end if;
''', '''               -- cd-timing-ps1: absolute clocks, 28224 = a 1x sector / 16. Short seeks
               -- (a 100-sector seek with its first sector: 0.67-1.02M on a PS1 at 1x and 2x).
               -- the first sector follows two sector periods after the seek: at 1x those already
               -- make 0.9M (console 0.67M-0.98M), so short seeks add only the distance term there
               if (diffLBA < 4500 and modeReg(7) = '0') then
                  seekCycles <= (diffLBA / 256) * 28224;
               elsif (diffLBA < 4500) then
                  seekCycles <= (12 + diffLBA / 256) * 28224;
               elsif (diffLBA < 250000) then   -- not measured: continuous from 0.93M to 7.7M (0.23 s)
                  seekCycles <= (29 + diffLBA / 1024) * 28224;
               else
                  -- extreme sled seek only when starting from inner position after Stop
                  if (currentLBA = 0) then
                     seekCycles <= 137 * 451584;
                  else
                     seekCycles <= (29 + diffLBA / 1024) * 28224;
                  end if;
               end if;
'''),
        ('''               if (limitDelayTime = '1' and (driveDelay + driveREADSPEED * seekTimeMul) > 22880000) then -- max seek time
                  driveDelay <= 22880000;
               else
                  driveDelay <= driveDelay + driveREADSPEED * seekTimeMul;
               end if;''', '''               if (limitDelayTime = '1' and (driveDelay + seekCycles) > 22880000) then -- max seek time
                  driveDelay <= 22880000;
               else
                  driveDelay <= driveDelay + seekCycles;
               end if;'''),
        # a read without a seek: one sector at the current speed, as upstream
        ('''                  seekTimeMul        <= 1;
''', '''                  seekCycles         <= driveREADSPEED;
''', 2),
    ],
    'speed': [
        ('''                     if (newMode(7) = '1') then
                        driveDelay     <= 27095040; -- 80%
                     else
                        driveDelay     <= 33868800; -- 44100 * 0x300; -- 100%
                     end if;''', '''                     -- cd-timing-ps1: no long stall (a PS1's first 2x sector after Setmode 0x80: 554k-1.02M)
                     driveDelay     <= 400000;'''),
    ],
    'pause': [
        ('''                                 pause_target_v := pause_rotation_ticks - (2 * driveREADSPEED);
''', '''                                 -- cd-timing-ps1: PS1 1.006M-1.022M at 1x, 1.029M-1.046M at 2x
                                 if (modeReg(7) = '1') then pause_target_v := 1037000; else pause_target_v := 1012000; end if;
'''),
        ('''                              if (modeReg(7) = '1') then
                                 pause_min_v := 1066874;
                              else
                                 pause_min_v := 2157295;
                              end if;''', '''                              if (driveState = DRIVE_READING and modeReg(7) = '1') then
                                 pause_min_v := 1037000;   -- cd-timing-ps1
                              elsif (driveState = DRIVE_READING) then
                                 pause_min_v := 1012000;   -- cd-timing-ps1
                              elsif (modeReg(7) = '1') then
                                 pause_min_v := 1066874;
                              else
                                 pause_min_v := 2157295;
                              end if;'''),
    ],
    'ack': [
        ("""                  if (driveState = DRIVE_OPENING) then
                     cmd_delay  <= 15000 - 2;
                  else
                     cmd_delay  <= 25000 - 2;
                  end if;""", """                  -- cd-timing-ps1: the console's average ACK per command (ps1-tests cdrom/timing)
                  if (driveState = DRIVE_OPENING) then
                     cmd_delay  <= 15000 - 2;
                  elsif (newCmd = x"0A") then   -- Init: 69k-94k, average 75k
                     cmd_delay  <= 75000 - 2;
                  elsif (newCmd = x"06" or newCmd = x"1B") then   -- ReadN/ReadS: 34k-131k, average 77k
                     cmd_delay  <= 77000 - 2;
                  elsif (newCmd = x"09") then   -- Pause: 28.6k-43.9k, average 36k
                     cmd_delay  <= 36000 - 2;
                  elsif (newCmd = x"02") then   -- Setloc: 34k-182k, average 58k
                     cmd_delay  <= 58000 - 2;
                  else   -- Nop, Mute, Demute, Setmode: averages 46k-50k
                     cmd_delay  <= 48000 - 2;
                  end if;"""),
    ],
    'ackjitter': [
        ('''   signal cmd_delay                 : integer range 0 to 120000;
''', '''   signal cmd_delay                 : integer range 0 to 131071;
   signal ack_lfsr                  : std_logic_vector(15 downto 0) := x"ACE1";   -- cd-timing-ps1
'''),
        ('''                  if (driveState = DRIVE_OPENING) then
                     cmd_delay  <= 15000 - 2;
                  else
                     cmd_delay  <= 25000 - 2;
                  end if;''', '''                  -- cd-timing-ps1: per command, with the console's spread (ps1-tests cdrom/timing)
                  if (driveState = DRIVE_OPENING) then
                     cmd_delay  <= 15000 - 2;
                  elsif (newCmd = x"0A") then   -- Init: 69k-94k
                     cmd_delay  <= 69000 - 2 + to_integer(unsigned(ack_lfsr(13 downto 0)));
                  elsif (newCmd = x"06" or newCmd = x"1B") then   -- ReadN/ReadS: 34k-46k or 107k-131k
                     if (ack_lfsr(15) = '1') then
                        cmd_delay <= 109000 - 2 + to_integer(unsigned(ack_lfsr(13 downto 0)));
                     else
                        cmd_delay <= 34000 - 2 + to_integer(unsigned(ack_lfsr(13 downto 0)));
                     end if;
                  elsif (newCmd = x"09") then   -- Pause: 28.6k-43.9k
                     cmd_delay  <= 28000 - 2 + to_integer(unsigned(ack_lfsr(13 downto 0)));
                  else   -- Nop, Mute, Demute, Setmode: 24k-177k, average 46k-50k; Setloc 34k-182k, average 58k
                     if (ack_lfsr(15 downto 13) = "111") then
                        if (newCmd = x"02") then
                           cmd_delay <= 32000 - 2 + 65536 + to_integer(unsigned(ack_lfsr(14 downto 0)));
                        else
                           cmd_delay <= 24000 - 2 + 65536 + to_integer(unsigned(ack_lfsr(14 downto 0)));
                        end if;
                     elsif (newCmd = x"02") then
                        cmd_delay  <= 32000 - 2 + to_integer(unsigned(ack_lfsr(14 downto 0)));
                     else
                        cmd_delay  <= 24000 - 2 + to_integer(unsigned(ack_lfsr(14 downto 0)));
                     end if;
                  end if;'''),
    ],
    'initspin': [
        # Init's soft reset kept the drive busy 0.5 s (16,934,400 clocks) at 1x,
        # so a ReadN up to half a second after Init waited for it. The console
        # shows no such wait in ps1-tests' order (10 Inits, then about 5M
        # clocks of ACK tests, then a first 1x sector in 668k-984k), while the
        # core read its first 1x sector at 5.6M-10M there (0.5.7-test9,
        # rel057e). The drive now leaves that state as Init completes
        # (476,000, the init part). Init arriving in 2x keeps its extra
        # second (+33,868,800): no console data for that case.
        ('''                  driveDelay     <= 16934400 + 33868800 + 19999;
''', '''                  driveDelay     <= 475999 + 33868800 + 19999;   -- cd-timing-ps1 initspin
'''),
        ('''                  driveDelay     <= 16934400 + 19999;
''', '''                  driveDelay     <= 475999 + 19999;   -- cd-timing-ps1 initspin: busy until Init completes
'''),
    ],
    'init': [
        ('''                           workDelay   <= workDelay + 3999999;
''', '''                           workDelay   <= workDelay + 475999;   -- cd-timing-ps1: PS1 Init 138k-600k, average 476k
'''),
    ],
}

# The LFSR steps every clk1x (x^16 + x^14 + x^13 + x^11 + 1), beside the command process.
LFSR_ANCHOR = '''   -- command processing
   process(clk1x)
'''


def _rstrip(text):
    return '\n'.join(line.rstrip() for line in text.split('\n'))


def timing_text(text, parts=PARTS):
    # Trailing blanks differ between cd_top copies; the edits match without them.
    if 'ack' in parts and 'ackjitter' in parts:
        raise ValueError('cd-timing-ps1: ack and ackjitter are alternatives')
    text = _rstrip(text)
    for part in parts:
        for edit in EDITS[part]:
            old, new = _rstrip(edit[0]), _rstrip(edit[1])
            count = edit[2] if len(edit) > 2 else 1
            if text.count(old) != count:
                raise ValueError(f'cd-timing-ps1 {part}: cd_top.vhd changed near: ' + old.strip().splitlines()[0][:70])
            text = text.replace(old, new)
    if 'ackjitter' in parts:
        lfsr = '''   -- cd-timing-ps1: ACK jitter source
   process (clk1x)
   begin
      if rising_edge(clk1x) then
         ack_lfsr <= ack_lfsr(14 downto 0) & (ack_lfsr(15) xor ack_lfsr(13) xor ack_lfsr(12) xor ack_lfsr(10));
      end if;
   end process;

'''
        if text.count(LFSR_ANCHOR) != 1:
            raise ValueError('cd-timing-ps1 ack: command processing anchor not found')
        text = text.replace(LFSR_ANCHOR, lfsr + LFSR_ANCHOR)
    return text


def prepare_project(target, parts=PARTS):
    target = Path(target).resolve()
    if target.suffix == '.vhd':
        target.write_text(timing_text(target.read_text(), parts)); return target
    qsf = target/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/cd_top\.vhd)"', q)
    if len(hits) != 1: raise ValueError('cd-timing-ps1: expected one cd_top.vhd in the project')
    out = target/'cd-timing-ps1'/'cd_top.vhd'; out.parent.mkdir(exist_ok=True)
    out.write_text('-- Modified by scripts/prepare_cd_timing_ps1.py: see its docstring.\n'
                   + timing_text(Path(hits[0]).read_text(), parts))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', type=Path)
    ap.add_argument('--only', default=','.join(PARTS), help='comma-separated parts: ' + ', '.join(ALL_PARTS))
    a = ap.parse_args()
    parts = tuple(p for p in a.only.split(',') if p)
    bad = [p for p in parts if p not in ALL_PARTS]
    if bad: ap.error('unknown part(s): ' + ', '.join(bad))
    print(prepare_project(a.target, parts))
