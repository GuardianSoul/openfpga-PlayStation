#!/usr/bin/env python3
"""Specialize syntactically one-writer/one-reader DPRAMs in a copied project.

Only syntactically unused read outputs and constant-disabled opposite writers
qualify. Keep the upstream primitive's clocks, enables and register modes.
The source primitive and all input files remain unchanged. No mixed-width,
two-writer, two-reader or expression-based reachability inference is used.
"""
import argparse
import json
from pathlib import Path
import re
import shutil

from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT


def uncomment(text):
    """Blank VHDL comments without changing source offsets or quoted strings."""
    return re.sub(r'"(?:""|[^"\n])*"|--[^\n]*',
                  lambda m: ' '*len(m[0]) if m[0].startswith('--') else m[0], text)


def balanced(text, start):
    depth = 0
    quoted = False
    i = start
    while i < len(text):
        c = text[i]
        if c == '"':
            if quoted and text[i:i+2] == '""':
                i += 2
                continue
            quoted = not quoted
        elif not quoted:
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return text[start+1:i], i+1
        i += 1
    raise ValueError('Unbalanced VHDL association')


def fields(text):
    values = {}
    parts = []
    last = depth = 0
    quoted = False
    i = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            if quoted and text[i:i+2] == '""':
                i += 2
                continue
            quoted = not quoted
        elif not quoted:
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            elif c == ',' and depth == 0:
                parts.append(text[last:i]); last = i+1
        i += 1
    parts.append(text[last:])
    for part in parts:
        m = re.fullmatch(r'\s*(\w+)\s*=>\s*(.+?)\s*', part, re.S)
        if not m or m[1].lower() in values:
            raise ValueError('Expected unique named VHDL associations')
        values[m[1].lower()] = m[2]
    return values


def inventory(text):
    clean = uncomment(text)
    result = []
    for m in re.finditer(r'(\w+)\s*:\s*entity\s+(work|mem)\.(dpram)\b', clean, re.I):
        i = m.end()
        maps = {}
        while True:
            h = re.match(r'\s*(generic|port)\s+map\s*\(', clean[i:], re.I)
            if not h or h[1].lower() in maps:
                raise ValueError('Unsupported DPRAM map')
            body, i = balanced(clean, i+h.end()-1)
            maps[h[1].lower()] = fields(body)
            if h[1].lower() == 'port':
                break
        ports = maps['port']
        required = {'clock_a', 'clock_b', 'address_a', 'address_b', 'data_a'}
        known = required | {'clken_a', 'clken_b', 'data_b', 'wren_a', 'wren_b', 'q_a', 'q_b'}
        if not required <= ports.keys() or ports.keys()-known:
            raise ValueError('Unsupported DPRAM port contract')
        if maps.get('generic', {}).keys()-{'addr_width', 'data_width'}:
            raise ValueError('Unsupported DPRAM generic contract')
        p = {k: v.lower() for k, v in ports.items()}
        # In these named VHDL maps an omitted OUT port is unconnected, just as
        # an explicit OPEN. The selected pinned entity has no output default.
        p.setdefault('q_a', 'open')
        p.setdefault('q_b', 'open')
        orientation = None
        if p['q_a'] == 'open' and p.get('wren_b', "'0'") == "'0'" and p['q_b'] != 'open':
            orientation = 'Awrite-Bread'
        elif p['q_b'] == 'open' and p.get('wren_a', "'0'") == "'0'" and p['q_a'] != 'open':
            orientation = 'Bwrite-Aread'
        result.append(dict(instance=m[1], library=m[2], start=m.start(3), end=m.end(3),
                           orientation=orientation, ports=ports, generics=maps.get('generic', {})))
    return result


def primitive(text, reverse=False):
    first = text[:text.index('END SYN;')+len('END SYN;')]+'\n'
    name = 'pocket_dpram_sdp_reverse' if reverse else 'pocket_dpram_sdp'
    result = re.sub(r'\bdpram\b', name, first, flags=re.I)
    anchor = 'operation_mode => "BIDIR_DUAL_PORT"'
    if result.count(anchor) != 1:
        raise ValueError('Unsupported pinned DPRAM primitive')
    result = result.replace(anchor, 'operation_mode => "DUAL_PORT",\n        ram_block_type => "M10K"')
    if reverse:
        before, sep, ports = result.partition('\n\tPORT MAP (')
        if not sep:
            raise ValueError('Unsupported pinned primitive port map')
        swap = {f'{n}_{a}': f'{n}_{b}' for n in ('address', 'clock', 'clken', 'data', 'wren', 'q')
                for a, b in (('a', 'b'), ('b', 'a'))}
        ports = re.sub(r'(=>\s*)(\w+)', lambda m: m[1]+swap.get(m[2], m[2]), ports)
        result = before+sep+ports
    return result


def prepare_project(directory):
    directory = Path(directory).resolve()
    qsf = directory/'apf_top.qsf'
    q = qsf.read_text()
    work = directory/'dpram-sdp'
    if work.exists():
        raise ValueError('DPRAM port specialization already prepared')
    paths = list(dict.fromkeys(p for kind, p in assignments(directory, q) if kind == 'VHDL_FILE'))
    selected = []
    for path in paths:
        text = path.read_text()
        eligible = [r for r in inventory(text) if r['orientation']]
        if eligible:
            if q.count('"'+str(path)+'"') < 1:
                raise ValueError('Expected quoted absolute DPRAM consumer assignment')
            selected.append((path, text, digest(path), eligible))
    if not selected:
        raise ValueError('No syntactically eligible one-writer/one-reader DPRAMs')
    base = ROOT/'references/PSX_MiSTer/rtl/dpram.vhd'
    helpers = [(name, primitive(base.read_text(), reverse)) for name, reverse in
               (('pocket_dpram_sdp', False), ('pocket_dpram_sdp_reverse', True))]
    work.mkdir()
    shutil.copyfile(ROOT/'references/PSX_MiSTer/LICENSE', work/'COPYING.GPL2')
    changes = []
    for index, (path, text, sha, rows) in enumerate(selected):
        output = work/'sources'/str(index)/path.name
        output.parent.mkdir(parents=True)
        for row in sorted(rows, key=lambda r: r['start'], reverse=True):
            name = 'pocket_dpram_sdp_reverse' if row['orientation'] == 'Bwrite-Aread' else 'pocket_dpram_sdp'
            text = text[:row['start']]+name+text[row['end']:]
        output.write_text(text)
        q = q.replace('"'+str(path)+'"', '"'+str(output)+'"')
        changes.append(dict(original=str(path), original_sha256=sha, path=str(output),
                            sha256=digest(output), instances=rows))
    for name, text in helpers:
        path = work/(name+'.vhd'); path.write_text(text)
        for library in ('work', 'mem'):
            q += f'set_global_assignment -name VHDL_FILE "{path}" -library {library}\n'
    if any(digest(p) != sha for p, _, sha, _ in selected):
        raise ValueError('DPRAM consumer changed while preparing')
    qsf.write_text(q)
    report = dict(changes=changes, specialized_instances=sum(len(c['instances']) for c in changes),
                  pinned_primitive_sha256=digest(base), preparer_sha256=digest(Path(__file__)),
                  clocks_enables_register_modes_unchanged=True,
                  fit_verified=False, timing_verified=False, hardware_verified=False)
    (work/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project', type=Path)
    print(json.dumps(prepare_project(p.parse_args().project), indent=2))
