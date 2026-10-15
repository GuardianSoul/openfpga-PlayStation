"""Time GTE engine-internal paths over two 2x clocks.

The adapter enables the engine only on clk2xIndex = 1, which psx_top toggles
on every 2x edge, so an engine register launched at one enabled edge is next
captured at least two 2x periods later. The only exception is reset, which
enabled every edge; this variant also gates reset by clk2xIndex so the
two-period relation holds unconditionally (reset lasts many cycles). The SDC
then gives engine-to-engine paths a two-cycle setup budget. Paths into the
engine from the CPU interface and the adapter's registered readback stay
single-cycle. The original RTPS/RTPT divider-to-multiplier path misses one
2x period by 5.6 ns on the fitted reset-only top.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest

ORIGINAL = 'wire engine_enable = reset || (ce && clk2xIndex);'
STRICT = 'wire engine_enable = clk2xIndex && (reset || ce);'
ENGINE = '*|gte:igte|psx_laxer_mister_adapter:engine|PocketGTEEngine:engine|*'
SDC = [
    '# GTE engine registers only change on alternate 2x edges (gte-multicycle).',
    f'set_multicycle_path -setup 2 -from [get_registers {{{ENGINE}}}] -to [get_registers {{{ENGINE}}}]',
    f'set_multicycle_path -hold 1 -from [get_registers {{{ENGINE}}}] -to [get_registers {{{ENGINE}}}]',
]


def adapter_text(text):
    if 'loading_savestate' in text: raise ValueError('GTE multicycle does not cover save-state loading writes')
    if text.count(ORIGINAL) != 1: raise ValueError('GTE adapter enable changed')
    return text.replace(ORIGINAL, STRICT)


def apply_gte_multicycle(manifest):
    target = manifest['targets']['psx_top']
    if 'gte_adapter' not in target: raise ValueError('GTE multicycle requires the GTE adapter')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'laxer_mister_adapter.sv')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gte-multicycle'; out.mkdir(parents=True, exist_ok=True)
    path = out / previous.name; path.write_text(adapter_text(previous.read_text()))
    copy = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
                original=str(previous), original_sha256=digest(previous),
                transform='Engine enable only on clk2xIndex, including reset; engine paths get two 2x periods')
    target['quartus_sources'][index] = copy
    qsf = directory / (target['top'] + '.qsf'); text = qsf.read_text()
    if text.count(str(previous)) != 1: raise ValueError('Expected one GTE adapter assignment')
    qsf.write_text(text.replace(str(previous), str(path)))
    target['project_sha256']['.qsf'] = digest(qsf)
    target['gte_multicycle'] = copy; manifest['gte_multicycle'] = copy
    manifest['sdc_extra'] = manifest.get('sdc_extra', []) + SDC
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
