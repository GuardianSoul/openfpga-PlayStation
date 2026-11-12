"""Replace only the four cue firmware ROM lanes in an already fitted copy.

Pure preparation here; run_frozen_pocket_fit invokes cdb/asm only in Jenkins.
The original emitted MIFs must first match every byte of the fitted .hex.
"""
import hashlib
from pathlib import Path
import re


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lane_bytes(path):
    rows = Path(path).read_text().splitlines()
    if len(rows) != 4096 or any(not re.fullmatch(r'[0-9a-fA-F]{2}', r) for r in rows):
        raise ValueError('Cue firmware must be exactly 4096 bytes per lane')
    return bytes(int(r, 16) for r in rows)


def checked_mif(text, expected):
    if not all(re.search(p, text) for p in (r'\bWIDTH\s*=\s*8;', r'\bDEPTH\s*=\s*4096;',
                                            r'\bADDRESS_RADIX=UNS;', r'\bDATA_RADIX=BIN;')):
        raise ValueError('Cue MIF geometry/radix changed')
    header, body = text.split('CONTENT BEGIN', 1)
    body, tail = body.split('END;', 1)
    if tail.strip():
        raise ValueError('Unexpected cue MIF trailer')
    values = {}
    for line in body.splitlines():
        if not line.strip(): continue
        m = re.fullmatch(r'\s*(\d+)\s*:\s*([01]{8});\s*', line)
        if not m or int(m[1]) in values or not 0 <= int(m[1]) < 4096:
            raise ValueError('Cue MIF row duplicate/range/format')
        values[int(m[1])] = int(m[2], 2)
    if len(values) != 4096 or bytes(values[i] for i in range(4096)) != expected:
        raise ValueError('Fitted cue MIF differs from fitted firmware')
    return header


def stage(project, firmware, request):
    """Validate every old/new lane before writing anything; return a bit-exact ledger."""
    project, firmware = Path(project), Path(firmware)
    names = {f'psx_cue_fw{i}.hex' for i in range(4)}
    if set(request) != names:
        raise ValueError('All four cue ROM lanes are required')
    plans, ledger = [], []
    candidates = list((project / 'db').glob('*.hdl.mif'))
    for name in sorted(names):
        old, new = project / name, firmware / name
        if set(request[name]) != {'before_sha256', 'after_sha256'} or \
                sha(old) != request[name]['before_sha256'] or sha(new) != request[name]['after_sha256']:
            raise ValueError('Cue firmware source hash mismatch')
        before, after = lane_bytes(old), lane_bytes(new)
        signature = f'-- psx_cue_ram_lane(FIRMWARE\n-- "{name}")'
        matches = [p for p in candidates if signature in p.read_text()]
        if len(matches) != 1:
            raise ValueError('Expected exactly one fitted cue MIF per lane')
        mif = matches[0]
        header = checked_mif(mif.read_text(), before)
        text = header + 'CONTENT BEGIN\n' + ''.join(f'  {i} : {v:08b};\n' for i, v in enumerate(after)) + 'END;\n'
        checked_mif(text, after)
        ledger.append(dict(lane=name, before_sha256=sha(old), after_sha256=sha(new),
                           mif=mif.name, mif_before_sha256=sha(mif),
                           mif_after_sha256=hashlib.sha256(text.encode()).hexdigest(), bytes=4096))
        plans.append((old, new.read_bytes(), mif, text))
    for old, data, mif, text in plans:
        old.write_bytes(data)
        mif.write_text(text)
    for row in ledger:
        if sha(project / row['lane']) != row['after_sha256'] or \
                sha(project / 'db' / row['mif']) != row['mif_after_sha256']:
            raise ValueError('Cue ROM update verification failed')
    return ledger
