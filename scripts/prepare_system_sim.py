"""Prepare portable full-system simulation sources without changing references."""
import json
from pathlib import Path
import re
import shutil

if __package__:
    from .prepare_resource_targets import UPSTREAM, digest
else:
    from prepare_resource_targets import UPSTREAM, digest


def prepare_memory_models(directory):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    root=UPSTREAM/'sim/system/src/tb'; result=[]
    original=root/'sdram_model3x.vhd'; text=original.read_text()
    text=text.replace('use IEEE.numeric_std.all;', 'use IEEE.numeric_std.all;\nuse STD.textio.all;',1)
    marker='      FILELOADING       : std_logic := \'0\''
    if text.count(marker)!=1: raise ValueError('SDRAM model generic changed')
    text=text.replace(marker,marker+''';
      MEMORY_BYTES      : positive := 16777216;
      INITADDR          : natural := 0''')
    marker='type t_data is array(0 to (2**27)-1) of integer;'
    if text.count(marker)!=1: raise ValueError('SDRAM model allocation changed')
    text=text.replace(marker,'type t_data is array(0 to MEMORY_BYTES-1) of integer;')
    marker='      file infile             : bit_vector_file;'
    if text.count(marker)!=1: raise ValueError('SDRAM model file declaration changed')
    text=text.replace(marker,marker+'''
      file boot_file          : text;
      variable boot_line      : line;
      variable boot_byte      : std_logic_vector(7 downto 0);''')
    pattern=r"(      if \(FILELOADING = '1'\) then\n).*?(      end if;\n   \n   \n   end process;)"
    replacement='''      if (FILELOADING = '1') then
         if (initFromFile = '1') then
            initFromFile <= '0';
            file_open(f_status, boot_file, INITFILE, read_mode);
            assert f_status=open_ok report "Cannot open local boot image" severity failure;
            targetpos := INITADDR;
            while not endfile(boot_file) loop
               assert targetpos<MEMORY_BYTES report "Boot image exceeds modeled memory" severity failure;
               readline(boot_file,boot_line); hread(boot_line,boot_byte);
               data(targetpos) := to_integer(unsigned(boot_byte));
               targetpos := targetpos+1;
            end loop;
            fileSize <= to_unsigned(targetpos-INITADDR,30);
            file_close(boot_file);
         end if;
      end if;


   end process;'''
    text,count=re.subn(pattern,replacement,text,flags=re.S)
    if count!=1: raise ValueError('SDRAM model preload block changed')
    path=directory/original.name; path.write_text(text)
    result.append({'path':str(path),'library':'tb','sha256':digest(path),'original':str(original),
        'original_sha256':digest(original),'transform':'Configurable byte-array bound; portable hex-byte preload at explicit offset',
        'clock_cache_dma_logic_unchanged':True,'script_loading_supported':False})
    original=root/'ddrram_model.vhd'; text=original.read_text()
    marker='type t_data is array(0 to (2**28)-1) of integer;'
    if text.count(marker)!=1: raise ValueError('DDR model allocation changed')
    # intern_addr uses only 23 address bits followed by zero. Add full maximum
    # burst padding to preserve access across the last addressed doubleword.
    text=text.replace(marker,'type t_data is array(0 to (2**24)+511) of integer;')
    path=directory/original.name; path.write_text(text)
    result.append({'path':str(path),'library':'tb','sha256':digest(path),'original':str(original),
        'original_sha256':digest(original),'transform':'Bound word-array to reachable intern_addr range plus maximum burst padding',
        'read_write_timing_and_masks_unchanged':True,'script_loading_supported':False})
    shutil.copy2(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    (directory/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def observed_system_source(directory):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    text=(UPSTREAM/'rtl/psx_top.vhd').read_text()
    text,count=re.subn(r'(savestate_number\s*=> )savestate_number,',
                       r"\1integer'(savestate_number),",text)
    if count!=2: raise ValueError('Pinned system integer associations changed')
    shutil.copy2(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    # Original 8-bit signed +128 wraps before a Natural conversion, which GHDL
    # rejects even for a disabled lightgun. Widen the addition; the resulting
    # eight output bits match the intended offset for every signed byte.
    for pad in (1,2):
        for axis in ('X','Y'):
            expression=f'to_integer(joypad{pad}.Analog1{axis} + 128)'
            if text.count(expression)!=1: raise ValueError('Pinned analog offset changed')
            text=text.replace(expression,f'to_integer(resize(joypad{pad}.Analog1{axis}, 9) + 128)')
    marker='      Cheats_BusDone        : in     std_logic\n'
    if text.count(marker)!=1: raise ValueError('System entity observation marker changed')
    text=text.replace(marker,marker+'''-- synthesis translate_off
      ; sim_cpu_done : out std_logic;
      sim_cpu_state : out cpu_export_type;
      sim_reset : out std_logic
-- synthesis translate_on
''')
    marker='begin \n   \n   -- reset'
    if text.count(marker)!=1: raise ValueError('System observation architecture marker changed')
    text=text.replace(marker,'''begin
   -- synthesis translate_off
   sim_cpu_done <= cpu_done;
   sim_cpu_state <= cpu_export;
   sim_reset <= reset_intern;
   -- synthesis translate_on

   -- reset''')
    marker="gexport : if is_simu = '1' generate"
    if text.count(marker)!=1: raise ValueError('Legacy debug export marker changed')
    text=text.replace(marker,'gexport : if false generate')
    path=directory/'psx_top_observed.vhd'; path.write_text(text)
    return {'path':str(path),'library':'work','sha256':digest(path),
        'original':str(UPSTREAM/'rtl/psx_top.vhd'),'original_sha256':digest(UPSTREAM/'rtl/psx_top.vhd'),
        'transform':'GHDL scalar/analog conversion compatibility; simulation-only CPU observations; omit legacy file logger',
        'analog_offset_output_bits_preserved':True,'production_reference_modified':False}


def compatible_cd_source(source, directory):
    original=Path(source['path']); text=original.read_text()
    marker='std_logic_vector(to_unsigned(positionInIndex, 20))'
    if text.count(marker)!=1: raise ValueError('Pinned CD position encoding changed')
    # The field is restored as signed and its range includes the negative
    # pregap. Preserve its 20-bit two's-complement encoding without passing a
    # negative integer to Natural, which GHDL rejects during startup.
    text=text.replace(marker,'std_logic_vector(to_signed(positionInIndex, 20))')
    path=Path(directory)/'cd_top_compatible.vhd'; path.write_text(text)
    return {'path':str(path),'library':'work','sha256':digest(path),'original':str(original),
        'original_sha256':digest(original),'transform':'Explicit signed 20-bit pregap position encoding',
        'production_reference_modified':False}


def compatible_async_video_source(directory):
    original=UPSTREAM/'rtl/gpu_videoout_async.vhd'; text=original.read_text()
    for expression,count in (('to_unsigned((lineMax - 1), 9)',3),('to_unsigned((lineMax - 2), 9)',1)):
        if text.count(expression)!=count: raise ValueError('Pinned line counter conversion changed')
        value='lineMax - 1' if '- 1' in expression else 'lineMax - 2'
        text=text.replace(expression,f'resize(unsigned(to_signed({value}, 10)), 9)')
    values=('((vdispNew - vDisplayStart) * 2) + 1','(vdispNew - vDisplayStart) * 2',
            'vdispNew - vDisplayStart')
    for value in values:
        expression=f'to_unsigned({value}, 9)'
        if text.count(expression)!=3: raise ValueError('Pinned display line offset conversion changed')
        text=text.replace(expression,f'resize(unsigned(to_signed({value}, 12)), 9)')
    path=Path(directory)/original.name; path.write_text(text)
    return {'path':str(path),'library':'work','sha256':digest(path),'original':str(original),
        'original_sha256':digest(original),'transform':'Preserve low nine line-counter/offset bits for blank/reset display ranges',
        'production_reference_modified':False}
