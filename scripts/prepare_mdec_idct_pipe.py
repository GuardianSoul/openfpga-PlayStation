"""Register the MDEC IDCT multiplier operands one cycle earlier.

The IDCT multiplies a block-RAM output (coefficient or 30-bit temporary row)
by a scale-table block-RAM output in the same 2x cycle; on the fitted
reset-only top that path misses by 5.2 ns. This variant inserts one operand
register stage (all calc0 control travels with it). Every IDCT result is
still written at least 9 cycles before idct_done, the temporary row a stage
two read needs is written about 28 cycles before that read, and idct_done,
the buffer swap and all MDEC outputs keep their original cycles, so the MDEC
is cycle-identical at its ports.
"""
import json
from pathlib import Path
import re
import shutil

if __package__:
    from .prepare_resource_targets import UPSTREAM, digest
else:
    from prepare_resource_targets import UPSTREAM, digest

FIELDS = (('ena', 'std_logic'), ('stage', 'std_logic'), ('target1', 'integer range 0 to 63'),
          ('target2', 'integer range 0 to 511'), ('first', 'std_logic'), ('last', 'std_logic'),
          ('mul11', 'signed(29 downto 0)'), ('mul12', 'signed(15 downto 0)'),
          ('mul21', 'signed(29 downto 0)'), ('mul22', 'signed(15 downto 0)'))


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned MDEC source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def idct_pipe_text(text):
    # Operand widths follow the source (mdec-mednafen narrows them to 16 bits).
    width = re.search(r'signal idct_calc0_mul11    : (signed\(\d+ downto 0\));', text)
    if not width: raise ValueError('Pinned MDEC source changed near idct_calc0_mul11')
    fields = [(name, width[1] if name in ('mul11', 'mul21') else kind) for name, kind in FIELDS]
    declarations = ''.join(f'   signal idct_calcP_{name:<10}: {kind};\n' for name, kind in fields)
    text = _replace(text, '   signal idct_calc1_ena      : std_logic := \'0\';\n',
                    '   -- Operand register stage (mdec-idct-pipe).\n' + declarations +
                    '   signal idct_calc1_ena      : std_logic := \'0\';\n')
    old = '''            idct_calc1_ena     <= idct_calc0_ena;
            idct_calc1_stage   <= idct_calc0_stage;
            idct_calc1_target1 <= idct_calc0_target1;
            idct_calc1_target2 <= idct_calc0_target2;
            idct_calc1_first   <= idct_calc0_first;
            idct_calc1_last    <= idct_calc0_last;
            idct_calc1_mul1    <= idct_calc0_mul11 * idct_calc0_mul12;
            idct_calc1_mul2    <= idct_calc0_mul21 * idct_calc0_mul22;
'''
    stage = ''.join(f'            idct_calcP_{name:<8} <= idct_calc0_{name};\n' for name, _ in FIELDS)
    new = stage + '\n' + old.replace('idct_calc0_', 'idct_calcP_')
    return _replace(text, old, new)


def idct_pipe_source(directory, original):
    original = Path(original)
    text = idct_pipe_text(original.read_text())
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'mdec.vhd'
    path.write_text('-- GPL-2.0 MDEC specialization; registered IDCT multiplier operands.\n' + text)
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    return {'path': str(path), 'library': 'work', 'sha256': digest(path),
            'original': str(original), 'original_sha256': digest(original),
            'transform': 'One IDCT operand register stage; port behavior cycle-identical',
            'game_verified': False, 'hardware_verified': False}


def apply_mdec_idct_pipe(manifest):
    target = manifest['targets']['psx_top']
    if 'mdec_idct_pipe' in target: raise ValueError('MDEC IDCT pipeline already applied')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'mdec.vhd')
    previous = target['quartus_sources'][index]['path']
    directory = Path(target['directory']); copy = idct_pipe_source(directory / 'mdec-idct-pipe', previous)
    target['quartus_sources'][index] = copy
    qsf = directory / (target['top'] + '.qsf'); text = qsf.read_text()
    if text.count(previous) != 1: raise ValueError('Expected one MDEC assignment')
    qsf.write_text(text.replace(previous, copy['path']))
    target['project_sha256']['.qsf'] = digest(qsf)
    target['mdec_idct_pipe'] = copy; manifest['mdec_idct_pipe'] = copy
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
