"""Experimental FFVII-minimum CD controller: drop CD-DA and TOC-query commands.

Play, Forward, Backward, GetTN, GetTD, GetQ and VideoCD answer like the
unsupported Sync command (error 0x01, reason 0x40). FFVII was not observed
issuing any of them (census, 2026-10-13); with Play gone the CD-DA playback
state is unreachable. Measurement only until the census covers more of the game.
"""
import json
from pathlib import Path
import re
import shutil

if __package__:
    from .prepare_resource_targets import UPSTREAM, digest
else:
    from prepare_resource_targets import UPSTREAM, digest

DROPPED = {'03': 'play', '04': 'forward', '05': 'backward', '13': 'GetTN', '14': 'GetTD', '1D': 'GetQ', '1F': 'VideoCD'}
ERROR = '''                        errorResponseCmd_new    <= '1';
                        errorResponseCmd_error  <= x"01";
                        errorResponseCmd_reason <= x"40";
                        cmdPending <= '0';

'''


def cd_ffvii_text(text):
    for code, name in DROPPED.items():
        head = f'                     when x"{code}" => -- {name}\n'
        if text.count(head) != 1: raise ValueError('CD command branch changed: ' + name)
        start = text.index(head) + len(head)
        end = re.compile(r'^                     when ', re.M).search(text, start).start()
        text = text[:start] + ERROR + text[end:]
    return text


def apply_cd_ffvii(manifest):
    target = manifest['targets']['psx_top']
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'cd_top.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'cd-ffvii'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'cd_top.vhd'; path.write_text(cd_ffvii_text(previous.read_text()))
    shutil.copy2(UPSTREAM / 'LICENSE', out / 'COPYING.GPL2')
    copy = {'path': str(path), 'library': 'work', 'sha256': digest(path), 'original': str(previous),
            'original_sha256': digest(previous), 'dropped_commands': DROPPED, 'game_verified': False}
    target['quartus_sources'][index] = copy
    qsf = directory / (target['top'] + '.qsf'); text = qsf.read_text()
    if text.count(str(previous)) != 1: raise ValueError('Expected one CD assignment')
    qsf.write_text(text.replace(str(previous), copy['path']))
    target['project_sha256']['.qsf'] = digest(qsf)
    target['cd_ffvii'] = copy; manifest['cd_ffvii'] = copy
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
