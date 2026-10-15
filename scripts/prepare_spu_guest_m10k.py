"""Move the SPU's CPU-readback register copies from LUT RAM to block RAM.

Guest reads of voice registers (256 x 16), voice volumes (64 x 16) and, with
spu-reverb-table, the reverb block (32 x 16) come from asynchronous-read
MLAB copies. bus_addr is a clk1x register, so a block RAM that samples it on
the falling clk1x edge, as the upstream context copies already do, delivers
the same word before the rising edge that registers bus_dataRead. Writes stay
on the rising edge, so a read sees exactly the writes the MLAB read saw.
This frees the copies' memory LABs and their read multiplexers.
"""
import json
from pathlib import Path
import re
import shutil

if __package__:
    from .prepare_resource_targets import UPSTREAM, digest
else:
    from prepare_resource_targets import UPSTREAM, digest


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned SPU source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def guest_block(name, signal, width_ad):
    pattern = (r'      guest_table : if i /= 1 generate\n      itagram' + name + r' : entity mem\.RamMLAB\n.*?'
               r'         q          => ' + signal + r'_dataB\(i\)\n      \);\n      end generate;\n')
    new = f'''      guest_table : if i /= 1 generate
         -- Guest readback copy in block RAM, read on the falling edge.
         ram : entity mem.dpram
         generic map (addr_width => {width_ad}, data_width => 16)
         port map (
            clock_a => clk1x,
            address_a => std_logic_vector({signal}_addrA),
            data_a => {signal}_dataA, wren_a => {signal}_write,
            q_a => open,
            clock_b => not clk1x,
            address_b => std_logic_vector({signal}_addrB(i)),
            data_b => (others => '0'), wren_b => '0',
            q_b => {signal}_dataB(i)
         );
      end generate;
'''
    return pattern, new


def guest_m10k_text(text):
    for name, signal, width_ad in (('VOICEREGS', 'RamVoice', 8), ('VOICEVOLUMES', 'RamVoiceVolumes', 6)):
        pattern, new = guest_block(name, signal, width_ad)
        text, count = re.subn(pattern, lambda m: new, text, flags=re.S)
        if count != 1: raise ValueError('Pinned SPU guest table changed: ' + name)
    if 'rvTable_guest' in text:
        text = _replace(text, 'attribute ramstyle of rvTable_guest  : signal is "MLAB, no_rw_check";',
                        'attribute ramstyle of rvTable_guest  : signal is "M10K, no_rw_check";')
        old = "   rv_guest_q     <= rvTable_guest(rv_guest_addr)   when rvValid(rv_guest_addr) = '1'  else (others => '0');\n"
        text = _replace(text, old, '''   -- Reverb guest copy in block RAM, read on the falling edge (spu-guest-m10k).
   process (clk1x)
   begin
      if falling_edge(clk1x) then
         rv_guest_ram_q <= rvTable_guest(rv_guest_addr);
      end if;
   end process;
   rv_guest_q     <= rv_guest_ram_q when rvValid(rv_guest_addr) = '1'  else (others => '0');
''')
        text = _replace(text, '   signal rv_guest_q     : std_logic_vector(15 downto 0);\n',
                        '   signal rv_guest_q     : std_logic_vector(15 downto 0);\n   signal rv_guest_ram_q : std_logic_vector(15 downto 0);\n')
    return text


def guest_m10k_source(directory, original):
    original = Path(original)
    text = guest_m10k_text(original.read_text())
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'spu.vhd'
    path.write_text('-- GPL-2.0 SPU specialization; guest readback copies in block RAM.\n' + text)
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    return {'path': str(path), 'library': 'work', 'sha256': digest(path),
            'original': str(original), 'original_sha256': digest(original),
            'transform': 'Voice, volume and reverb readback copies in falling-edge block RAM',
            'game_verified': False, 'hardware_verified': False}


def apply_spu_guest_m10k(manifest):
    target = manifest['targets']['psx_top']
    if 'spu_guest_m10k' in target: raise ValueError('SPU guest block RAM already applied')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'spu.vhd')
    previous = target['quartus_sources'][index]['path']
    directory = Path(target['directory']); copy = guest_m10k_source(directory / 'spu-guest-m10k', previous)
    target['quartus_sources'][index] = copy
    qsf = directory / (target['top'] + '.qsf'); text = qsf.read_text()
    if text.count(previous) != 1: raise ValueError('Expected one SPU assignment')
    qsf.write_text(text.replace(previous, copy['path']))
    target['project_sha256']['.qsf'] = digest(qsf)
    target['spu_guest_m10k'] = copy; manifest['spu_guest_m10k'] = copy
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
