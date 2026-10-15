"""Keep numeric_std signed shift semantics in GHDL's generated Verilog."""
import hashlib
import json
from pathlib import Path
import re

SIGNED_LOGICAL_SHIFT = re.compile(r'\$signed\([^()\n]+\)\s+>>(?!>)')


def normalize_signed_shifts(text):
    # GHDL 4 emits '$signed(value) >> amount' for signed shift_right. Verilog's
    # >> still fills with zero; >>> is required for numeric_std sign extension.
    pattern = r'(?m)^(\s*assign\s+\w+\s*=\s*\$signed\([^()\n]+\)\s+)>>(?!>)'
    text, count = re.subn(pattern, r'\1>>>', text)
    if SIGNED_LOGICAL_SHIFT.search(text):
        raise ValueError('Unsupported GHDL signed logical-shift expression; cannot preserve its semantics')
    return text, count


def normalize_generated_verilog(path):
    path = Path(path)
    original = path.read_text()
    normalized, count = normalize_signed_shifts(original)
    raw = path.with_suffix('.raw.v')
    raw.write_text(original)
    if count:
        path.write_text(normalized)
    report = {'signed_shift_operator_fixes': count, 'raw_path': str(raw),
              'raw_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
              'normalized_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'production_vhdl_modified': False}
    path.with_suffix('.verilog-normalization.json').write_text(json.dumps(report, indent=2) + '\n')
    return report
