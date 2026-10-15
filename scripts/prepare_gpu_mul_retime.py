"""Retime gpu_poly's gradient multiply behind its operand registers.

Every write of mulResult32clk is mulResult32clk <= mulResult32 (the product
of the operands selected in that cycle), and the register otherwise holds.
Registering the selected operands at exactly those writes and forming
mulResult32clk combinationally from them gives the same value in every
cycle, but moves the state decode and operand multiplexers in front of a
register instead of in front of the multiplier. The multiply now precedes
the accumulators' adders, which have no other logic in their path. Requires
gpu-mul-narrow. No cycle changes.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned gpu_poly source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def mul_retime_text(text):
    if 'signed(mulIn2(12 downto 0))' not in text: raise ValueError('gpu-mul-retime requires gpu-mul-narrow')
    writes = text.count('mulResult32clk <= mulResult32;')
    if writes < 1 or text.count('mulResult32clk <=') != writes: raise ValueError('Unexpected mulResult32clk writes')
    text = text.replace('mulResult32clk <= mulResult32;', 'mulOp1 <= mulIn1; mulOp2 <= mulIn2(12 downto 0);')
    text = _replace(text, "   signal mulResult32clk      : unsigned(31 downto 0) := (others => '0');\n",
                    "   signal mulResult32clk      : unsigned(31 downto 0);\n"
                    "   signal mulOp1              : unsigned(31 downto 0) := (others => '0');\n"
                    "   signal mulOp2              : unsigned(12 downto 0) := (others => '0');\n")
    return _replace(text, '   mulResult32 <= unsigned(resize(signed(mulIn1) * signed(mulIn2(12 downto 0)), 32));\n',
                    '   mulResult32 <= unsigned(resize(signed(mulIn1) * signed(mulIn2(12 downto 0)), 32));\n'
                    '   -- Product of the registered operands (gpu-mul-retime).\n'
                    '   mulResult32clk <= unsigned(resize(signed(mulOp1) * signed(mulOp2), 32));\n')


def apply_gpu_mul_retime(manifest):
    target = manifest['targets']['psx_top']
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'gpu_poly.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gpu-mul-retime'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'gpu_poly.vhd'; path.write_text(mul_retime_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='Gradient multiply after registered operands')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one gpu_poly assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_mul_retime'] = True; manifest['gpu_mul_retime'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
