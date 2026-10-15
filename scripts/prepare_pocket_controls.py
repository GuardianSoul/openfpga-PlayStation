"""Measure a Pocket digital pad configuration without mouse/lightgun/multitap.

Both memory cards and every guest serial register remain implemented. All first
controller button inputs remain dynamic; this does not select a release core.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest, joypad_fields, wrapper, write_project
else:
    from prepare_resource_targets import digest, joypad_fields, wrapper, write_project


def apply_pocket_controls(manifest):
    if set(manifest['targets']) not in ({'psx_top'},{'joypad'}):
        raise ValueError('Pocket controls require the system or joypad alone')
    name=next(iter(manifest['targets'])); target=manifest['targets'][name]
    if 'pocket_controls' in target: raise ValueError('Pocket controls already applied')
    if 'compile_profile' in manifest or 'gte_adapter' in target or 'reset_only_specialization' in target:
        raise ValueError('Configure Pocket controls before applying other variants/profiles')
    fixed=dict(target['fixed_ports'])
    disabled=('multitap','multitapDigital','multitapAnalog','DSAltSwitchMode','MouseEvent','MouseLeft','MouseRight',
        'actionNextSnac','receiveValidSnac','ackSnac','snacMC','neGconRumble')
    disabled+=('snacPort1','snacPort2','irq10Snac') if name=='psx_top' else ('snacPort1_in','snacPort2_in')
    vectors=('MouseX','MouseY','receiveBufferSnac')
    # Both entities expose these host-only inputs. Guest card paths stay dynamic.
    for port in disabled: fixed[port]="'0'"
    for port in vectors: fixed[port]="(others=>'0')"
    record_fixed={}
    for pad in range(1,5):
        values={}
        for field,kind in joypad_fields():
            if pad==1 and field.startswith('Key'): continue
            values[field]="(others=>'0')" if kind.startswith('signed') else "'0'"
        if pad==1: values.update(PadPortEnable="'1'",PadPortDigital="'1'")
        record_fixed['joypad'+str(pad)]=values
    directory=Path(target['directory']); top,external,text=wrapper(name,fixed,record_fixed)
    path=directory/(top+'.vhd'); path.write_text(text)
    replaced=0
    for source in target['quartus_sources']:
        if source['path']==str(path): source['sha256']=digest(path); replaced+=1
    if replaced!=1: raise ValueError('Expected one resource wrapper')
    sdc=(directory/(top+'.sdc')).read_text().splitlines()
    target['project_sha256']=write_project(directory,top,external,target['physical_clock_ports'],target['quartus_sources'],sdc)
    target.update(fixed_ports=fixed,external_ports=[port for port,_,_ in external])
    target['pocket_controls']={'controller':'One digital PSX pad, all sixteen buttons dynamic',
        'fixed_record_fields':record_fixed,'guest_memory_cards':2,
        'excluded_host_devices':['Additional controllers','Analog axes','Mouse','Lightguns','Multitap','SNAC'],
        'hardware_verified':False}
    manifest['pocket_controls']=target['pocket_controls']
    (directory.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
