"""Encode gpu_poly's state machine one-hot (synthesis attribute only).

The area-min-state profile gives every state machine minimal-bit encoding.
In gpu_poly the multiplier operand selection decodes state and step counters
in front of the 32 x 13 gradient multiply, the last failing 2x path. One-hot
state bits shorten that decode. No RTL behavior changes.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def onehot_text(text):
    old = '   signal state     : tState := IDLE;\n'
    if text.count(old) != 1: raise ValueError('Pinned gpu_poly state declaration changed')
    return text.replace(old, '   attribute syn_encoding : string;\n   attribute syn_encoding of tState : type is "one-hot";\n' + old)


def apply_gpu_poly_onehot(manifest):
    target = manifest['targets']['psx_top']
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'gpu_poly.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gpu-poly-onehot'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'gpu_poly.vhd'; path.write_text(onehot_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='One-hot state encoding attribute')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one gpu_poly assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_poly_onehot'] = True; manifest['gpu_poly_onehot'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
