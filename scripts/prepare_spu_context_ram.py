"""Optional falling-edge block-RAM reads for internal MiSTer SPU tables."""
import json
from pathlib import Path
import re
import shutil
import subprocess

if __package__:
    from .prepare_resource_targets import UPSTREAM, PIN, digest
else:
    from prepare_resource_targets import UPSTREAM, PIN, digest


def context_ram_source(directory, selected=None):
    pin = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    if pin != PIN or subprocess.run(['git', '-C', str(UPSTREAM), 'diff', '--quiet', 'HEAD']).returncode:
        raise ValueError('SPU reference differs from its recorded pin')
    original = UPSTREAM / 'rtl/spu.vhd'
    selected = Path(selected or original)
    text = selected.read_text()
    libraries = set(re.findall(r'entity (\w+)\.RamMLAB', text))
    if len(libraries) != 1 or not libraries <= {'mem', 'ramip'}:
        raise ValueError('Expected pinned hardware or simulation RAM library')
    memory_library = libraries.pop()
    tables = [('VOICEREGS', 'RamVoice', 16, 8, 1),
              ('VOICEVOLUMES', 'RamVoiceVolumes', 16, 6, 1),
              ('VOICERECORDS', 'RamVoiceRecord', 103, 5, 0)]
    for name, base, width, addr, bank in tables:
        pattern = rf'(   g{name} : for i in 0 to 1 generate\n   begin\n)(.*?)(   end generate;)'
        match = re.search(pattern, text, re.S)
        if not match or match[2].count('entity ' + memory_library + '.RamMLAB') != 1:
            raise ValueError('Pinned SPU table changed: ' + name)
        block = f'''      guest_table : if i /= {bank} generate
{match[2]}      end generate;
      context_table : if i = {bank} generate
         ram : entity {memory_library}.dpram
         generic map (addr_width => {addr}, data_width => {width})
         port map (
            clock_a => clk1x,
            address_a => std_logic_vector({base}_addrA),
            data_a => {base}_dataA, wren_a => {base}_write,
            q_a => open,
            clock_b => not clk1x,
            address_b => std_logic_vector({base}_addrB(i)),
            data_b => (others => '0'), wren_b => '0',
            q_b => {base}_dataB(i)
         );
      end generate;
'''
        text = text[:match.start()] + match[1] + block + match[3] + text[match.end():]
    pattern = r'   iEnvCounterRam : entity ' + memory_library + r'\.RamMLAB\n.*?\n   \);'
    text, count = re.subn(pattern, '''   iEnvCounterRam : entity MEMORY_LIBRARY.dpram
   generic map (addr_width => 6, data_width => 25)
   port map (
      clock_a => clk1x,
      address_a => std_logic_vector(EnvCounterRam_addrA),
      data_a => EnvCounterRam_dataA, wren_a => EnvCounterRam_write,
      q_a => open,
      clock_b => not clk1x,
      address_b => std_logic_vector(EnvCounterRam_addrB),
      data_b => (others => '0'), wren_b => '0',
      q_b => EnvCounterRam_dataB
   );'''.replace('MEMORY_LIBRARY', memory_library), text, flags=re.S)
    if count != 1:
        raise ValueError('Pinned SPU envelope table changed')
    marker = 'begin \n\n   ififoIn:'
    if text.count(marker) != 1:
        raise ValueError('Pinned SPU architecture changed')
    text = text.replace(marker, '''begin
   -- synthesis translate_off
   assert SS_rden = '0'
      report "Internal SPU context RAM probe requires savestate readback off" severity failure;
   -- synthesis translate_on

   ififoIn:''')
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'spu.vhd'; path.write_text(text)
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    return {'path': str(path), 'library': 'work', 'sha256': digest(path),
            'original': str(original), 'original_sha256': digest(original),
            'selected': str(selected), 'selected_sha256': digest(selected),
            'transform': 'Internal SPU tables: unchanged write edge, falling-edge block-RAM read',
            'tables': ['Voice registers bank 1', 'Voice volumes bank 1',
                       'Voice records bank 0', 'Envelope counters'],
            'guest_read_banks_retained': True,
            'required_controls': {'SS_rden': 0},
            'state_machine_latency_changed': False,
            'cycle_equivalence_verified': False, 'game_verified': False, 'hardware_verified': False}


def apply_spu_context_ram(manifest):
    if set(manifest['targets']) not in ({'spu'}, {'psx_top'}):
        raise ValueError('SPU context RAM requires SPU or connected system alone')
    for name, target in manifest['targets'].items():
        if 'spu_context_ram' in target:
            raise ValueError('SPU context RAM already applied')
        if name == 'spu' and target['fixed_ports'].get('SS_rden') != "'0'":
            raise ValueError('SPU context RAM requires savestate readback off')
        if name == 'psx_top':
            reset = target.get('reset_only_specialization', {})
            if len(reset.get('removed_save_states', [])) != 12:
                raise ValueError('System requires reset-only sequencer with SAVE/readback arms removed')
            for control in ('save_state', 'load_state', 'rewind_on', 'rewind_active', 'loadExe'):
                if target['fixed_ports'].get(control) != "'0'":
                    raise ValueError('SPU context RAM requires reset-only controls off: ' + control)
        index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'spu.vhd')
        prior = target['quartus_sources'][index]
        replacement = context_ram_source(Path(target['directory']) / 'spu-context', prior['path'])
        qsf = Path(target['directory']) / (target['top'] + '.qsf')
        text = qsf.read_text()
        if text.count(prior['path']) != 1:
            raise ValueError('Expected one SPU source assignment')
        qsf.write_text(text.replace(prior['path'], replacement['path']))
        target['quartus_sources'][index] = replacement
        target['project_sha256']['.qsf'] = digest(qsf)
        target['spu_context_ram'] = replacement
        manifest['spu_context_ram'] = replacement
        (Path(target['directory']).parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
