"""Read the SPU reverb configuration block from LUT RAM instead of registers.

Guest registers 0x1F801DC0-0x1F801DFE (32 halfwords: APF/comb/wall/IIR
coefficients and the left/right buffer offsets) are written to two
asynchronous-read MLAB copies on the original clk1x edge. The guest copy
serves bus reads; the engine copy is addressed by the reverb state, step and
side, which select exactly the register each original expression read in
that step. The flip-flop copies lose all readers and are removed by
synthesis, except dAPF1/dAPF2, which the address step subtracts from mAPF1/2
in the same cycle. vLOUT, vROUT and mBASE stay in registers. Valid bits
reproduce the reset clear. Savestate transfers must be off (reset-only).
"""
import json
from pathlib import Path
import re
import shutil

if __package__:
    from .prepare_resource_targets import UPSTREAM, digest
else:
    from prepare_resource_targets import UPSTREAM, digest

# Halfword index (address - 0x1C0) / 2 of each block register.
INDEX = {name: i for i, name in enumerate((
    'dAPF1', 'dAPF2', 'vIIR', 'vCOMB1', 'vCOMB2', 'vCOMB3', 'vCOMB4', 'vWALL', 'vAPF1', 'vAPF2',
    'mLSAME', 'mRSAME', 'mLCOMB1', 'mRCOMB1', 'mLCOMB2', 'mRCOMB2', 'dLSAME', 'dRSAME',
    'mLDIFF', 'mRDIFF', 'mLCOMB3', 'mRCOMB3', 'mLCOMB4', 'mRCOMB4', 'dLDIFF', 'dRDIFF',
    'mLAPF1', 'mRAPF1', 'mLAPF2', 'mRAPF2', 'vLIN', 'vRIN'))}


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f'SPU source changed near ({text.count(old)} matches): ' + old.strip().splitlines()[0])
    return text.replace(old, new)


def _side(left, right):
    """Index expression selecting left/right by reverbRight, as the original muxes did."""
    if INDEX[right] != INDEX[left] + 1 and INDEX[left] != INDEX[right] + 1:
        raise ValueError('Left/right registers are not adjacent')
    return f'{INDEX[left]} + side' if INDEX[right] == INDEX[left] + 1 else f'{INDEX[left]} - side'


def engine_address():
    """Table index read by each engine step; matches every consumer of the step."""
    steps = {
        'REVERB_READ1': {0: _side('dLSAME', 'dRSAME'), 1: _side('dRDIFF', 'dLDIFF'),
                         2: _side('mLSAME', 'mRSAME'), 3: _side('mLDIFF', 'mRDIFF')},
        'REVERB_WRITE1': {0: _side('mLSAME', 'mRSAME'), 1: _side('mLDIFF', 'mRDIFF')},
        'REVERB_READ2': {0: _side('mLCOMB1', 'mRCOMB1'), 1: _side('mLCOMB2', 'mRCOMB2'),
                         2: _side('mLCOMB3', 'mRCOMB3'), 3: _side('mLCOMB4', 'mRCOMB4'),
                         4: _side('mLAPF1', 'mRAPF1'), 5: _side('mLAPF2', 'mRAPF2')},
        # Count 1 and every later count used the default mAPF2 branch.
        'REVERB_WRITE2': {0: _side('mLAPF1', 'mRAPF1'), 'others': _side('mLAPF2', 'mRAPF2')},
        'REVERB_PROCOUT': {0: INDEX['vWALL'], 1: _side('vLIN', 'vRIN'), 2: INDEX['vWALL'],
                           3: INDEX['vIIR'], 4: INDEX['vIIR'], 5: INDEX['vIIR'], 6: INDEX['vIIR']},
        'REVERB_PROCIN': {0: INDEX['vCOMB1'], 1: INDEX['vCOMB2'], 2: INDEX['vCOMB3'], 3: INDEX['vCOMB4'],
                          4: INDEX['vAPF1'], 6: INDEX['vAPF2'], 7: INDEX['vAPF1'], 10: INDEX['vAPF2']},
    }
    lines = ['   process (state, reverb_count, reverbRight)',
             '      variable side : integer range 0 to 1;',
             '   begin',
             "      if (reverbRight = '1') then side := 1; else side := 0; end if;",
             '      rv_engine_addr <= 0;',
             '      case (state) is']
    for state, counts in steps.items():
        lines.append(f'         when {state} =>')
        lines.append('            case (reverb_count) is')
        for count, value in counts.items():
            lines.append(f'               when {count} => rv_engine_addr <= {value};')
        if 'others' not in counts: lines.append('               when others => null;')
        lines.append('            end case;')
    lines += ['         when others => null;', '      end case;', '   end process;', '']
    return '\n'.join(lines)


