"""Optional early coordinate negation for the pinned MiSTer GPU multiplier."""
import json
from pathlib import Path
import re
import shutil
import subprocess

if __package__:
    from .prepare_resource_targets import UPSTREAM, PIN, digest
else:
    from prepare_resource_targets import UPSTREAM, PIN, digest


def registered_coordinate_source(directory, selected=None):
    pin = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    if pin != PIN or subprocess.run(['git', '-C', str(UPSTREAM), 'diff', '--quiet', 'HEAD']).returncode:
        raise ValueError('GPU reference differs from its recorded pin')
    original = UPSTREAM / 'rtl/gpu_poly.vhd'
    selected = Path(selected or original)
    text = selected.read_text()
    for axis in ('x', 'y'):
        if text.count(axis + '         : integer range -2048 to 2047;') != 1:
            raise ValueError('Pinned vertex coordinate bounds changed: ' + axis)
    marker = '   signal mulIn1              : unsigned(31 downto 0);'
    if text.count(marker) != 1 or 'coreNegativeX' in text:
        raise ValueError('Pinned multiplier declaration changed or coordinates already registered')
    text = text.replace(marker, '''   signal coreNegativeX       : signed(12 downto 0) := (others => '0');
   signal coreNegativeY       : signed(12 downto 0) := (others => '0');
''' + marker)
    marker = '               when CALCBOUNDARY2 =>'
    if text.count(marker) != 1:
        raise ValueError('Pinned boundary state changed')
    text = text.replace(marker, marker + '''
                  coreNegativeX <= -to_signed(vt(coreVertex).x, 13);
                  coreNegativeY <= -to_signed(vt(coreVertex).y, 13);''')
    text, count = re.subn(r'unsigned\(to_signed\(-vt\(coreVertex\)\.([xy]), 32\)\)',
        lambda m: 'unsigned(resize(coreNegative' + m[1].upper() + ', 32))', text)
    if count != 10:
        raise ValueError('Pinned coordinate multiplier selections changed')
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'gpu_poly.vhd'; path.write_text(text)
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    result = {'path':str(path), 'library':'work', 'sha256':digest(path),
        'original':str(original), 'original_sha256':digest(original),
        'selected':str(selected), 'selected_sha256':digest(selected),
        'transform':'Register exact 13-bit negated core coordinates during existing boundary setup',
        'coordinate_range':[-2048,2047], 'negated_coordinate_range':[-2047,2048],
        'register_bits':26, 'state_machine_latency_changed':False,
        'cycle_equivalence_verified':False, 'game_verified':False, 'hardware_verified':False}
    (directory / 'manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def apply_gpu_coordinates(manifest):
    if set(manifest['targets']) not in ({'gpu'}, {'psx_top'}):
        raise ValueError('GPU coordinate registration requires GPU or connected system alone')
    target = next(iter(manifest['targets'].values()))
    if 'gpu_coordinates' in target:
        raise ValueError('GPU coordinates already registered')
    sources = [s for s in target['quartus_sources'] if Path(s['path']).stem == 'gpu_poly']
    if len(sources) != 1:
        raise ValueError('Expected one selected polygon engine')
    prior = sources[0]; directory = Path(target['directory'])
    copy = registered_coordinate_source(directory / 'gpu-coordinates', prior['path'])
    qsf = directory / (target['top'] + '.qsf'); text = qsf.read_text()
    if text.count(prior['path']) != 1:
        raise ValueError('Expected one polygon source assignment')
    qsf.write_text(text.replace(prior['path'], copy['path']))
    target['quartus_sources'][target['quartus_sources'].index(prior)] = copy
    target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_coordinates'] = copy; manifest['gpu_coordinates'] = copy
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest
