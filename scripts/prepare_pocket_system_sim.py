"""Private mixed-language simulation copies of the measured Pocket candidate.

Canonical source selection comes from the physical-budget builder. Only the
existing GHDL compatibility rewrites and an opaque GTE entity are added here.
No reference or running simulation input is edited.
"""
import json
from pathlib import Path
import re
import shutil

from prepare_full_pocket import prepare_system, VARIANTS
from prepare_resource_targets import UPSTREAM, digest, hardware_text
from prepare_system_sim import compatible_cd_source, compatible_async_video_source
from prepare_system_verilator import gpu_transcode_source, joypad_transcode_source

CONFIGURATION = 'pocket-reset-tables-one-digital-pad-v1'
SELECTED = VARIANTS + (
    'dma-channel-ram', 'spu-reverb-table')


def prepare(directory, fast_reset=True, fast_boot=False):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    canonical = prepare_system(directory/'canonical', SELECTED, True, 'area-min-state', False)
    target = canonical['targets']['psx_top']
    originals = target['quartus_sources']
    result = []
    sv_sources = []
    seen = set()
    for basename in ('RamMLAB.vhd', 'dpram.vhd'):
        original = UPSTREAM/'sim/system/src/mem'/basename
        path = directory/'hardware/ramip'/basename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original.read_bytes())
        result.append({'path':str(path), 'library':'ramip', 'sha256':digest(path),
            'original':str(original), 'original_sha256':digest(original), 'transforms':[]})
    cd = next(s for s in originals if Path(s['path']).name == 'cd_top.vhd')
    cd_compat = compatible_cd_source(cd, directory)
    video_compat = compatible_async_video_source(directory)
    substitutions = {'cd_top.vhd':cd_compat['path'], 'gpu_videoout_async.vhd':video_compat['path']}
    for source in originals:
        original = Path(source['path'])
        if original.suffix.lower() != '.vhd':
            # Snapshot includes too, so a build never relies on a changing
            # external include directory after compilation begins.
            path = directory/'hardware/gte-sv'/original.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original.read_bytes())
            sv_sources.append({**source, 'path':str(path), 'sha256':digest(path),
                'original':str(original), 'original_sha256':digest(original)})
            continue
        name, library = original.name, source['library']
        if name in ('RamMLAB.vhd', 'dpram.vhd') or (library,name) in seen:
            continue
        seen.add((library,name))
        selected = Path(substitutions.get(name, original))
        text = hardware_text(selected.read_text())
        changes = ['Omit synthesis translate-off debug sections']
        if name == 'export.vhd':
            text = text.split('end package;')[0]
            marker = '   signal regs                   : tExportRegs;'
            if text.count(marker) != 1: raise ValueError('Export package changed')
            text = text.replace(marker, '')+'end package;\n'
            changes.append('Omit debug-only package signal')
        if name == 'spu_ram.vhd':
            marker = 'LIBRARY altera_mf;\nUSE altera_mf.altera_mf_components.all;'
            if text.count(marker) != 1: raise ValueError('SPU vendor context changed')
            text = text.replace(marker, '')
            changes.append('Omit unused vendor context')
        if name == 'psx_top.vhd':
            text,count = re.subn(r'(savestate_number\s*=> )savestate_number,',
                r"\1integer'(savestate_number),", text)
            if count != 2: raise ValueError('System integer associations changed')
            changes.append('Explicit scalar association qualification')
            # Same simulation-only correction as the reference converter:
            # this alias is driven by savestates' constant x01 output. GHDL
            # otherwise emits a second initialized driver that Verilator
            # --no-timing can keep at zero, losing reset-header responses.
            marker="signal ss_ram_BURSTCNT        : std_logic_vector(7 downto 0) := (others => '0');"
            if text.count(marker)!=1:
                raise ValueError('Candidate reset-header burst alias changed')
            text=text.replace(marker,"signal ss_ram_BURSTCNT        : std_logic_vector(7 downto 0);")
            changes.append('Omit simulation-only initializer on constant reset-header burst port alias')
        if name == 'gpu_videoout.vhd':
            for expression,count in {'to_integer(videoout_request_clkvid.lineDisp)':9,
                'to_integer(Gun1Y_screen)':2, 'to_integer(Gun2Y_screen)':2}.items():
                if text.count(expression) != count: raise ValueError('Video scalar associations changed')
                text = text.replace(expression, "integer'("+expression+")")
            text,count = re.subn(r'(i_pixel_out_x\s*=> )videoout_request_clkvid.xpos,',
                r"\1integer'(videoout_request_clkvid.xpos),", text)
            if count != 5: raise ValueError('Overlay scalar associations changed')
            changes.append('Explicit video/overlay scalar qualification')
        if name in ('gpu_line.vhd','gpu_poly.vhd','gpu.vhd'):
            text = gpu_transcode_source(original.stem, text, True)
            changes.append('Split divider record directions; explicit constant width and integer magnitude')
        if name == 'joypad_pad.vhd':
            text = joypad_transcode_source(text)
            changes.append('Statically select the identical rumble byte')
        if name == 'gte.vhd':
            match = re.search(r'    component (\w+) is\n(.*?)    end component;', text, re.S)
            if not match or match[1] != 'psx_laxer_mister_adapter':
                raise ValueError('GTE component contract changed')
            entity = directory/'hardware/gteip'/('gte_entity.vhd')
            entity.parent.mkdir(parents=True, exist_ok=True)
            entity.write_text('library ieee; use ieee.std_logic_1164.all;\nentity '+match[1]+
                ' is\n'+match[2]+'end entity;\narchitecture opaque of '+match[1]+' is begin end;\n')
            result.append({'path':str(entity), 'library':'gteip', 'sha256':digest(entity),
                'transforms':['Opaque entity with exactly the selected VHDL/SV interface']})
            text = 'library gteip;\n'+text
            text = text.replace(match[0], match[0]+'\n    for engine : '+match[1]+
                ' use entity gteip.'+match[1]+';')
            changes.append('Bind unchanged selected SV GTE through opaque vendor entity')
        text,count = re.subn(r'entity (?:work|mem)\.(RamMLAB|dpram(?:_dif)?)\b',
            r'entity ramip.\1', text, flags=re.I)
        if count:
            text = 'library ramip;\n'+text
            changes.append('Opaque checked RAM-model entity binding')
        if original.stem == target['top']:
            if fast_reset:
                marker = 'component_core : entity work.psx_top\n'
                if text.count(marker) != 1: raise ValueError('Wrapper instance changed')
                text = text.replace(marker,"component_core : entity work.psx_top generic map(is_simu=>'1')\n")
                changes.append('FASTSIM scratch-only reset for authored diagnostic')
            if fast_boot:
                marker = "fastboot => '0'"
                if text.count(marker) != 1: raise ValueError('Fastboot control changed')
                text = text.replace(marker,"fastboot => '1'")
                changes.append('Original BIOS read-time logo-skip patch')
        path = directory/'hardware'/library/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        result.append({'path':str(path), 'library':library, 'sha256':digest(path),
            'original':str(original), 'original_sha256':digest(original),
            'selected':str(selected), 'selected_sha256':digest(selected), 'transforms':changes})
    shutil.copy2(UPSTREAM/'LICENSE', directory/'COPYING.GPL2')
    report = {'configuration':CONFIGURATION, 'variants':list(SELECTED),
        'canonical_manifest':canonical, 'sources':result, 'sv_sources':sv_sources,
        'fast_reset':fast_reset, 'fast_boot':fast_boot, 'reset_header_constant_alias':1,
        'controller_configuration':'One digital pad; both guest memory-card paths retained',
        'all_original_controller_records':False, 'save_adapter_extensions':False,
        'production_reference_modified':False, 'game_verified':False, 'hardware_verified':False}
    (directory/'source-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