def reverb_table_text(text):
    text = _replace(text, '	signal REVERB_vRIN', '''   -- Reverb block 0x1C0-0x1FE in LUT RAM: guest and engine read copies.
   type trvTable is array(0 to 31) of std_logic_vector(15 downto 0);
   signal rvTable_guest  : trvTable;
   signal rvTable_engine : trvTable;
   attribute ramstyle : string;
   attribute ramstyle of rvTable_guest  : signal is "MLAB, no_rw_check";
   attribute ramstyle of rvTable_engine : signal is "MLAB, no_rw_check";
   signal rvValid        : std_logic_vector(31 downto 0) := (others => '0');
   signal rv_guest_addr  : integer range 0 to 31;
   signal rv_engine_addr : integer range 0 to 31;
   signal rv_guest_q     : std_logic_vector(15 downto 0);
   signal rv_engine_q    : std_logic_vector(15 downto 0);
	signal REVERB_vRIN''')
    text = _replace(text, '               REVERB_vLOUT         <= (others => \'0\');\n',
                    '               REVERB_vLOUT         <= (others => \'0\');\n               rvValid              <= (others => \'0\');\n')
    # Table write beside the original register writes of the same bus cycle.
    write_case = '''               else
                  case (to_integer(bus_addr)) is
                     when 16#180# => VOLUME_LEFT               <= bus_dataWrite;'''
    text = _replace(text, write_case, '''               else
                  if (bus_addr(9 downto 6) = "0111" and bus_addr(0) = '0') then
                     rvTable_guest(rv_guest_addr)  <= bus_dataWrite;
                     rvTable_engine(rv_guest_addr) <= bus_dataWrite;
                     rvValid(rv_guest_addr)        <= '1';
                  end if;
                  case (to_integer(bus_addr)) is
                     when 16#180# => VOLUME_LEFT               <= bus_dataWrite;''')
    read_branch = '''               elsif (bus_addr >= 16#200# and bus_addr < 16#260#) then
                  bus_dataRead <= RamVoiceVolumes_dataB(0);
'''
    text = _replace(text, read_branch, read_branch + '''               elsif (bus_addr(9 downto 6) = "0111" and bus_addr(0) = '0') then
                  bus_dataRead <= rv_guest_q;
''')
    # Engine consumers.
    for name in ('vWALL', 'vIIR', 'vCOMB1', 'vCOMB2', 'vCOMB3', 'vCOMB4', 'vAPF1', 'vAPF2'):
        pattern = f'signed(REVERB_{name})'
        if pattern not in text: raise ValueError('SPU engine use missing: ' + name)
        text = text.replace(pattern, 'signed(rv_engine_q)')
    for name in ('vAPF1', 'vAPF2'):
        old = f'(REVERB_{name} = x"8000")'
        text = _replace(text, old, '(rv_engine_q = x"8000")')
    for derived, left in (('mSAME', 'mLSAME'), ('mCOMB1', 'mLCOMB1'), ('mCOMB2', 'mLCOMB2'), ('dSAME', 'dLSAME'),
                          ('mDIFF', 'mLDIFF'), ('mCOMB3', 'mLCOMB3'), ('mCOMB4', 'mLCOMB4'), ('dDIFF', 'dRDIFF'),
                          ('mAPF1', 'mLAPF1'), ('mAPF2', 'mLAPF2')):
        pattern = rf'   REVERB_{derived} *<= unsigned\(REVERB_{left} *\) when \(reverbRight = \'0\'\) else unsigned\(REVERB_\w+ *\);\n'
        if len(re.findall(pattern, text)) != 1: raise ValueError('SPU derived select changed: ' + derived)
        text = re.sub(pattern, f'   REVERB_{derived} <= unsigned(rv_engine_q);\n', text)
    pattern = r"   REVERB_vIN    <=   signed\(REVERB_vLIN   \) when \(reverbRight = '0'\) else   signed\(REVERB_vRIN   \);\n"
    if len(re.findall(pattern, text)) != 1: raise ValueError('SPU vIN select changed')
    text = re.sub(pattern, '   REVERB_vIN <= signed(rv_engine_q);\n', text)
    # The new branch precedes the guest read case for the whole block.
    pattern = r'                     when 16#1[C-F][02468ACE]# => bus_dataRead <= REVERB_\w+; *\n'
    if len(re.findall(pattern, text)) != 32: raise ValueError('SPU guest reverb reads changed')
    text = re.sub(pattern, '', text)
    marker = '   reverb_reqAddr  <= '
    reads = '''   rv_guest_addr  <= to_integer(bus_addr(5 downto 1));
   rv_guest_q     <= rvTable_guest(rv_guest_addr)   when rvValid(rv_guest_addr) = '1'  else (others => '0');
   rv_engine_q    <= rvTable_engine(rv_engine_addr) when rvValid(rv_engine_addr) = '1' else (others => '0');

''' + engine_address() + '''
   -- synthesis translate_off
   process (clk1x)
   begin
      if rising_edge(clk1x) then
         assert SS_wren = '0' and SS_rden = '0' and loading_savestate = '0'
            report "SPU reverb table requires savestate transfers off" severity failure;
      end if;
   end process;
   -- synthesis translate_on

'''
    text = _replace(text, marker, reads + marker)
    return text


