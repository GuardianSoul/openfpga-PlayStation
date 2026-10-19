#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""MEASUREMENT ONLY, never a build: replace one region of cd_top.vhd (from the
line containing START up to the first later line containing END, exclusive, inside one
process) with assignments that load every signal the region drove from a
kept shift register fed by bus_dataWrite, so nothing downstream is pruned.
Usage: ablate_cd_region_kept.py --label NAME --start TEXT --end TEXT PROJECT
"""
import argparse
import math
import re
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('--label', required=True)
ap.add_argument('--start', required=True)
ap.add_argument('--end', required=True)
ap.add_argument('project', type=Path)
args = ap.parse_args()
hits = {line.split('"')[1] for line in (args.project / 'apf_top.qsf').read_text().splitlines() if '/cd_top.vhd"' in line}
assert len(hits) == 1, hits
path = Path(hits.pop())
text = path.read_text()
enums = {m[1]: len([x for x in m[2].split(',') if x.strip()])
         for m in re.finditer(r'type\s+(\w+)\s+is\s*\(([^;]*?)\)\s*;', text, re.S)}
arrays = {m[1]: (int(m[2]), int(m[3]), int(m[4]) - int(m[5]) + 1)
          for m in re.finditer(r'type\s+(\w+)\s+is\s+array\s*\(\s*(\d+)\s+to\s+(\d+)\s*\)\s+of\s+std_logic_vector\s*\(\s*(\d+)\s+downto\s+(\d+)\s*\)', text)}
decl = {}
for m in re.finditer(r'^\s*signal\s+([\w, ]+?)\s*:\s*([^;:=]+)', text, re.M):
    for name in m[1].split(','):
        decl[name.strip()] = m[2].strip()
assert text.count(args.start) == 1, text.count(args.start)
start = text.rindex('\n', 0, text.index(args.start)) + 1
end = text.rindex('\n', 0, text.index(args.end, start)) + 1   # first END after START
assert start < end
region = text[start:end]
driven = []
for m in re.finditer(r'(?:^|\bthen\b|;)\s*([A-Za-z_]\w*)\s*(\([^;]*?\))?\s*<=', region, re.M):
    if m[1] in decl and m[1] not in driven:
        driven.append(m[1])
bit = 0
lines = []


def take(width):
    global bit
    lo = bit
    bit += width
    return f'abl_sr({lo + width - 1} downto {lo})'


for name in driven:
    ty = decl[name]
    vec = re.match(r'(std_logic_vector|unsigned|signed)\s*\((\d+)\s+downto\s+(\d+)\)', ty)
    rng = re.match(r'integer range\s+(-?\d+)\s+to\s+(\d+)', ty)
    if ty == 'std_logic':
        lines.append(f'            {name} <= abl_sr({bit});'); bit += 1
    elif vec:
        lines.append(f'            {name} <= {vec[1]}({take(int(vec[2]) - int(vec[3]) + 1)});')
    elif rng:
        lo, hi = int(rng[1]), int(rng[2])
        if lo < 0:
            lines.append(f'            {name} <= to_integer(signed({take(int(math.log2(hi + 1)) + 1)}));')
        else:
            lines.append(f'            {name} <= {lo} + to_integer(unsigned({take(max(1, int(math.log2(hi - lo + 1))))}));')
    elif ty in enums:
        lines.append(f"            {name} <= {ty}'val(to_integer(unsigned({take(max(1, int(math.log2(enums[ty]))))})));")
    elif ty in arrays:
        a, b, w = arrays[ty]
        for i in range(a, b + 1):
            lines.append(f'            {name}({i}) <= {take(w)};')
    else:
        raise SystemExit(f'unsupported type for {name}: {ty}')
text = text[:start] + f'            -- ablate_cd_region_kept.py {args.label}: {len(driven)} signals\n' + '\n'.join(lines) + '\n' + text[end:]
arch = re.search(r'\nbegin\s*\n', text[text.index('architecture'):]).start() + text.index('architecture')
text = (text[:arch] + f'\n   signal abl_sr : std_logic_vector({bit - 1} downto 0) := (others => \'0\');\n'
        '   attribute keep : boolean;\n   attribute keep of abl_sr : signal is true;\n' + text[arch:])
end_arch = text.rindex('end architecture')
text = (text[:end_arch] + f'   process(clk1x) begin if rising_edge(clk1x) then\n'
        f'      abl_sr <= abl_sr({bit - 2} downto 0) & (bus_dataWrite(0) xor bus_dataWrite(7) xor abl_sr({bit - 1}));\n'
        '   end if; end process;\n' + text[end_arch:])
path.write_text(text)
print(f'{path}: {args.label}: {len(driven)} signals {driven}, {bit} bits')
