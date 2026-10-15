#!/usr/bin/env python3
"""Prepare FF-focused PSX component resource projects, not a Pocket core."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .tool_paths import ROOT
else:
    from tool_paths import ROOT

UPSTREAM = ROOT / 'references/PSX_MiSTer'
PIN = 'cd17b5a0ad261005c321d9d5ba41b8dbe313a7d3'
GTE_SOURCES = ['pGTE.vhd','gte_mac0.vhd','gte_mac123.vhd','gte_UNRDivide.vhd','gte.vhd']
CPU_SOURCES = ['export.vhd','divider.vhd','datacache.vhd','cpu.vhd']
CPU_FIXED = {'TURBO':"'0'", 'TURBO_CACHE':"'0'", 'TURBO_CACHE50':"'0'",
             'SS_reset':'reset', 'SS_DataWrite':"(others=>'0')", 'SS_Adr':"(others=>'0')",
             'SS_wren_CPU':"'0'", 'SS_wren_SCP':"'0'", 'SS_rden_CPU':"'0'", 'SS_rden_SCP':"'0'",
             'SS_DataRead_CPU':'open', 'SS_DataRead_SCP':'open', 'SS_idle':'open', 'debug_firstGTE':"'0'"}
GTE_FIXED = {'WIDESCREEN':'"00"', 'TURBO':"'0'", 'loading_savestate':"'0'", 'SS_reset':"'0'",
             'SS_DataWrite':"(others=>'0')", 'SS_Adr':"(others=>'0')", 'SS_wren':"'0'", 'SS_rden':"'0'",
             'SS_DataRead':'open', 'SS_idle':'open', 'debug_firstGTE':'open'}
GPU_SOURCES = ['pGPU.vhd','mul32u.vhd','mul9s.vhd','divider.vhd',
               'gpu_fillVram.vhd','gpu_cpu2vram.vhd','gpu_vram2vram.vhd','gpu_vram2cpu.vhd',
               'gpu_line.vhd','gpu_rect.vhd','gpu_poly.vhd','gpu_pixelpipeline.vhd',
               'gpu_overlay.vhd','gpu_dither.vhd','gpu_videoout_async.vhd','gpu_videoout_sync.vhd',
               'gpu_crosshair.vhd','justifier_sensor.vhd','gpu_videoout.vhd','gpu.vhd']
GPU_FIXED = {name:"'0'" for name in
             ['savestate_busy','ditherOff','interlaced480pHack','REPRODUCIBLEGPUTIMING',
              'pal60','fpscountOn','noTexture','textureFilter2DOff','dither24','render24',
              'drawSlow','debugmodeOn','syncVideoOut','syncInterlace','rotate180','fixedVBlank',
              'hCrop','oldGPU','Gun1CrosshairOn','Gun2CrosshairOn','cdSlow',
              'errorOn','errorEna','LBAOn','loading_savestate',
              'SS_wren_GPU','SS_wren_Timing','SS_rden_GPU','SS_rden_Timing']}
GPU_FIXED.update({'videoout_on':"'1'", 'textureFilter':'"00"', 'textureFilterStrength':'"00"',
                  'vCrop':'"00"', 'SS_reset':'reset',
                  **{name:"(others=>'0')" for name in
                     ['Gun1X','Gun1Y_scanlines','Gun2X','Gun2Y_scanlines','errorCode',
                      'LBAdisplay','SS_DataWrite','SS_Adr']},
                  'Gun1offscreen':"'1'",'Gun2offscreen':"'1'",
                  **{name:'open' for name in ['Gun1IRQ10','Gun2IRQ10','SS_DataRead_GPU','SS_DataRead_Timing','SS_Idle']}})
SPU_FIXED = {'SPUon':"'1'",'SPUIRQTrigger':"'0'",'useSDRAM':"'1'",'REPRODUCIBLESPUIRQ':"'1'",
             'REPRODUCIBLESPUDMA':"'0'",'REVERBOFF':"'0'",'SS_reset':'reset','loading_savestate':"'0'",
             'SS_DataWrite':"(others=>'0')",'SS_Adr':"(others=>'0')",'SS_wren':"'0'",'SS_rden':"'0'",
             'SS_DataRead':'open','SS_idle':'open','SS_RAM_dataWrite':"(others=>'0')",
             'SS_RAM_Adr':"(others=>'0')",'SS_RAM_request':"'0'",'SS_RAM_rnw':"'1'",
             'SS_RAM_dataRead':'open','SS_RAM_done':'open',
             'mem_ack':"'0'",'mem_DOUT':"(others=>'0')",'mem_DOUT_READY':"'0'",
             **{name:'open' for name in ['mem_request','mem_BURSTCNT','mem_ADDR','mem_DIN','mem_BE','mem_WE','mem_RD']}}
MDEC_FIXED = {'SS_reset':'reset','SS_DataWrite':"(others=>'0')",'SS_Adr':"(others=>'0')",
              'SS_wren':"'0'",'SS_rden':"'0'",'SS_DataRead':'open','SS_Idle':'open'}
MEMORY_SOURCES = [('mem','RamMLAB.vhd'),('mem','dpram.vhd'),('work','dpram.vhd'),
                  ('mem','SyncFifo.vhd'),('mem','SyncFifoFallThrough.vhd')]
TARGETS = {
    'cpu':(CPU_FIXED,CPU_SOURCES,[('mem','RamMLAB.vhd'),('work','dpram.vhd')],('clk1x','clk2x','clk3x')),
    'gte':(GTE_FIXED,GTE_SOURCES,[],('clk2x',)),
    'gpu':(GPU_FIXED,GPU_SOURCES,MEMORY_SOURCES,('clk1x','clk2x','clkvid')),
    'spu':(SPU_FIXED,['spu_gauss.vhd','spu_ram.vhd','spu.vhd'],MEMORY_SOURCES,('clk1x','clk2x')),
    'mdec':(MDEC_FIXED,['mdec.vhd'],MEMORY_SOURCES,('clk1x','clk2x')),
}
CLUSTER_COMPONENTS = tuple(TARGETS)
COMPILE_PROFILES = {
    'baseline':{},
    'area':{'OPTIMIZATION_TECHNIQUE':'AREA','MUX_RESTRUCTURE':'ON',
            'ALLOW_ANY_RAM_SIZE_FOR_RECOGNITION':'ON','ALLOW_ANY_ROM_SIZE_FOR_RECOGNITION':'ON',
            'PRE_MAPPING_RESYNTHESIS':'OFF','ADV_NETLIST_OPT_SYNTH_WYSIWYG_REMAP':'OFF',
            'PHYSICAL_SYNTHESIS_REGISTER_RETIMING':'OFF'},
}
COMPILE_PROFILES['area-share']={**COMPILE_PROFILES['area'],'AUTO_RESOURCE_SHARING':'ON'}
COMPILE_PROFILES['area-min-state']={**COMPILE_PROFILES['area-share'],
                                  'STATE_MACHINE_PROCESSING':'MINIMAL BITS'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hardware_text(text):
    """Remove only synthesis translate-off sections; fail on unbalanced markers."""
    lines = []; depth = 0
    for line in text.splitlines(keepends=True):
        if re.search(r'--\s*synthesis\s+translate_off',line,re.I): depth += 1
        elif re.search(r'--\s*synthesis\s+translate_on',line,re.I):
            depth -= 1
            if depth < 0: raise ValueError('Unbalanced synthesis markers')
        elif not depth: lines.append(line)
    if depth: raise ValueError('Unbalanced synthesis markers')
    return ''.join(lines)


def ports(entity):
    text = hardware_text((UPSTREAM/'rtl'/f'{entity}.vhd').read_text())
    declaration = text.split('end entity;')[0]
    # Bounded to the pinned component entities, with literal vector widths only.
    pattern = r'^\s*(\w+)\s*:\s*(in|out|buffer)\s+(std_logic(?:_vector)?(?:\([^;\n]+?\))?|(?:un)?signed\([^;\n]+?\)|joypad_t|integer\s+range\s+\d+\s+to\s+\d+)'
    found = re.findall(pattern,declaration,re.M|re.I)
    expected = re.findall(r'^\s*(\w+)\s*:\s*(?:in|out|buffer)\b',declaration,re.M|re.I)
    if not found or len({name for name,_,_ in found}) != len(found) or set(expected)!={name for name,_,_ in found}:
        raise ValueError(f'Unsupported/duplicate port declaration: {entity}')
    return found


def savestates_disabled(entity, extra=None):
    fixed=dict(extra or {})
    for name,direction,kind in ports(entity):
        if name.startswith('SS_'):
            fixed[name]='open' if direction=='out' else 'reset' if name=='SS_reset' else \
                        "'0'" if kind=='std_logic' else "(others=>'0')"
        elif name=='loading_savestate': fixed[name]="'0'"
    return fixed


PERIPHERALS = {
    'cd_top':({'INSTANTSEEK':"'0'",'FORCECDSPEED':'"000"','LIMITREADSPEED':"'0'",
               'fastCD':"'0'",'testSeek':"'0'",'pauseOnCDSlow':"'0'",'cd_hps_lba_sim':'open'},
              ['cd_xa_zigzag.vhd','cd_xa.vhd','cd_top.vhd'],MEMORY_SOURCES,('clk1x',)),
    'dma':({'TURBO':"'0'",'TURBO_CACHE':"'0'",'ram8mb':"'0'",'ignoreCDTiming':"'0'"},
           ['dma.vhd'],MEMORY_SOURCES,('clk1x','clk3x')),
    'timer':({},['timer.vhd'],[],('clk1x',)),
    'irq':({},['irq.vhd'],[],('clk1x',)),
    'joypad':({},['pJoypad.vhd','joypad_pad.vhd','joypad_mem.vhd','joypad.vhd'],
              MEMORY_SOURCES,('clk1x','clk2x')),
    'memcard':({},['memcard.vhd'],MEMORY_SOURCES,('clk2x',)),
    'memctrl':({},['memctrl.vhd'],[],('clk1x',)),
    'sio':({},['sio.vhd'],[],('clk1x',)),
    'exp2':({},['exp2.vhd'],[],('clk1x',)),
    'memorymux':({'loadExe':"'0'",'fastboot':"'0'",'PATCHSERIAL':"'0'",'TURBO':"'0'",
                  **{name:"(others=>'0')" for name in ('exe_initial_pc','exe_initial_gp',
                      'exe_load_address','exe_file_size','exe_stackpointer')}},
                 ['memorymux.vhd'],MEMORY_SOURCES+[('mem','SyncFifoFallThroughMLAB.vhd')],('clk1x','clk2x')),
}
for entity,(fixed,files,memory,clocks) in PERIPHERALS.items():
    TARGETS[entity]=(savestates_disabled(entity,fixed),files,memory,clocks)
COMPONENT_NAMES = tuple(TARGETS)

# Connected upstream system; external SDRAM/DDR/host interfaces remain exposed.
SYSTEM_FIXED = {name:"'0'" for name in ('loadExe','fastboot','ram8mb','TURBO_MEM','TURBO_COMP',
    'TURBO_CACHE','TURBO_CACHE50','REPRODUCIBLEGPUTIMING','INSTANTSEEK','LIMITREADSPEED',
    'IGNORECDDMATIMING','ditherOff','interlaced480pHack','showGunCrosshairs','enableNeGconRumble',
    'fpscountOn','cdslowOn','testSeek','pauseOnCDSlow','errorOn','LBAOn','PATCHSERIAL',
    'noTexture','textureFilter2DOff','dither24','render24','drawSlow','syncVideoOut',
    'syncInterlace','rotate180','fixedVBlank','hCrop','SPUIRQTrigger','REVERBOFF',
    'REPRODUCIBLESPUDMA','oldGPU','fastCD','pal60','increaseSSHeaderCount','save_state','load_state',
    'rewind_on','rewind_active','cheats_enabled','cheat_on')}
SYSTEM_FIXED.update({'SPUon':"'1'",'SPUSDRAM':"'1'",'videoout_on':"'1'",'cheat_clear':"'1'",
    'FORCECDSPEED':'"000"','textureFilter':'"00"','textureFilterStrength':'"00"','vCrop':'"00"',
    'WIDESCREEN':'"00"','savestate_number':'0','state_loaded':'open','validSStates':'open',
    'cheats_active':'open','Cheats_BusAddr':'open','Cheats_BusRnW':'open',
    'Cheats_BusByteEnable':'open','Cheats_BusWriteData':'open','Cheats_Bus_ena':'open','Cheats_BusDone':"'0'",
    **{name:"(others=>'0')" for name in ('exe_initial_pc','exe_initial_gp','exe_load_address',
        'exe_file_size','exe_stackpointer','cheat_in','Cheats_BusReadData')}})
SYSTEM_SOURCES = re.findall(r'file join \$::quartus\(qip_path\) (\w+\.vhd)',
                            (UPSTREAM/'rtl/psx.qip').read_text())
SYSTEM_SOURCES.remove('psx_mister.vhd')
TARGETS['psx_top']=(SYSTEM_FIXED,SYSTEM_SOURCES,
    MEMORY_SOURCES+[('mem','SyncFifoFallThroughMLAB.vhd')],('clk1x','clk2x','clk3x','clkvid'))
TARGET_NAMES = [*TARGETS,'cluster','platform','laxer_gte']


def joypad_fields():
    text=(UPSTREAM/'rtl/pJoypad.vhd').read_text().split('type joypad_t is record')[1].split('end record')[0]
    return re.findall(r'^\s*(\w+)\s*:\s*(std_logic|signed\(\d+ downto \d+\))',text,re.M)


def wrapper(entity, fixed, record_fixed=None):
    record_fixed=record_fixed or {}
    declarations = ports(entity)
    if set(fixed)-{name for name,_,_ in declarations}:
        raise ValueError(f'Unknown configured port for {entity}')
    external=[]; records=[]
    for name,direction,kind in declarations:
        if name in fixed: continue
        if kind=='joypad_t':
            if direction!='in': raise ValueError('Only input joypad records are supported')
            records.append(name)
            external.extend((name+'_'+field,'in',field_kind) for field,field_kind in joypad_fields()
                            if field not in record_fixed.get(name,{}))
        else: external.append((name,'out' if direction=='buffer' else direction,kind))
    top = f'psx_mister_{entity}_resource'
    header = ('-- Component instantiation of GPL-2.0 MiSTer PSX sources; preserve upstream notices.\n'
              '-- Generated area target; virtual interfaces, no APF or deployment bitstream.\n'
              'library ieee;\nuse ieee.std_logic_1164.all;\nuse ieee.numeric_std.all;\n')
    if records: header+='use work.pJoypad.all;\n'
    text = header + f'entity {top} is\nport (\n'
    text += ';\n'.join(f'    {name} : {direction} {kind}' for name,direction,kind in external)
    text += f'\n);\nend entity;\narchitecture area of {top} is\n'
    text += ''.join(f'    signal {name} : joypad_t;\n' for name in records)+'begin\n'
    for name in records:
        text += ''.join(f'    {name}.{field} <= {record_fixed.get(name,{}).get(field,name+"_"+field)};\n'
                        for field,_ in joypad_fields())
    text += f'    component_core : entity work.{entity}\n    port map (\n'
    original = (UPSTREAM/'rtl'/f'{entity}.vhd').read_text().split('end entity;')[0]
    simulation_ports = [name for name in re.findall(r'^\s*(\w+)\s*:\s*out\b',original,re.M|re.I)
                        if name not in {name for name,_,_ in declarations}]
    if simulation_ports:
        text += '        -- synthesis translate_off\n'
        text += ''.join(f'        {name} => open,\n' for name in simulation_ports)
        text += '        -- synthesis translate_on\n'
    text += ',\n'.join(f'        {name} => {fixed.get(name,name)}' for name,_,_ in declarations)
    text += '\n    );\nend architecture;\n'
    return top, external, text


def write_project(directory,top,external,clocks,sources,sdc):
    qsf = ['set_global_assignment -name FAMILY "Cyclone V"',
           'set_global_assignment -name DEVICE 5CEBA4F23C8',
           f'set_global_assignment -name TOP_LEVEL_ENTITY {top}',
           'set_global_assignment -name VHDL_INPUT_VERSION VHDL_2008',
           'set_global_assignment -name SEED 1','set_global_assignment -name NUM_PARALLEL_PROCESSORS 4',
           'set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files',
           f'set_global_assignment -name SDC_FILE {top}.sdc']
    for name,_,kind in external:
        if name in clocks:
            continue
        width = re.search(r'\((\d+)\s+downto\s+(\d+)\)',kind,re.I)
        pins = [f'{name}[{bit}]' for bit in range(int(width[2]),int(width[1])+1)] if width else [name]
        for pin in pins:
            qsf.append(f'set_instance_assignment -name VIRTUAL_PIN ON -to "{pin}"')
    for source in sources:
        path = source['path'].replace('\\','/').replace('"','\\"')
        kind=source.get('assignment','VHDL_FILE')
        qsf.append(f'set_global_assignment -name {kind} "{path}" -library {source["library"]}')
    (directory/(top+'.qsf')).write_text('\n'.join(qsf)+'\n')
    (directory/(top+'.qpf')).write_text(f'PROJECT_REVISION = "{top}"\n')
    (directory/(top+'.sdc')).write_text('\n'.join(sdc)+'\n')
    return {suffix:digest(directory/(top+suffix)) for suffix in ('.qsf','.qpf','.sdc')}


def prepare_cluster(output_dir):
    """Five independent components with shared clocks; no CPU/device integration."""
    result=prepare(output_dir,list(CLUSTER_COMPONENTS))
    directory=output_dir.resolve()/'cluster'; directory.mkdir(exist_ok=True)
    top='psx_mister_cluster_resource'; clocks=('clk1x','clk2x','clk3x','clkvid')
    external=[(name,'in','std_logic') for name in clocks]
    sources=[]; mappings={}
    for entity,target in result['targets'].items():
        _,component_ports,_ = wrapper(entity,TARGETS[entity][0])
        mappings[entity]=[]
        for name,direction,kind in component_ports:
            if name in clocks:
                mappings[entity].append((name,name))
            else:
                port=entity+'_'+name; external.append((port,direction,kind))
                mappings[entity].append((name,port))
        for source in target['quartus_sources']:
            if not any((source['path'],source['library'])==(s['path'],s['library']) for s in sources):
                sources.append(source)
    text=('-- Generated GPL-2.0 MiSTer component cluster; preserve upstream notices.\n'
          '-- Resource envelope only: components have independent external data interfaces.\n'
          'library ieee;\nuse ieee.std_logic_1164.all;\nuse ieee.numeric_std.all;\n'
          f'entity {top} is\nport (\n')
    text+=';\n'.join(f'    {name} : {direction} {kind}' for name,direction,kind in external)
    text+=f'\n);\nend entity;\narchitecture area of {top} is\nbegin\n'
    for entity,mapping in mappings.items():
        text+=f'    {entity}_block : entity work.psx_mister_{entity}_resource\n    port map (\n'
        text+=',\n'.join(f'        {name} => {port}' for name,port in mapping)+'\n    );\n'
    text+='end architecture;\n'
    path=directory/(top+'.vhd'); path.write_text(text)
    sources.append({'path':str(path),'library':'work','sha256':digest(path)})
    sdc=[line for line in (output_dir/'cpu/psx_mister_cpu_resource.sdc').read_text().splitlines()
         if line!='derive_clock_uncertainty']
    sdc += ['create_clock -name gpu_video_clk -period 18.62434 [get_ports clkvid]',
            'set_clock_groups -asynchronous -group [get_clocks cpu_clk*] -group [get_clocks gpu_video_clk]',
            'derive_clock_uncertainty']
    hashes=write_project(directory,top,external,clocks,sources,sdc)
    members=result['targets']
    result['targets']={'cluster':{'top':top,'directory':str(directory),
        'fixed_ports':{name:target['fixed_ports'] for name,target in members.items()},
        'external_ports':[name for name,_,_ in external],'physical_clock_ports':list(clocks),
        'quartus_sources':sources,'project_sha256':hashes,'upstream_license':str(UPSTREAM/'LICENSE'),
        'included_components':list(CLUSTER_COMPONENTS),'components_connected':False}}
    (output_dir/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def prepare(output_dir, targets=None):
    targets = list(COMPONENT_NAMES) if targets is None else list(targets)
    if targets==['laxer_gte']:
        if __package__:
            from .prepare_laxer_gte_target import prepare_laxer_gte
        else:
            from prepare_laxer_gte_target import prepare_laxer_gte
        return prepare_laxer_gte(output_dir)
    if targets==['platform']:
        if __package__:
            from .prepare_platform_target import prepare_platform
        else:
            from prepare_platform_target import prepare_platform
        return prepare_platform(output_dir)
    if targets==['cluster']:
        return prepare_cluster(output_dir)
    if not targets or len(targets)!=len(set(targets)) or set(targets)-set(TARGETS):
        raise ValueError('Select distinct, known component targets')
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True,exist_ok=True)
    actual = subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip()
    if actual != PIN: raise ValueError(f'Reference pin changed: {actual}')
    if subprocess.run(['git','-C',str(UPSTREAM),'diff','--quiet','HEAD']).returncode:
        raise ValueError('MiSTer reference has tracked modifications; restore its pinned sources first.')
    result = {'device':'5CEBA4F23C8', 'target_games':['Final Fantasy VII','Final Fantasy VIII','Final Fantasy IX'],
              'upstream_pin':PIN, 'full_core':False, 'hardware_fit_verified':False,
              'excluded_components':([] if 'psx_top' in targets else
                  [name.upper() for name in COMPONENT_NAMES if name not in targets])+
                  ['APF','external memory controllers','host sector loading','host save persistence',
                   'Pocket video/audio interfaces','board clocks/resets'],
              'targets':{}}
    for entity in targets:
        fixed,files,memory,clocks = TARGETS[entity]
        directory = output_dir/entity; directory.mkdir(exist_ok=True)
        top,external,text = wrapper(entity,fixed)
        wrapper_path = directory/(top+'.vhd'); wrapper_path.write_text(text)
        sources = [{'path':str(UPSTREAM/'rtl'/name),'library':library} for library,name in memory]
        sources += [{'path':str(UPSTREAM/'rtl'/name), 'library':'work'} for name in files]
        for source in sources: source['sha256'] = digest(Path(source['path']))
        sources.append({'path':str(wrapper_path.resolve()),'library':'work','sha256':digest(wrapper_path)})
        # Internal clocks only: no physical PLL, pin or external interface timing proof.
        sdc = ['# Component clock targets only; not a board SDC.']
        if 'clk1x' in clocks:
            sdc.append('create_clock -name cpu_clk -period 29.5251 [get_ports clk1x]')
            if 'clk2x' in clocks:
                sdc.append('create_generated_clock -name cpu_clk2x -source [get_ports clk1x] -multiply_by 2 [get_ports clk2x]')
            if 'clk3x' in clocks:
                sdc.append('create_generated_clock -name cpu_clk3x -source [get_ports clk1x] -multiply_by 3 [get_ports clk3x]')
            if 'clkvid' in clocks:
                sdc += ['create_clock -name gpu_video_clk -period 18.62434 [get_ports clkvid]',
                        '# Independent video PLL domain, matching upstream PSX.sdc intent; CDC not verified.',
                        'set_clock_groups -asynchronous -group [get_clocks cpu_clk*] -group [get_clocks gpu_video_clk]']
        else:
            # GTE and the standalone card loader only retain the 2x clock.
            sdc.append('create_clock -name gte_clk2x -period 14.76255 [get_ports clk2x]')
        sdc.append('derive_clock_uncertainty')
        hashes=write_project(directory,top,external,clocks,sources,sdc)
        result['targets'][entity] = {'top':top,'directory':str(directory.resolve()),'fixed_ports':fixed,
                                    'external_ports':[name for name,_,_ in external],
                                    'physical_clock_ports':list(clocks),
                                    'quartus_sources':sources,
                                    'project_sha256':hashes,
                                    'upstream_license':str(UPSTREAM/'LICENSE')}
        if entity=='psx_top':
            result['targets'][entity].update(included_components=list(COMPONENT_NAMES),components_connected=True)
    (output_dir/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def apply_compile_profile(manifest, profile):
    """Change only compiler settings; preserve HDL, interfaces and clock constraints."""
    if 'compile_profile' in manifest:
        raise ValueError('Apply compiler profiles only to a newly prepared project')
    settings=COMPILE_PROFILES[profile]
    # These are controlled compiler constants. Quote multiword values so Tcl
    # passes them as one assignment value rather than extra command arguments.
    assignments=''.join(f'set_global_assignment -name {name} '
                        + (f'"{value}"' if ' ' in value else value) + '\n'
                        for name,value in settings.items())
    manifest['compile_profile']=profile
    manifest['compile_settings']=settings
    for target in manifest['targets'].values():
        path=Path(target['directory'])/(target['top']+'.qsf')
        with path.open('a') as output:
            output.write(assignments)
        target['project_sha256']['.qsf']=digest(path)
        target['compile_profile']=profile
        target['compile_settings']=settings
    directory=Path(next(iter(manifest['targets'].values()))['directory']).parent
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def apply_reset_only(manifest):
    """Candidate specialization with an explicit disabled-control contract."""
    if set(manifest['targets'])!={'psx_top'}:
        raise ValueError('Reset-only specialization requires the connected PSX target alone')
    target=manifest['targets']['psx_top']
    for name in ('save_state','load_state','rewind_on','rewind_active','loadExe'):
        if target['fixed_ports'].get(name)!="'0'":
            raise ValueError('Reset-only control contract not satisfied: '+name)
    if __package__:
        from .prepare_reset_only import reset_only_source
    else:
        from prepare_reset_only import reset_only_source
    directory=Path(target['directory']); copy=reset_only_source(directory/'reset-only')
    changed=0
    for i,source in enumerate(target['quartus_sources']):
        if source['path']==str(UPSTREAM/'rtl/savestates.vhd'):
            target['quartus_sources'][i]=copy; changed+=1
    if changed!=1: raise ValueError('Expected exactly one savestate engine source')
    qsf=directory/(target['top']+'.qsf'); text=qsf.read_text()
    original=str(UPSTREAM/'rtl/savestates.vhd')
    if text.count(original)!=1: raise ValueError('Expected exactly one savestate QSF assignment')
    qsf.write_text(text.replace(original,copy['path']))
    target['project_sha256']['.qsf']=digest(qsf)
    target['reset_only_specialization']=copy
    manifest['reset_only_specialization']=True
    (directory.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'build/resources')
    parser.add_argument('--targets',nargs='+',choices=TARGET_NAMES,help='Components to prepare; cluster must be selected alone')
    parser.add_argument('--profile',choices=COMPILE_PROFILES,default='baseline',help='Compiler settings; no RTL changes')
    parser.add_argument('--reset-only',action='store_true',help='Candidate reset-mode specialization; psx_top alone')
    args=parser.parse_args(); result=prepare(args.output,args.targets)
    if args.reset_only: result=apply_reset_only(result)
    result=apply_compile_profile(result,args.profile)
    for key,target in result['targets'].items(): print(f'{key}: {target["directory"]}')
    print('Component area targets only; full FF core fit and timing remain unverified.')


if __name__=='__main__': main()
