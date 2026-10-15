"""Measure Pocket controls: two pads with digital, analog and DualShock modes.

Pads 1 and 2 keep every button, both sticks and the Digital/Analog/DualShock
mode inputs dynamic (rumble outputs stay connected). Devices the Pocket and
its Dock cannot attach are fixed off: mouse, lightguns (GunCon/Justifier),
neGcon, arcade stick, Pop'n, multitap and SNAC. Both memory cards and every
guest serial register remain implemented. This is a measurement, not a
release selection.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest, joypad_fields, wrapper, write_project
else:
    from prepare_resource_targets import digest, joypad_fields, wrapper, write_project

KEPT_MODES = ('PadPortEnable', 'PadPortDigital', 'PadPortAnalog', 'PadPortDS', 'ToggleDS')


def apply_pocket_dualshock(manifest):
    if set(manifest['targets']) not in ({'psx_top'}, {'joypad'}):
        raise ValueError('Pocket DualShock controls require the system or joypad alone')
    name = next(iter(manifest['targets'])); target = manifest['targets'][name]
    if 'pocket_controls' in target: raise ValueError('Pocket controls already applied')
    if 'compile_profile' in manifest or 'gte_adapter' in target or 'reset_only_specialization' in target:
        raise ValueError('Configure Pocket controls before applying other variants/profiles')
    fixed = dict(target['fixed_ports'])
    disabled = ('multitap', 'multitapDigital', 'multitapAnalog', 'MouseEvent', 'MouseLeft', 'MouseRight',
                'actionNextSnac', 'receiveValidSnac', 'ackSnac', 'snacMC', 'neGconRumble')
    disabled += ('snacPort1', 'snacPort2', 'irq10Snac') if name == 'psx_top' else ('snacPort1_in', 'snacPort2_in')
    for port in disabled: fixed[port] = "'0'"
    for port in ('MouseX', 'MouseY', 'receiveBufferSnac'): fixed[port] = "(others=>'0')"
    record_fixed = {}
    for pad in range(1, 5):
        values = {}
        for field, kind in joypad_fields():
            if pad <= 2 and (field.startswith('Key') or field.startswith('Analog') or field in KEPT_MODES): continue
            values[field] = "(others=>'0')" if kind.startswith('signed') else "'0'"
        record_fixed['joypad' + str(pad)] = values
    directory = Path(target['directory']); top, external, text = wrapper(name, fixed, record_fixed)
    path = directory / (top + '.vhd'); path.write_text(text)
    replaced = 0
    for source in target['quartus_sources']:
        if source['path'] == str(path): source['sha256'] = digest(path); replaced += 1
    if replaced != 1: raise ValueError('Expected one resource wrapper')
    sdc = (directory / (top + '.sdc')).read_text().splitlines()
    target['project_sha256'] = write_project(directory, top, external, target['physical_clock_ports'], target['quartus_sources'], sdc)
    target.update(fixed_ports=fixed, external_ports=[port for port, _, _ in external])
    target['pocket_controls'] = {'controller': 'Two pads; digital, analog and DualShock modes dynamic',
        'fixed_record_fields': record_fixed, 'guest_memory_cards': 2,
        'excluded_host_devices': ['Pads 3-4', 'Multitap', 'Mouse', 'GunCon', 'Justifier', 'neGcon', 'Arcade stick', "Pop'n", 'SNAC'],
        'hardware_verified': False}
    manifest['pocket_controls'] = target['pocket_controls']
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
