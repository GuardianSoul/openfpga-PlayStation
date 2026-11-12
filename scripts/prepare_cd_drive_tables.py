#!/usr/bin/env python3
"""Move the selected CD seek and rotation arithmetic to clocked ROM reads."""
import argparse
import json
from pathlib import Path

from measure_pocket_seeds import inputs
from prepare_spu_ring_paired import once
from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot

CD_SHA = 'a8393192d78669f2ec2763611fa52a33160988c48b8170bf656eceb9919e0fdc'
SPU_SHA = '77170eda3749b24b9ec5a28afd9183c854bb5905bc1e2a2a0f748a4708d6803b'
DECLARATIONS = '''
   signal drive_speed_index : natural range 0 to 4;
   type t_drive_speeds is array (0 to 4) of natural;
   constant DRIVE_SPEEDS : t_drive_speeds :=
      (READSPEED1X, READSPEED2X, READSPEED4X, READSPEED6X, READSPEED8X);

   type t_rotation_rom is array (0 to 127) of natural range 0 to 16777215;
   function make_rotation_rom return t_rotation_rom is
      variable result : t_rotation_rom := (others => 0);
   begin
      for speed in DRIVE_SPEEDS'range loop
         for sectors in 8 to 22 loop
            result(speed * 16 + sectors - 8) := DRIVE_SPEEDS(speed) * sectors;
         end loop;
      end loop;
      return result;
   end function;
   constant rotation_rom : t_rotation_rom := make_rotation_rom;

   -- The normal distance term is a multiple of 64 clocks. Store its
   -- quotient and append those six zero bits to the registered read.
   type t_seek_rom is array (0 to 540) of natural range 0 to 262143;
   function make_seek_rom return t_seek_rom is
      variable result : t_seek_rom;
   begin
      for unit in result'range loop
         result(unit) := unit * 441;
      end loop;
      return result;
   end function;
   constant seek_rom : t_seek_rom := make_seek_rom;
   attribute romstyle : string;
   attribute romstyle of rotation_rom : constant is "M10K";
   attribute romstyle of seek_rom : constant is "M10K";
'''


def transform(text):
    anchor = '   signal pause_rotation_ticks     : integer range 0 to 16777215 := 0; -- one disc rotation in ticks\n'
    text = once(text, anchor, anchor + DECLARATIONS)
    start = text.index('   process (FORCECDSPEED, allow_speedhack, modeReg)')
    end = text.index('   -- drive\n', start)
    block = text[start:end]
    for index, speed in enumerate((1, 2, 4, 6, 8)):
        block = block.replace(f'driveREADSPEED <= READSPEED{speed}X;', f'drive_speed_index <= {index};')
    text = text[:start] + block + '   driveREADSPEED <= DRIVE_SPEEDS(drive_speed_index);\n\n' + text[end:]
    text = once(text, '         pause_rotation_ticks <= getSectorsPerTrack(currentLBA) * driveREADSPEED;',
                '         pause_rotation_ticks <= rotation_rom(drive_speed_index * 16 + getSectorsPerTrack(currentLBA) - 8);')
    anchor = '      variable physicalLBANew  : integer range 0 to 524287;'
    text = once(text, anchor, anchor + '''
      variable seek_target     : integer range 0 to 524287;
      variable seek_delta      : signed(19 downto 0);
      variable seek_units      : natural range 0 to 540;''')
    old = '''               if (seekOnDiskPlay = '1') then
                  readLBA <= playLBA;
                  if (playLBA > currentLBA) then diffLBA <= playLBA - currentLBA; else diffLBA <= currentLBA - playLBA; end if;
               else
                  readLBA <= seekLBA;
                  if (seekLBA > currentLBA) then diffLBA <= seekLBA - currentLBA; else diffLBA <= currentLBA - seekLBA; end if;
               end if;'''
    text = once(text, old, '''               if seekOnDiskPlay = '1' then
                  seek_target := playLBA;
               else
                  seek_target := seekLBA;
               end if;
               readLBA <= seek_target;
               seek_delta := to_signed(seek_target, 20) - to_signed(currentLBA, 20);
               diffLBA <= to_integer(abs(seek_delta));''')
    start = text.index("               if (diffLBA < 4500 and modeReg(7) = '0') then")
    end = text.index("\n            end if;\n\n            if (addSeekTime", start)
    text = text[:start] + '''               if diffLBA >= 250000 and currentLBA = 0 then
                  seekCycles <= 137 * 451584;
               else
                  if diffLBA < 4500 then
                     seek_units := diffLBA / 256;
                     if modeReg(7) /= '0' then seek_units := seek_units + 12; end if;
                  else
                     seek_units := 29 + diffLBA / 1024;
                  end if;
                  seekCycles <= seek_rom(seek_units) * 64;
               end if;
''' + text[end:]
    return text


def prepare(source, output, rewrite=transform):
    source, output = source.resolve(), output.resolve()
    original = load_source(source, source / 'fit-inputs.json')
    selected = {Path(row['path']).name: row['sha256'] for row in original['quartus_sources']}
    if selected['spu.vhd'] != SPU_SHA or selected['cd_top.vhd'] != CD_SHA:
        raise ValueError('Requires the reviewed full-audio SPU and selected CD controller')
    manifest = snapshot(source, source / 'fit-inputs.json', output, 2)
    cd = next(Path(row['path']) for row in manifest['quartus_sources'] if Path(row['path']).name == 'cd_top.vhd')
    if digest(cd) != CD_SHA:
        raise ValueError('CD source changed after path relocation')
    cd.write_text(rewrite(cd.read_text()))
    current = inputs(output)
    previous = {row['path']: row for row in manifest['quartus_sources']}
    if len(current) != len(previous) or {row['path'] for row in current if previous.get(row['path']) != row} != {str(cd)}:
        raise ValueError('Expected a single changed CD source')
    hold_guard(output, current)
    manifest['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    return cd


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.source, args.output))