def reverb_table_source(directory, original):
    original = Path(original)
    text = reverb_table_text(original.read_text())
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'spu.vhd'
    path.write_text('-- GPL-2.0 SPU specialization; reverb configuration block read from LUT RAM.\n' + text)
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    return {'path': str(path), 'library': 'work', 'sha256': digest(path),
            'original': str(original), 'original_sha256': digest(original),
            'transform': 'Reverb registers 0x1C0-0x1FE read from guest and engine LUT-RAM copies with valid bits',
            'required_controls': {'savestate_transfers': 'off (reset-only)'},
            'game_verified': False, 'hardware_verified': False}


def apply_spu_reverb_table(manifest):
    if set(manifest['targets']) != {'psx_top'}:
        raise ValueError('SPU reverb table requires the connected reset-only system')
    target = manifest['targets']['psx_top']
    if 'spu_reverb_table' in target: raise ValueError('SPU reverb table already applied')
    if 'reset_only_specialization' not in target: raise ValueError('SPU reverb table requires the reset-only sequencer')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'spu.vhd')
    previous = target['quartus_sources'][index]['path']
    directory = Path(target['directory']); copy = reverb_table_source(directory / 'spu-reverb-table', previous)
    target['quartus_sources'][index] = copy
    qsf = directory / (target['top'] + '.qsf'); text = qsf.read_text()
    if text.count(previous) != 1: raise ValueError('Expected one SPU assignment')
    qsf.write_text(text.replace(previous, copy['path']))
    target['project_sha256']['.qsf'] = digest(qsf)
    target['spu_reverb_table'] = copy; manifest['spu_reverb_table'] = copy
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
