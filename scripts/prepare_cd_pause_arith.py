#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""cd_top: the Pause rotation target with one signed difference and one clamp.

Upstream (cd17b5a) computes the Pause workDelay as
    E = driveDelayNext - driveDelay if driveDelayNext > driveDelay else 0
    t = T - E if T > E else 0            (T: rotation target, 27 bits)
    workDelay = max(t, 1066874 if modeReg(7) else 2157295)
which is two compares, two subtractions and two constant clamps on the
command clock. With d = driveDelay - driveDelayNext (signed):
    T > E  <=>  T + min(d, 0) > 0, and then t = T + min(d, 0);
    otherwise t = 0, and T + min(d, 0) <= 0 is below either minimum;
so workDelay = max(T + min(d, 0), minimum) is the same value for every
input (T >= 0 as the 27-bit value it already is). T keeps its original
statement, wrap included. Same clock, same inputs, no new registers
(run_cd_lockstep.py).
Usage: prepare_cd_pause_arith.py PROJECT_OR_cd_top.vhd
"""
import sys
from pathlib import Path

OLD_VARS = '''      variable pause_elapsed_v  : integer range 0 to 134217727;
      variable pause_target_v   : integer range 0 to 134217727;
'''
NEW_VARS = '''      variable pause_target_v   : integer range 0 to 134217727;
      variable pause_dn_v       : signed(28 downto 0);
      variable pause_x_v        : signed(28 downto 0);
      variable pause_min_v      : integer range 0 to 2157295;
'''

OLD = '''                           if (driveState = DRIVE_READING or driveState = DRIVE_PLAYING) then
                              -- ticks already elapsed in the current sector
                              if (driveDelayNext > driveDelay) then
                                 pause_elapsed_v := driveDelayNext - driveDelay;
                              else
                                 pause_elapsed_v := 0;
                              end if;

                              -- data mode: 2 sectors less, holding is based on subq
                              if (driveState = DRIVE_READING) then
                                 pause_target_v := pause_rotation_ticks - (2 * driveREADSPEED);
                              else
                                 pause_target_v := pause_rotation_ticks;
                              end if;

                              if (pause_target_v > pause_elapsed_v) then
                                 pause_target_v := pause_target_v - pause_elapsed_v;
                              else
                                 pause_target_v := 0;
                              end if;

                              -- minimum Pause time, values from psx spx doc.
                              if (modeReg(7) = '1') then
                                 if (pause_target_v < 1066874) then
                                    workDelay <= 1066874;
                                 else
                                    workDelay <= pause_target_v;
                                 end if;
                              else
                                 if (pause_target_v < 2157295) then
                                    workDelay <= 2157295;
                                 else
                                    workDelay <= pause_target_v;
                                 end if;
                              end if;
                           end if;
'''
NEW = '''                           if (driveState = DRIVE_READING or driveState = DRIVE_PLAYING) then
                              -- data mode: 2 sectors less, holding is based on subq
                              if (driveState = DRIVE_READING) then
                                 pause_target_v := pause_rotation_ticks - (2 * driveREADSPEED);
                              else
                                 pause_target_v := pause_rotation_ticks;
                              end if;

                              -- prepare_cd_pause_arith.py: less the ticks already elapsed in the current
                              -- sector (driveDelayNext - driveDelay when positive) as one signed difference;
                              -- a result <= 0 is below either minimum, so one clamp gives upstream's value.
                              pause_dn_v := to_signed(driveDelay, 29) - to_signed(driveDelayNext, 29);
                              pause_x_v  := to_signed(pause_target_v, 29);
                              if (pause_dn_v(28) = '1') then
                                 pause_x_v := pause_x_v + pause_dn_v;
                              end if;

                              -- minimum Pause time, values from psx spx doc.
                              if (modeReg(7) = '1') then
                                 pause_min_v := 1066874;
                              else
                                 pause_min_v := 2157295;
                              end if;
                              if (pause_x_v < pause_min_v) then
                                 workDelay <= pause_min_v;
                              else
                                 workDelay <= to_integer(pause_x_v);
                              end if;
                           end if;
'''


def patch(text):
    for old, new in ((OLD_VARS, NEW_VARS), (OLD, NEW)):
        if text.count(old) != 1:
            raise SystemExit(f'cd_top.vhd: Pause anchor not unique: {old.strip().splitlines()[0][:70]}')
        text = text.replace(old, new)
    if 'pause_elapsed_v' in text:
        raise SystemExit('cd_top.vhd: pause_elapsed_v still used')
    return text


def target(path):
    path = Path(path)
    if path.is_file():
        return path
    hits = {line.split('"')[1] for line in (path / 'apf_top.qsf').read_text().splitlines() if '/cd_top.vhd"' in line}
    if len(hits) != 1:
        raise SystemExit(f'{path}: expected one assigned cd_top.vhd, found {sorted(hits)}')
    return Path(hits.pop())


if __name__ == '__main__':
    p = target(sys.argv[1])
    p.write_text(patch(p.read_text()))
    print(p)
