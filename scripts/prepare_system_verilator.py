"""Generate an audited, simulation-only Verilog copy of the connected PSX RTL."""
import json
from pathlib import Path
import re
import shutil

if __package__:
    from .prepare_cd_arithmetic import cd_arithmetic_source
    from .prepare_no_turbo_cache import no_turbo_cache_source
    from .prepare_pocket_controls import apply_pocket_controls
    from .prepare_resource_targets import UPSTREAM,digest,hardware_text,prepare as prepare_resources
    from .prepare_system_sim import compatible_cd_source,compatible_async_video_source
else:
    from prepare_cd_arithmetic import cd_arithmetic_source
    from prepare_no_turbo_cache import no_turbo_cache_source
    from prepare_pocket_controls import apply_pocket_controls
    from prepare_resource_targets import UPSTREAM,digest,hardware_text,prepare as prepare_resources
    from prepare_system_sim import compatible_cd_source,compatible_async_video_source


def split_divider_ports(text):
    """Keep the record layout, but separate each divider's driven/read fields."""
    for i in range(1,7):
        text,count=re.subn(rf'      div{i}\s*: inout div_type;',
            f'      div{i}_in : in div_type;\n      div{i}_out : out div_type;',text)
        if count!=1:raise ValueError('Pinned GPU divider interface changed')
        for field in ('start','dividend','divisor'):text=text.replace(f'div{i}.{field}',f'div{i}_out.{field}')
        for field in ('done','quotient','remainder'):text=text.replace(f'div{i}.{field}',f'div{i}_in.{field}')
    assignments='\n'.join(f"   div{i}_out.done <= '0'; div{i}_out.quotient <= (others=>'0'); div{i}_out.remainder <= (others=>'0');" for i in range(1,7))
    marker='begin \n'
    if text.count(marker)!=1:raise ValueError('Pinned GPU architecture marker changed')
    return text.replace(marker,marker+assignments+'\n')


def gpu_transcode_source(name,text,gpu_timing_prune=False):
    if name=='gpu_poly':
        if text.count('abs(')!=(4 if gpu_timing_prune else 9) or text.count('x"100000000"')!=3:raise ValueError('Pinned polygon arithmetic changed')
        text=text.replace('abs(','magnitude(')
        function='''   function magnitude(value : integer) return integer is
   begin
      if value < 0 then return -value; else return value; end if;
   end function;

'''
        text=text.replace('begin \n',function+'begin \n',1)
        text=text.replace('x"100000000"','signed\'(\"000000000\" & x\"100000000\")')
        # GHDL synthesis truncates a negative integer compared with an
        # unsigned (span start -40 > right edge 319 becomes true), so a
        # polygon span starting left of x=0 was skipped. Quartus compares it
        # as a signed value (checked on a fitted netlist; docs/gpu-replay.md).
        marker='to_integer(xStart(43 downto 32)) > drawingAreaRight'
        if text.count(marker)!=1:raise ValueError('Pinned polygon span-start comparison changed')
        text=text.replace(marker,'xStart(43 downto 32) > signed(resize(drawingAreaRight, 12))')
    if name in ('gpu_line','gpu_poly'):return split_divider_ports(text)
    if name=='gpu':
        marker='   type t_div_array is array(0 to 5) of div_type;'
        if text.count(marker)!=1:raise ValueError('Pinned GPU divider array changed')
        text=text.replace(marker,marker+'\n   signal line_div_out,poly_div_out : t_div_array;')
        for kind in ('line','poly'):
            for i in range(1,7):
                text,count=re.subn(rf'      div{i}\s*=> {kind}_div\({i-1}\),',
                    f'      div{i}_in => {kind}_div({i-1}),\n      div{i}_out => {kind}_div_out({i-1}),',text)
                if count!=1:raise ValueError('Pinned GPU divider association changed')
            for field in ('start','dividend','divisor'):
                marker=f'      div_array(i).{field}';index=text.index(marker)
                text=text[:index]+f'      {kind}_div(i).{field} <= {kind}_div_out(i).{field};\n'+text[index:]
    return text


