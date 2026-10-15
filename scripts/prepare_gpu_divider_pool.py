"""Optional GPU divider serialization; retain original arithmetic and six results."""
import json
from pathlib import Path
import shutil
import subprocess

if __package__:
    from .prepare_resource_targets import UPSTREAM, digest
    from .tool_paths import ROOT
else:
    from prepare_resource_targets import UPSTREAM, digest
    from tool_paths import ROOT

PIN = 'cd17b5a0ad261005c321d9d5ba41b8dbe313a7d3'


def pooled_gpu_source(directory):
    pin = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    if pin != PIN or subprocess.run(['git', '-C', str(UPSTREAM), 'diff', '--quiet', 'HEAD']).returncode:
        raise ValueError('GPU reference differs from its recorded pin')
    original = UPSTREAM / 'rtl/gpu.vhd'
    text = original.read_text()
    marker = '   signal div_array                 : t_div_array;'
    if text.count(marker) != 1:
        raise ValueError('Pinned GPU divider declarations changed')
    text = text.replace(marker, marker + '''
   signal pool_starts, pool_dones : std_logic_vector(5 downto 0);
   signal pool_dividends, pool_quotients : signed(269 downto 0);
   signal pool_divisors, pool_remainders : signed(149 downto 0);''')
    begin = text.index('      idivider : entity work.divider\n', text.index('   gdividers:'))
    end = text.index('   end generate;', begin)
    text = text[:begin] + '''      pool_starts(i) <= div_array(i).start;
      pool_dividends(i*45+44 downto i*45) <= div_array(i).dividend;
      pool_divisors(i*25+24 downto i*25) <= div_array(i).divisor;
      div_array(i).done <= pool_dones(i);
      div_array(i).quotient <= pool_quotients(i*45+44 downto i*45);
      div_array(i).remainder <= pool_remainders(i*25+24 downto i*25);
''' + text[end:]
    marker = '   pixelColor  <= cpu2vram_pixelColor or vram2vram_pixelColor or pipeline_pixelColor;'
    if text.count(marker) != 1:
        raise ValueError('Pinned GPU divider continuation changed')
    text = text.replace(marker, '''   divider_pool : entity work.psx_gpu_divider_pool
   port map(clk=>clk2x, reset=>softreset or SS_reset, starts=>pool_starts,
            dividends=>pool_dividends, divisors=>pool_divisors,
            dones=>pool_dones, quotients=>pool_quotients, remainders=>pool_remainders);

''' + marker)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'gpu.vhd'
    path.write_text('-- GPL-2.0 derived optional GPU divider sharing; added setup latency.\n' + text)
    manager = ROOT / 'rtl/gpu/psx_gpu_divider_pool.vhd'
    copied = directory / manager.name
    copied.write_bytes(manager.read_bytes())
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    result = {'path': str(path), 'library': 'work', 'sha256': digest(path),
              'original': str(original), 'original_sha256': digest(original),
              'scheduler': {'path': str(copied), 'library': 'work', 'sha256': digest(copied),
                            'original': str(manager), 'original_sha256': digest(manager)},
              'transform': 'Serialize six GPU divider lanes through one unchanged MiSTer divider',
              'state_machine_latency_changed': True, 'pixel_equivalence_verified': False,
              'fit_verified': False, 'game_verified': False, 'hardware_verified': False}
    (directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def apply_gpu_divider_pool(manifest):
    if set(manifest['targets']) not in ({'gpu'}, {'psx_top'}):
        raise ValueError('Divider pool requires GPU or system alone')
    name = next(iter(manifest['targets']))
    target = manifest['targets'][name]
    if 'gpu_divider_pool' in target:
        raise ValueError('Divider pool already applied')
    adapted = pooled_gpu_source(Path(target['directory']) / 'gpu-divider-pool')
    matches = [i for i, source in enumerate(target['quartus_sources']) if source['path'] == adapted['original']]
    if len(matches) != 1:
        raise ValueError('Expected exactly one original GPU source')
    index = matches[0]
    target['quartus_sources'][index:index+1] = [adapted['scheduler'], adapted]
    for suffix in ('.qsf',):
        path = Path(target['directory']) / (target['top'] + suffix)
        text = path.read_text()
        marker = f'set_global_assignment -name VHDL_FILE "{adapted["original"]}" -library work'
        if text.count(marker) != 1:
            raise ValueError('Expected one GPU source assignment')
        text = text.replace(marker, '\n'.join(f'set_global_assignment -name VHDL_FILE "{source["path"]}" -library work'
                                            for source in (adapted['scheduler'], adapted)))
        path.write_text(text)
        target['project_sha256'][suffix] = digest(path)
    target['gpu_divider_pool'] = adapted
    manifest['gpu_divider_pool'] = adapted
    (Path(target['directory']).parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
