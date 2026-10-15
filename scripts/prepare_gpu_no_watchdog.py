"""Remove MiSTer's rectangle watchdog.

gpu_rect (like gpu_poly) aborts a primitive and raise error after 1,048,575 2x
clocks (about 15 ms). The PlayStation GPU has no such timeout; the abort only
fires when a primitive hangs or legitimately runs that long, and in the
second case it truncates the drawing. Without it the 20-bit counters and
compares have no readers and synthesis removes them. Normal drawing is
unchanged cycle for cycle. The same edit in gpu_poly raised its synthesized
size (2,371 -> 2,551 ALUTs in a full map), so it is applied to gpu_rect only.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest

OLD = 'elsif (timeout < 1048575) then'


def no_watchdog_text(text):
    if text.count(OLD) != 1: raise ValueError('Pinned GPU watchdog changed')
    return text.replace(OLD, 'elsif (true) then -- watchdog removed (gpu-no-watchdog)')


def apply_gpu_no_watchdog(manifest):
    target = manifest['targets']['psx_top']
    directory = Path(target['directory']); out = directory / 'gpu-no-watchdog'; out.mkdir(parents=True, exist_ok=True)
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    for name in ('gpu_rect.vhd',):
        index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == name)
        previous = Path(target['quartus_sources'][index]['path'])
        path = out / name; path.write_text(no_watchdog_text(previous.read_text()))
        target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
            original=str(previous), original_sha256=digest(previous), transform='Primitive watchdog removed')
        if q.count(str(previous)) != 1: raise ValueError('Expected one assignment: ' + name)
        q = q.replace(str(previous), str(path))
    qsf.write_text(q); target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_no_watchdog'] = True; manifest['gpu_no_watchdog'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
