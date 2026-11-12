"""Replace only the save-state handler ROM in an already fitted copy.

psx_save_exp1's one 1024 x 40 ROM (save-states/psx_save_rom40.hex, both
handler ROMs: build_save_fw.rom40_hex) and, in a fit that has it
(prepare_save_rom_ext.py), the 1024 x 8 extension ROM of psx_save_rom_ext
(save-states/psx_save_rom_ext.hex). Pure preparation here, as in
update_cue_fit.py; run_frozen_pocket_fit invokes cdb/asm only in Jenkins.
Each fitted MIF must first match every word of its fitted .hex.
"""
import hashlib
from pathlib import Path
import re

LANE = 'save-states/psx_save_rom40.hex'
EXT_LANE = 'save-states/psx_save_rom_ext.hex'
EXT_WORDS = 1024


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rom_words(path, width=40, depth=1024):
    rows = Path(path).read_text().splitlines()
    if len(rows) != depth or any(not re.fullmatch(r'[0-9a-fA-F]{%d}' % (width // 4), r) for r in rows):
        raise ValueError(f'Save ROM must be exactly {depth} words of {width} bits')
    return [int(r, 16) for r in rows]


def checked_mif(text, expected, width=40):
    depth = len(expected)
    if not all(re.search(p, text) for p in (r'\bWIDTH\s*=\s*%d;' % width, r'\bDEPTH\s*=\s*%d;' % depth,
                                            r'\bADDRESS_RADIX=UNS;', r'\bDATA_RADIX=BIN;')):
        raise ValueError('Save ROM MIF geometry/radix changed')
    header, body = text.split('CONTENT BEGIN', 1)
    body, tail = body.split('END;', 1)
    if tail.strip():
        raise ValueError('Unexpected save ROM MIF trailer')
    values = {}
    for line in body.splitlines():
        if not line.strip(): continue
        m = re.fullmatch(r'\s*(\d+)\s*:\s*([01]{%d});\s*' % width, line)
        if not m or int(m[1]) in values or not 0 <= int(m[1]) < depth:
            raise ValueError('Save ROM MIF row duplicate/range/format')
        values[int(m[1])] = int(m[2], 2)
    if len(values) != depth or [values[i] for i in range(depth)] != expected:
        raise ValueError('Fitted save ROM MIF differs from fitted firmware')
    return header


def _plan(project, lane, new, request, signature, width, what):
    """One ROM: check the request, the fitted .hex and its one fitted MIF; return (ledger row, writes)."""
    old = project / lane
    if set(request) != {'before_sha256', 'after_sha256'} or \
            sha(old) != request['before_sha256'] or sha(new) != request['after_sha256']:
        raise ValueError(f'Save firmware source hash mismatch ({what})' if what else 'Save firmware source hash mismatch')
    before, after = rom_words(old, width), rom_words(new, width)
    matches = [p for p in (project / 'db').glob('*.hdl.mif') if re.search(signature, p.read_text())]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one fitted save ROM MIF ({what})' if what else 'Expected exactly one fitted save ROM MIF')
    mif = matches[0]
    header = checked_mif(mif.read_text(), before, width)
    text = header + 'CONTENT BEGIN\n' + ''.join(f'\t{i} :\t{v:0{width}b};\n' for i, v in enumerate(after)) + 'END;\n'
    checked_mif(text, after, width)
    row = dict(lane=lane, before_sha256=sha(old), after_sha256=sha(new), mif=mif.name,
               mif_before_sha256=sha(mif), mif_after_sha256=hashlib.sha256(text.encode()).hexdigest(),
               words=len(after))
    return row, [(old, Path(new).read_bytes()), (mif, text.encode())]


def stage(project, new_rom, request, new_ext=None):
    """Validate the old and new ROM before writing anything; return a bit-exact ledger.
    request = {before_sha256, after_sha256} of the fitted and the new psx_save_rom40.hex.
    A fit with the extension ROM (it has save-states/psx_save_rom_ext.hex) is updated as a whole:
    new_ext is the new psx_save_rom_ext.hex and request['ext'] = {before_sha256, after_sha256} of
    the fitted and the new one (the same file again when the extension does not change). Both
    ROMs are checked before either is written; the ledger then has two rows."""
    project = Path(project)
    request = dict(request)
    ext_request = request.pop('ext', None)
    fitted_ext = (project / EXT_LANE).is_file()
    if fitted_ext != (ext_request is not None) or fitted_ext != (new_ext is not None):
        raise ValueError('This fit has the extension ROM: the update names both ROMs' if fitted_ext else
                         'This fit has no extension ROM')
    plans = [_plan(project, LANE, new_rom, request,
                   r'-- psx_save_exp1\(ROM_HEX\n-- "[^"\n]*/' + re.escape(LANE) + '"\n', 40, 'routine ROM' if fitted_ext else '')]
    if fitted_ext:
        plans.append(_plan(project, EXT_LANE, new_ext, dict(ext_request),
                           r'-- psx_save_rom_ext\(HEX\n-- "[^"\n]*/' + re.escape(EXT_LANE) + r'"\)\n', 8, 'extension ROM'))
    for _, writes in plans:
        for path, data in writes:
            path.write_bytes(data)
    ledger = [row for row, _ in plans]
    for row in ledger:
        if sha(project / row['lane']) != row['after_sha256'] or sha(project / 'db' / row['mif']) != row['mif_after_sha256']:
            raise ValueError('Save ROM update verification failed')
    return ledger
