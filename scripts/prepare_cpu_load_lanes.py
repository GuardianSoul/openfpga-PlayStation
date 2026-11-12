#!/usr/bin/env python3
"""Factor each selected CPU load formatter into rotation and byte selection."""

import argparse
import json
import re
from pathlib import Path

from measure_pocket_seeds import inputs
from prepare_spu_ring_paired import once
from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot

CPU_SHA = 'a65699418f65efc9ec6ab6b1997b8b51bfd2265e5914cf1b5e993e8bc5e06988'

FUNCTION = '''
   function load_lanes(data, old_data : unsigned(31 downto 0);
                       kind : CPU_LOADTYPE; offset : unsigned(1 downto 0);
                       bus_aligned : boolean) return unsigned is
      variable rotation : unsigned(1 downto 0);
      variable rotated, result : unsigned(31 downto 0);
      variable fill_byte : unsigned(7 downto 0);
   begin
      rotation := offset;
      case kind is
         when LOADTYPE_LEFT => rotation := offset + 1;
         when LOADTYPE_DWORD => rotation := "00";
         when LOADTYPE_SBYTE | LOADTYPE_BYTE =>
            if bus_aligned then rotation := "00"; end if;
         when LOADTYPE_SWORD | LOADTYPE_WORD =>
            rotation(0) := '0';
            if bus_aligned then rotation := "00"; end if;
         when others => null;
      end case;
      rotated := data;
      if rotation(1) = '1' then rotated := rotated(15 downto 0) & rotated(31 downto 16); end if;
      if rotation(0) = '1' then rotated := rotated(7 downto 0) & rotated(31 downto 8); end if;
      fill_byte := (others => '0');
      if kind = LOADTYPE_SBYTE then fill_byte := (others => rotated(7));
      elsif kind = LOADTYPE_SWORD then fill_byte := (others => rotated(15));
      end if;
      for lane in 0 to 3 loop
         result(lane*8+7 downto lane*8) := rotated(lane*8+7 downto lane*8);
         case kind is
            when LOADTYPE_SBYTE | LOADTYPE_BYTE =>
               if lane > 0 then result(lane*8+7 downto lane*8) := fill_byte; end if;
            when LOADTYPE_SWORD | LOADTYPE_WORD =>
               if lane > 1 then result(lane*8+7 downto lane*8) := fill_byte; end if;
            when LOADTYPE_LEFT =>
               if lane < 3-to_integer(offset) then
                  result(lane*8+7 downto lane*8) := old_data(lane*8+7 downto lane*8);
               end if;
            when LOADTYPE_RIGHT =>
               if lane > 3-to_integer(offset) then
                  result(lane*8+7 downto lane*8) := old_data(lane*8+7 downto lane*8);
               end if;
            when others => null;
         end case;
      end loop;
      return result;
   end function;
'''


def blocks(text):
    """Extract the two existing case trees, including nested cases."""
    found = []
    for selector in ('executeLoadType', 'writebackLoadType'):
        start = text.index(f'case ({selector}) is', text.index('variable dataReadData'))
        depth = 0
        for token in re.finditer(r'\bend case;|\bcase\b', text[start:]):
            depth += -1 if token[0] == 'end case;' else 1
            if depth == 0:
                found.append(text[start:start+token.end()])
                break
        else:
            raise ValueError('Unclosed load format case')
    return found


def transform(text):
    original = blocks(text)
    text = once(text, '   type CPU_EXESTALLTYPE is', FUNCTION + '\n   type CPU_EXESTALLTYPE is')
    text = once(text, original[0],
                'writebackData <= load_lanes(spad_cache_dataread, oldData, executeLoadType, executeReadAddress(1 downto 0), false);')
    return once(text, original[1],
                'lateReadData <= load_lanes(dataReadData, lateReadOldData, writebackLoadType, writebackReadAddress(1 downto 0), true);')


def prepare(source, output):
    source, output = source.resolve(), output.resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    selected = [row for row in manifest['quartus_sources'] if Path(row['path']).name == 'cpu.vhd']
    if len(selected) != 1 or selected[0]['sha256'] != CPU_SHA:
        raise ValueError('Expected the selected full-audio CPU')
    manifest = snapshot(source, source / 'fit-inputs.json', output, 2)
    previous = {row['path']: row for row in manifest['quartus_sources']}
    cpu = next(Path(p) for p in previous if Path(p).name == 'cpu.vhd')
    if digest(cpu) != CPU_SHA:
        raise ValueError('CPU changed during relocation')
    cpu.write_text(transform(cpu.read_text()))
    current = inputs(output)
    updated = {row['path']: row for row in current}
    if previous.keys() != updated.keys() or {
        p for p in previous if previous[p] != updated[p]
    } != {str(cpu)}:
        raise ValueError('Expected exactly one CPU source change')
    hold_guard(output, current)
    manifest['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    return cpu


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.source, args.output))
