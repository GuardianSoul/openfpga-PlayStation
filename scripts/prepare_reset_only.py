"""Bounded savestate specialization for systems with save/load/EXE controls off."""
import re
import shutil
from pathlib import Path

if __package__:
    from .prepare_resource_targets import UPSTREAM, digest
else:
    from prepare_resource_targets import UPSTREAM, digest


def reset_only_source(directory):
    original=UPSTREAM/'rtl/savestates.vhd'
    text=original.read_text()
    # With save/load/EXE requests tied off, these modes cannot change after the
    # first reset. Preserve every original reset state, handshake and clear loop.
    replacements={'resetMode':("'1'",3),'savemode':("'0'",3),'exeMode':("'0'",2)}
    for name,(value,writes) in replacements.items():
        text,count=re.subn(r'\bsignal '+name+r'\s*:\s*std_logic\s*:=\s*\x270\x27;',
                           f'constant {name} : std_logic := {value};',text)
        if count!=1: raise ValueError(f'Pinned mode declaration changed: {name}')
        text,count=re.subn(r'^\s*'+name+r'\s*<=\s*\x27[01]\x27;\s*$', '',text,flags=re.M)
        if count!=writes: raise ValueError(f'Pinned mode assignments changed: {name}: {count}')
    # Retaining unreachable SAVE arms can keep register-readback logic alive
    # in the connected Quartus target.
    # Preserve every LOAD/reset arm and the original startup signal values.
    start=text.index('            when SAVE_WAITSETTLE =>')
    end=text.index('            when LOAD_WAITSETTLE =>',start)
    saving=text[start:end]
    save_states=re.findall(r'            when (SAVE\w+) =>',saving)
    if len(save_states)!=12 or saving.count("SS_rden_2x(savetype_counter) <= '1';")!=1:
        raise ValueError('Pinned save/readback state arms changed')
    forbidden=' | '.join(save_states)
    text=text[:start]+f'''            when {forbidden} =>
               -- synthesis translate_off
               assert false report "Reset-only sequencer entered an unreachable SAVE state" severity failure;
               -- synthesis translate_on
               null;

'''+text[end:]
    marker='begin \n\n   savestate_busy'
    assertion=("begin \n\n   -- synthesis translate_off\n"
        "   assert save = '0' and load = '0' and loadExe = '0'\n"
        "      report \"Reset-only specialization requires save/load/EXE requests off\" severity failure;\n"
        "   -- synthesis translate_on\n\n   savestate_busy")
    if text.count(marker)!=1: raise ValueError('Pinned savestate architecture changed')
    text=text.replace(marker,assertion)
    # Reset starts at save type 12, skips type 13, then clears SPU/VRAM through
    # their separate memory ports and main RAM through type 16. Other device
    # register write strobes are unreachable under the disabled-control contract.
    marker='            SS_wren      <= SS_wren_2x;'
    if text.count(marker)!=1: raise ValueError('Pinned savestate write forwarding changed')
    text=text.replace(marker,'''            -- Reset reaches only scratchpad and main-RAM register strobes.
            -- synthesis translate_off
            assert SAVETYPESCOUNT = 17 report "Reset write-mask probe requires 17 save types" severity failure;
            for field in SS_wren_2x'range loop
               if field /= 12 and field /= 16 then
                  assert SS_wren_2x(field) = '0'
                     report "Reset-only sequencer attempted a device-register restore" severity failure;
               end if;
            end loop;
            -- synthesis translate_on
            SS_wren(12) <= SS_wren_2x(12);
            SS_wren(16) <= SS_wren_2x(16);''')
    directory=Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=True)
    path=directory/'savestates.vhd'; path.write_text(text)
    shutil.copy2(UPSTREAM/'LICENSE',directory/'LICENSE')
    return {'path':str(path),'library':'work','sha256':digest(path),
        'original':str(original),'original_sha256':digest(original),
        'transform':'Fix resetMode=1, savemode=0, exeMode=0; omit unreachable SAVE/readback arms; retain original reset sequencer; expose only reset-reachable scratchpad/RAM write strobes',
        'removed_save_states':save_states,
        'reset_write_fields':[12,16],
        'required_generics':{'SAVETYPESCOUNT':17},
        'required_controls':{'save':0,'load':0,'loadExe':0},
        'scope':'Resource/simulation candidate only; no game or hardware validation'}
