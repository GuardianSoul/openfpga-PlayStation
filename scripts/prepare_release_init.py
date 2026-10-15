"""Lean reset-only MiSTer initialization using its actual clear address cursor.

Preserve reset/header/clear handshakes and clock timing. This has no save/load
API: the existing reset-only control contract is mandatory.
"""
from pathlib import Path
import re

from prepare_reset_only import reset_only_source
from prepare_resource_targets import digest


def release_init_source(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    baseline=reset_only_source(directory/'baseline');text=Path(baseline['path']).read_text()
    states=baseline['removed_save_states']
    for state in states:
        pattern=rf'^      {state},\n'
        text,n=re.subn(pattern,'',text,flags=re.M)
        if n!=1:raise ValueError('Unreachable state enum changed: '+state)
    pattern=r'            when '+re.escape(' | '.join(states))+r' =>.*?               null;\n\n'
    text,n=re.subn(pattern,'',text,flags=re.S)
    if n!=1:raise ValueError('Unreachable state guard changed')
    before="""                     if (savemode = '1') then
                        state             <= SAVE_WAITSETTLE;
                     else
                        state             <= LOAD_WAITSETTLE;
                     end if;"""
    if text.count(before)!=1:raise ValueError('Constant reset-mode state selector changed')
    text=text.replace(before,'                     state             <= LOAD_WAITSETTLE;')
    text,n=re.subn(r'signal unstallwait\s*: integer range 0 to 67108863 := 0;',
        'signal unstallwait         : integer range 0 to 1023 := 0;',text)
    if n!=1:raise ValueError('Reset retry counter changed')
    # Only the removed SAVE arms assigned a retry of 67108863. Reset uses1023.
    if 'unstallwait <= 67108863' in text:raise ValueError('Long retry remains reachable')
    patterns=[r'^   signal count\s*: integer range 0 to 524288 := 0;\n',
        r'^   signal maxcount\s*: integer range 0 to 524288;\n']
    for pattern in patterns:
        text,n=re.subn(pattern,'',text,flags=re.M)
        if n!=1:raise ValueError('Clear counter declaration changed')
    for pattern in [r'^                  count\s*<= 2;\n',r'^                  maxcount\s*<= savetypes\(savetype_counter\).size;\n',
        r'^                     count\s*<= count \+ 2;\n']:
        text,n=re.subn(pattern,'',text,flags=re.M)
        if n!=1:raise ValueError('Clear counter update changed')
    # In reset mode FASTSIM stops after scratchpad, before SPU; this shortcut
    # exists only for save/load simulation, which this controller cannot enter.
    pattern=r"                     if \(FASTSIM = '1' and count = 1024\) then.*?                     end if;\n"
    text,n=re.subn(pattern,'',text,flags=re.S)
    if n!=1:raise ValueError('Unreachable FASTSIM SPU shortcut changed')
    before='if (useSPUSDRAM = \'1\' or count <= 1024 or count > 16#1C000#) then'
    if text.count(before)!=1:raise ValueError('SPU clear boundary changed')
    text=text.replace(before,"if (useSPUSDRAM = '1' or RAMAddrNext < 2048 or RAMAddrNext >= 16#38000#) then")
    if text.count('if (count < maxcount) then')!=1:raise ValueError('Clear completion test changed')
    text=text.replace('if (count < maxcount) then',"if (clear_finished = '0') then")
    marker='   signal RAMAddrNext         : unsigned(18 downto 0) := (others => \'0\');'
    if text.count(marker)!=1:raise ValueError('Clear cursor declaration changed')
    text=text.replace(marker,marker+"\n   signal clear_finished : std_logic;")
    marker='begin \n\n   -- synthesis translate_off'
    if text.count(marker)!=1:raise ValueError('Reset-only architecture marker changed')
    text=text.replace(marker,'begin \n'+'''
   -- Tested only at the second-word/packet completion state. Scratch counts
   -- words; SPU/VRAM cursors reach 2**18; main RAM's 19-bit cursor wraps after
   -- its final word. No independent loop counter or maxcount bank is needed.
   clear_finished <= '1' when
      (savetype_counter = 12 and RAMAddrNext >= 256) or
      ((savetype_counter = 14 or savetype_counter = 15) and RAMAddrNext(18) = '1') or
      (savetype_counter = 16 and RAMAddrNext = 0) else '0';

   -- synthesis translate_off''')
    if re.search(r'\bcount\b|\bmaxcount\b',re.sub(r'--[^\n]*','',text)):raise ValueError('Independent clear counter use remains')
    path=directory/'savestates.vhd';path.write_text(text)
    return dict(path=str(path),library='work',sha256=digest(path),original=baseline['original'],
        original_sha256=baseline['original_sha256'],baseline=baseline,preparer_sha256=digest(Path(__file__)),
        required_controls=baseline['required_controls'],required_generics={'SAVETYPESCOUNT':17},
        reset_retry_max=1023,clear_cursor_bits=19,unreachable_states_removed=states,
        reset_header_reads_preserved=True,all_clear_regions_preserved=True,SPUSDRAM_modes=[0,1],
        hardware_verified=False)
