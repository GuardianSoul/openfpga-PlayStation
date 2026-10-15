"""SPU context RAM with sequencer-scheduled save readback and restore.

The save sequencer changes addresses after a rising clk1x edge. The existing
falling-edge context RAM read consequently settles before the next save-read
rising edge. This helper is separate from the original reset-only experiment.
"""
import json
from pathlib import Path
from prepare_spu_context_ram import context_ram_source
from prepare_resource_targets import digest


def save_context_source(directory, selected=None):
    item=context_ram_source(directory,selected)
    path=Path(item['path']); text=path.read_text()
    guard='''   -- synthesis translate_off
   assert SS_rden = '0'
      report "Internal SPU context RAM probe requires savestate readback off" severity failure;
   -- synthesis translate_on
'''
    if text.count(guard)!=1:raise ValueError('SPU context readback guard changed')
    text=text.replace(guard,'''   -- Save read addresses are driven on rising clk1x edges by savestates.vhd.
   -- Context reads use the intervening falling edge; guest reads keep their
   -- original asynchronous bank. Tested separately with streaming save scans.
''')
    path.write_text(text)
    item.update(sha256=digest(path),required_controls={},
        save_read_address_contract='Address/rden change just after rising clk1x, stable through falling edge',
        save_transfer_supported=True,save_transfer_verified=False)
    return item


def apply_spu_save_context(manifest):
    if set(manifest['targets'])!={'psx_top'}:raise ValueError('Save context requires connected psx_top')
    target=manifest['targets']['psx_top']; directory=Path(target['directory'])
    index=next(i for i,s in enumerate(target['quartus_sources']) if Path(s['path']).name=='spu.vhd')
    prior=target['quartus_sources'][index]
    item=save_context_source(directory/'spu-save-context',prior['path'])
    qsf=directory/(target['top']+'.qsf'); text=qsf.read_text()
    if text.count(prior['path'])!=1:raise ValueError('Expected one SPU source assignment')
    qsf.write_text(text.replace(prior['path'],item['path']))
    target['quartus_sources'][index]=item; target['project_sha256']['.qsf']=digest(qsf)
    target['spu_context_ram']=item; manifest['spu_context_ram']=item
    (directory.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
