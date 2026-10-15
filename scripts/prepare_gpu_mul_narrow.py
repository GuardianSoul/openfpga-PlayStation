"""Multiply polygon gradients by a 13-bit coordinate instead of 32 bits.

gpu_poly's mul32u keeps the low 32 bits of mulIn1 * mulIn2. Every mulIn2
source is a sign-extended 13-bit value: yCoord (-2048..2047), the clamped
non-negative xStart(43:32) (0..2047) and coreNegativeX/Y (signed 13). The low
32 bits of a product depend only on the operands modulo 2^32, so
signed(mulIn1) * mulIn2(12:0) truncated to 32 bits is identical while needing
a narrower multiplier and a 13-bit operand multiplexer. On the fitted top the
32 x 32 path into mulResult32clk misses by 2.9 ns. No cycle changes.
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


def mul_narrow_text(text):
    old_instance = text[text.index('   imul32u : entity work.mul32u'):]
    old_instance = old_instance[:old_instance.index(');') + 2]
    for line in ('mul1     => mulIn1,', 'mul2     => mulIn2,', 'result   => mulResult32'):
        if line not in old_instance: raise ValueError('Pinned gpu_poly multiplier changed')
    text = _replace(text, old_instance, '''   -- mulIn2 is always a sign-extended 13-bit value (gpu-mul-narrow).
   mulResult32 <= unsigned(resize(signed(mulIn1) * signed(mulIn2(12 downto 0)), 32));
   -- synthesis translate_off
   process (mulIn2)
   begin
      assert mulIn2(31 downto 12) = (31 downto 12 => mulIn2(12)) or Is_X(std_logic_vector(mulIn2))
         report "gpu-mul-narrow: multiplier operand exceeds 13 bits" severity failure;
   end process;
   -- synthesis translate_on''')
    return text


def apply_gpu_mul_narrow(manifest):
    target = manifest['targets']['psx_top']
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'gpu_poly.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gpu-mul-narrow'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'gpu_poly.vhd'; path.write_text(mul_narrow_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='Gradient multiply by the 13-bit coordinate operand')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one gpu_poly assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_mul_narrow'] = True; manifest['gpu_mul_narrow'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
