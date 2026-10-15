"""Prune optional polygon timing bookkeeping with REPRODUCIBLEGPUTIMING off."""
import json
from pathlib import Path
import re
import shutil
import subprocess

if __package__:
    from .prepare_resource_targets import UPSTREAM, digest
    from .tool_paths import ROOT
else:
    from prepare_resource_targets import UPSTREAM, digest
    from tool_paths import ROOT

PIN = 'cd17b5a0ad261005c321d9d5ba41b8dbe313a7d3'
AUXILIARY_TIMING = {
    'gpu_rect': ({'drawTiming': ('unsigned(31 downto 0)', 2),
                  'targetTiming': ('unsigned(31 downto 0)', 2)}, 'state', 'WAITIMING'),
    'gpu_line': ({'drawTiming': ('unsigned(31 downto 0)', 2),
                  'targetTiming': ('unsigned(31 downto 0)', 3)}, 'recstate', 'REQUESTWAITIMING'),
    'gpu_vram2vram': ({'drawTiming': ('unsigned(6 downto 0)', 2)}, 'state', 'WAITIMING'),
    'gpu_vram2cpu': ({'drawTiming': ('unsigned(6 downto 0)', 2)}, 'state', 'WAITIMING'),
    'gpu_fillVram': ({'timer': ('integer', 3), 'timeCnt': ('integer', 1)}, 'state', 'WAITING'),
}