def joypad_transcode_source(text):
    marker='''                           receiveBuffer   <= portStates(portNr).dsRumbleConfig(((8 * rumblecount) + 7) downto (8 * rumblecount));
                           portStates(portNr).dsRumbleConfig(((8 * rumblecount) + 7) downto (8 * rumblecount)) <= transmitValue;'''
    if text.count(marker)!=1:raise ValueError('Pinned joypad byte selection changed')
    return text.replace(marker,'''                           for selected in 0 to 1 loop
                              for byte in 0 to 5 loop
                                 if portNr=selected and rumblecount=byte then
                                    receiveBuffer <= portStates(selected).dsRumbleConfig((8*byte+7) downto (8*byte));
                                    portStates(selected).dsRumbleConfig((8*byte+7) downto (8*byte)) <= transmitValue;
                                 end if;
                              end loop;
                           end loop;''')


def prepare(directory,fast_reset=True,gpu_line_share=False,fast_boot=False,gpu_divider_pool=False,gpu_timing_prune=False):
    # Do not modify upstream entities or the resource-fit projects.
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    manifest=prepare_resources(directory/'project',['psx_top'])
    apply_pocket_controls(manifest)
    cpu=no_turbo_cache_source(directory/'cpu-adapted')
    cd=cd_arithmetic_source(directory/'cd-adapted');cd_compat=compatible_cd_source(cd,directory)
    video=compatible_async_video_source(directory)
    substitutions={'cpu.vhd':cpu['path'],'cd_top.vhd':cd_compat['path'],'gpu_videoout_async.vhd':video['path']}
    gpu_adaptation=None
    if gpu_line_share:
        if __package__: from .prepare_gpu_line_share import shared_line_source
        else: from prepare_gpu_line_share import shared_line_source
        gpu_adaptation=shared_line_source(directory/'gpu-adapted')
        substitutions['gpu_poly.vhd']=gpu_adaptation['path']
    timing_adaptation=None
    if gpu_timing_prune:
        if __package__: from .prepare_gpu_timing import pruned_timing_source,pruned_auxiliary_source,shortened_wrap_source,AUXILIARY_TIMING
        else: from prepare_gpu_timing import pruned_timing_source,pruned_auxiliary_source,shortened_wrap_source,AUXILIARY_TIMING
        timing_adaptation=pruned_timing_source(directory/'gpu-timing-prune',substitutions.get('gpu_poly.vhd'))
        substitutions['gpu_poly.vhd']=timing_adaptation['path']
        timing_adaptation['auxiliary_sources']=[]
        for name in AUXILIARY_TIMING:
            extra=pruned_auxiliary_source(name,directory/'gpu-timing-prune'/name)
            substitutions[name+'.vhd']=extra['path']
            timing_adaptation['auxiliary_sources'].append(extra)
    result=[];seen=set()
    pool_adaptation=None
    if gpu_divider_pool:
        if __package__: from .prepare_gpu_divider_pool import pooled_gpu_source
        else: from prepare_gpu_divider_pool import pooled_gpu_source
        pool_adaptation=pooled_gpu_source(directory/'gpu-divider-pool')
        substitutions['gpu.vhd']=pool_adaptation['path']
    if gpu_timing_prune:
        wrapped=shortened_wrap_source(directory/'gpu-timing-prune'/'read-wrap',substitutions.get('gpu.vhd'))
        substitutions['gpu.vhd']=wrapped['path']
        timing_adaptation['read_wrap']=wrapped
    # RAM entities are opaque to GHDL synthesis. SV implements their pinned
    # behavioral semantics, including mixed widths and write-first readback.
    for basename in ('RamMLAB.vhd','dpram.vhd'):
        original=UPSTREAM/'sim/system/src/mem'/basename
        copied=directory/'hardware/ramip'/basename;copied.parent.mkdir(parents=True,exist_ok=True)
        copied.write_bytes(original.read_bytes())
        result.append({'path':str(copied),'library':'ramip','sha256':digest(copied),
            'original':str(original),'original_sha256':digest(original),'transforms':[]})
    for source in manifest['targets']['psx_top']['quartus_sources']:
        original=Path(source['path']);name=original.name;library=source['library']
        if name in ('RamMLAB.vhd','dpram.vhd'):continue
        key=(library,name)
        if key in seen:continue
        seen.add(key);selected=Path(substitutions.get(name,original))
        text=hardware_text(selected.read_text());changes=['Omit synthesis translate-off debug sections']
        if name=='export.vhd':
            text=text.split('end package;')[0]
            marker='   signal regs                   : tExportRegs;'
            if text.count(marker)!=1:raise ValueError('Pinned debug export package changed')
            text=text.replace(marker,'')+'end package;\n';changes.append('Omit debug-only package signal')
        if name=='spu_ram.vhd':
            marker='LIBRARY altera_mf;\nUSE altera_mf.altera_mf_components.all;'
            if text.count(marker)!=1:raise ValueError('Pinned unused SPU vendor context changed')
            text=text.replace(marker,'');changes.append('Omit unused vendor context')
        if name=='psx_top.vhd':
            text,count=re.subn(r'(savestate_number\s*=> )savestate_number,',r"\1integer'(savestate_number),",text)
            if count!=2:raise ValueError('Pinned system integer associations changed')
            changes.append('Explicit integer qualification at two scalar associations')
            # This is a combinational alias of savestates' constant x"01"
            # output. GHDL preserves its initializer as a second Verilog
            # procedural driver; Verilator 5.053 --no-timing can keep zero
            # instead of the port value, losing every reset header reply.
            marker="signal ss_ram_BURSTCNT        : std_logic_vector(7 downto 0) := (others => '0');"
            if text.count(marker)!=1:raise ValueError('Pinned reset header burst alias changed')
            text=text.replace(marker,"signal ss_ram_BURSTCNT        : std_logic_vector(7 downto 0);")
            changes.append('Omit simulation-only initializer on constant reset-header burst port alias')
        if name=='gpu_videoout.vhd':
            for expression,count in {'to_integer(videoout_request_clkvid.lineDisp)':9,
                                     'to_integer(Gun1Y_screen)':2,'to_integer(Gun2Y_screen)':2}.items():
                if text.count(expression)!=count:raise ValueError('Pinned GPU scalar associations changed')
                text=text.replace(expression,"integer'("+expression+")")
            text,count=re.subn(r'(i_pixel_out_x\s*=> )videoout_request_clkvid.xpos,',
                r"\1integer'(videoout_request_clkvid.xpos),",text)
            if count!=5:raise ValueError('Pinned GPU overlay association changed')
            changes.append('Explicit integer qualification at eighteen scalar associations')
        if name in ('gpu_line.vhd','gpu_poly.vhd','gpu.vhd'):
            text=gpu_transcode_source(original.stem,text,gpu_timing_prune)
            changes.append('Separate divider request/result record directions; explicit polygon constant width and integer magnitude')
        if name=='joypad_pad.vhd':
            text=joypad_transcode_source(text);changes.append('Statically select the same controller rumble byte')
        text,count=re.subn(r'entity (?:work|mem)\.(RamMLAB|dpram(?:_dif)?)\b',r'entity ramip.\1',text,flags=re.I)
        if count:text='library ramip;\n'+text;changes.append('Reference opaque behavioral RAM entities')
        if original.stem=='psx_mister_psx_top_resource' and fast_reset:
            marker='component_core : entity work.psx_top\n'
            if text.count(marker)!=1:raise ValueError('Pinned wrapper instance marker changed')
            text=text.replace(marker,"component_core : entity work.psx_top generic map(is_simu=>'1')\n")
            changes.append('FASTSIM scratch-only reset; external memory initially zero')
        if original.stem=='psx_mister_psx_top_resource' and fast_boot:
            marker="fastboot => '0'"
            if text.count(marker)!=1:raise ValueError('Pinned fastboot control changed')
            text=text.replace(marker,"fastboot => '1'")
            changes.append('Enable upstream BIOS read-time logo skip; private BIOS file unchanged')
        copied=directory/'hardware'/library/name;copied.parent.mkdir(parents=True,exist_ok=True);copied.write_text(text)
        if name=='gpu.vhd' and timing_adaptation:
            result.append({**timing_adaptation['read_wrap']['package'],
                           'transforms':['Bounded VRAM read-wrap arithmetic package']})
        if name=='gpu.vhd' and pool_adaptation:
            scheduler=pool_adaptation['scheduler']
            result.append({**scheduler,'transforms':['Original divider scheduler; serialized GPU setup latency']})
        result.append({'path':str(copied),'library':library,'sha256':digest(copied),
            'original':str(original),'original_sha256':digest(original),'selected':str(selected),
            'selected_sha256':digest(selected),'transforms':changes})
    shutil.copy2(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    report={'sources':result,'cpu':cpu,'cd':cd,'cd_compatibility':cd_compat,
        'video_compatibility':video,'gpu_line_share':gpu_adaptation,'gpu_divider_pool':pool_adaptation,
        'gpu_timing_prune':timing_adaptation,'fast_reset':fast_reset,'fast_boot':fast_boot,'gte':'Unmodified MiSTer arithmetic',
        'reset_header_constant_alias':1,
        'production_reference_modified':False,'hardware_verified':False,'game_verified':False}
    (directory/'source-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