def pruned_timing_source(directory, selected=None):
    pin = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    if pin != PIN or subprocess.run(['git', '-C', str(UPSTREAM), 'diff', '--quiet', 'HEAD']).returncode:
        raise ValueError('GPU reference differs from its recorded pin')
    original = UPSTREAM / 'rtl/gpu_poly.vhd'
    selected = Path(selected) if selected else original
    text = selected.read_text()

    def replace(old, new, description):
        nonlocal text
        if text.count(old) != 1:
            raise ValueError('Pinned GPU timing section changed: ' + description)
        text = text.replace(old, new)

    replace("            if (state /= IDLE) then\n               drawTiming <= drawTiming + 1;\n            end if;",
            '', 'elapsed counter')
    text, count = re.subn(r'^\s*drawTiming\s*<= \(others => \'0\'\);', '', text, flags=re.M)
    if count != 4:
        raise ValueError('Pinned GPU elapsed-counter resets changed')
    replace('                  targetTiming <= to_unsigned(500, 32);', '', 'target reset')
    replace('''                  xMax := abs(vt(0).x - vt(1).x);
                  if (abs(vt(0).x - vt(2).x) > xMax) then xMax := abs(vt(0).x - vt(2).x); end if;
                  if (abs(vt(1).x - vt(2).x) > xMax) then xMax := abs(vt(1).x - vt(2).x); end if;
                  if (rec_transparency = '1' or rec_texture = '1') then 
                     targetTiming <= targetTiming + (vt(2).y + 1 - vt(0).y) * xMax * 4;
                  else
                     targetTiming <= targetTiming + (vt(2).y + 1 - vt(0).y) * xMax * 2;
                  end if;''', '', 'target arithmetic')
    replace('''               when WAITIMING =>
                  if (clk2xIndex = '0' and drawTiming + 4 >= targetTiming) then
                     state <= IDLE;
                     done  <= '1';
                  end if;''', '''               when WAITIMING =>
                  -- No reachable transition enters this state with the required control off.
                  -- synthesis translate_off
                  assert false report "Disabled polygon timing state reached" severity failure;
                  -- synthesis translate_on
                  state <= IDLE;
                  done <= '1';''', 'unreachable wait')
    for name in ('drawTiming', 'targetTiming'):
        text, count = re.subn(r'^\s*signal ' + name + r'\s*:\s*unsigned\(31 downto 0\);', '', text, flags=re.M)
        if count != 1 or re.search(r'\b' + name + r'\b', text):
            raise ValueError('Pinned GPU timing signal changed: ' + name)
    replace('      variable xMax     : integer;', '', 'width temporary')
    replace('begin \n', '''begin 
   -- synthesis translate_off
   assert REPRODUCIBLEGPUTIMING = '0'
      report "Pruned polygon timing requires REPRODUCIBLEGPUTIMING off" severity failure;
   -- synthesis translate_on
''', 'control assertion')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'gpu_poly.vhd'
    path.write_text('-- GPL-2.0 derived disabled polygon timing specialization.\n' + text)
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    result = {'path': str(path), 'library': 'work', 'sha256': digest(path),
              'original': str(original), 'original_sha256': digest(original),
              'selected': str(selected), 'selected_sha256': digest(selected),
              'required_controls': {'REPRODUCIBLEGPUTIMING': 0},
              'transform': 'Omit disabled polygon elapsed/target timing arithmetic and unreachable wait',
              'state_machine_latency_changed': False, 'cycle_equivalence_verified': False,
              'game_verified': False, 'hardware_verified': False}
    (directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def pruned_auxiliary_source(name, directory, selected=None):
    """The listed counters are read only by a disabled timing wait state."""
    pin = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    if pin != PIN or subprocess.run(['git', '-C', str(UPSTREAM), 'diff', '--quiet', 'HEAD']).returncode:
        raise ValueError('GPU reference differs from its recorded pin')
    original = UPSTREAM / ('rtl/' + name + '.vhd')
    selected = Path(selected) if selected else original
    text = selected.read_text()
    counters, state, wait_state = AUXILIARY_TIMING[name]
    for counter, (kind, writes) in counters.items():
        value = '0' if kind == 'integer' else "(others => '0')"
        text, count = re.subn(r'\bsignal ' + counter + r'\s*:\s*' + re.escape(kind) + ';',
                             'constant ' + counter + ' : ' + kind + ' := ' + value + ';', text)
        if count != 1:
            raise ValueError('Pinned timing counter declaration changed: ' + name + '.' + counter)
        # Keep a null statement where a branch contained only bookkeeping.
        text, count = re.subn(r'\b' + counter + r'\s*<=[^;]+;', 'null;', text)
        if count != writes:
            raise ValueError('Pinned timing counter writes changed: ' + name + '.' + counter)
    marker = 'begin \n'
    if text.count(marker) != 1:
        raise ValueError('Pinned GPU architecture marker changed: ' + name)
    text = text.replace(marker, marker + f'''   -- synthesis translate_off
   assert REPRODUCIBLEGPUTIMING = '0'
      report "Pruned {name} timing requires REPRODUCIBLEGPUTIMING off" severity failure;
   assert {state} /= {wait_state}
      report "Disabled {name} timing state reached" severity failure;
   -- synthesis translate_on
''')
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name + '.vhd')
    path.write_text('-- GPL-2.0 derived disabled GPU timing specialization.\n' + text)
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    result = {'path': str(path), 'library': 'work', 'sha256': digest(path),
              'original': str(original), 'original_sha256': digest(original),
              'selected': str(selected), 'selected_sha256': digest(selected),
              'required_controls': {'REPRODUCIBLEGPUTIMING': 0},
              'transform': 'Fix unused timing counters to zero; omit their writes',
              'state_machine_latency_changed': False, 'cycle_equivalence_verified': False,
              'game_verified': False, 'hardware_verified': False}
    (directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def shortened_wrap_source(directory, selected=None):
    original=UPSTREAM/'rtl/gpu.vhd'
    selected=Path(selected) if selected else original
    text=selected.read_text()
    cap='if (reqVRAMSizeRounded > 1024) then reqVRAMSizeRounded := to_unsigned(1024, 11); end if;'
    if text.count(cap)!=1:raise ValueError('Pinned VRAM read-wrap size cap changed')
    rounding='''                        reqVRAMSizeRounded := reqVRAMSize;
                        if (reqVRAMSize(1 downto 0) /= "00") then -- round up read size to full 4*16bit
                           reqVRAMSizeRounded(10 downto 2) := reqVRAMSizeRounded(10 downto 2) + 1;
                        end if;
                        if (reqVRAMXPos(1 downto 0) /= "00" and ((to_integer(reqVRAMXPos(1 downto 0)) + to_integer(reqVRAMSize) > 4))) then{space}
                           reqVRAMSizeRounded(10 downto 2) := reqVRAMSizeRounded(10 downto 2) + 1;
                        end if;
                        if (reqVRAMSizeRounded > 1024) then reqVRAMSizeRounded := to_unsigned(1024, 11); end if;'''.replace('{space}', ' ')
    if text.count(rounding)!=1:raise ValueError('Pinned VRAM read size rounding changed')
    text=text.replace(rounding,'                        reqVRAMSizeRounded := work.psx_gpu_read_math.rounded_read_size(reqVRAMXPos(1 downto 0), reqVRAMSize);')
    marker='''                           if (reqVRAMXPos + reqVRAMSizeRounded > 1024) then
                              reqVRAMwrap <= resize(((reqVRAMXPos + reqVRAMSizeRounded) - 1024) / 4, 8);
                           end if;'''
    if text.count(marker)!=1:raise ValueError('Pinned VRAM read-wrap arithmetic changed')
    text=text.replace(marker,'                           reqVRAMwrap <= work.psx_gpu_read_math.read_wrap(reqVRAMXPos, reqVRAMSizeRounded);')
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    path=directory/'gpu.vhd';path.write_text('-- GPL-2.0 derived bounded VRAM read-wrap arithmetic.\n'+text)
    original_package=ROOT/'rtl/gpu/psx_gpu_read_math.vhd'
    package=directory/original_package.name
    package.write_bytes(original_package.read_bytes())
    shutil.copy2(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    result={'path':str(path),'library':'work','sha256':digest(path),
        'original':str(original),'original_sha256':digest(original),
        'selected':str(selected),'selected_sha256':digest(selected),
        'package':{'path':str(package),'library':'work','sha256':digest(package),
                   'original':str(original_package),'original_sha256':digest(original_package)},
        'transform':'Combine the two modulo rounding increments; use bounded sum bits for read wrap',
        'required_ranges':{'x':[0,1023],'rounded_size':[0,1024]},
        'state_machine_latency_changed':False,'game_verified':False,'hardware_verified':False}
    (directory/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def apply_gpu_timing_prune(manifest):
    if set(manifest['targets']) not in ({'gpu'}, {'psx_top'}):
        raise ValueError('GPU timing pruning requires GPU or system alone')
    target = next(iter(manifest['targets'].values()))
    if target['fixed_ports'].get('REPRODUCIBLEGPUTIMING') != "'0'":
        raise ValueError('GPU timing pruning requires its optional timing control fixed off')
    if 'gpu_timing_prune' in target:
        raise ValueError('GPU timing pruning already applied')
    sources = [s for s in target['quartus_sources'] if Path(s['path']).name == 'gpu_poly.vhd']
    if len(sources) != 1:
        raise ValueError('Expected one selected polygon engine')
    directory = Path(target['directory'])
    selected = sources[0]
    adapted = pruned_timing_source(directory / 'gpu-timing-prune', selected['path'])
    qsf = directory / (target['top'] + '.qsf')
    text = qsf.read_text()
    if text.count(selected['path']) != 1:
        raise ValueError('Expected one selected polygon source assignment')
    target['quartus_sources'][target['quartus_sources'].index(selected)] = adapted
    text = text.replace(selected['path'], adapted['path'])
    auxiliary=[]
    for name in AUXILIARY_TIMING:
        candidates=[s for s in target['quartus_sources'] if Path(s['path']).stem == name]
        if len(candidates)!=1:raise ValueError('Expected one selected GPU timing engine: '+name)
        previous=candidates[0]
        extra=pruned_auxiliary_source(name,directory/'gpu-timing-prune'/name,previous['path'])
        if text.count(previous['path'])!=1:raise ValueError('Expected one selected GPU timing assignment: '+name)
        text=text.replace(previous['path'],extra['path'])
        target['quartus_sources'][target['quartus_sources'].index(previous)]=extra
        auxiliary.append(extra)
    adapted['auxiliary_sources']=auxiliary
    core=next(s for s in target['quartus_sources'] if Path(s['path']).stem=='gpu')
    wrapped=shortened_wrap_source(directory/'gpu-timing-prune'/'read-wrap',core['path'])
    if text.count(core['path'])!=1:raise ValueError('Expected one selected GPU core assignment')
    text=text.replace(core['path'],wrapped['path'])
    index=target['quartus_sources'].index(core)
    target['quartus_sources'][index]=wrapped
    target['quartus_sources'].insert(index,wrapped['package'])
    text+='set_global_assignment -name VHDL_FILE "'+wrapped['package']['path']+'" -library work\n'
    adapted['read_wrap']=wrapped
    qsf.write_text(text)
    target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_timing_prune'] = adapted
    manifest['gpu_timing_prune'] = adapted
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
