"""Keep MiSTer DMA channel address/count registers in small LUT RAMs.

Each channel's MADR (24 bits) and BCR (32 bits) leave flip-flops for
asynchronous-read MLAB tables: one guest bus read port and one trigger read
port, written on the same rising clk1x edge as the original registers. Valid
bits reproduce the original reset clear. CHCR, request/chop state, priority,
transfer sequencing and IRQ logic are unchanged.

Two writes in one cycle would need two write ports. The only writers are a guest
bus write and the end-of-transfer writeback. The writeback happens only while
psx_top holds the CPU paused for DMA, so no guest bus access can coincide;
simulation asserts this. Savestate readback/restore of these registers is
removed, so the connected system must use the reset-only sequencer.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess

if __package__:
    from .prepare_resource_targets import UPSTREAM, PIN, digest
else:
    from prepare_resource_targets import UPSTREAM, PIN, digest


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('Pinned DMA source changed near: ' + old.strip().splitlines()[0])
    return text.replace(old, new)


def _sub(text, pattern, new, count=1):
    found = re.findall(pattern, text)
    if len(found) != count:
        raise ValueError('Pinned DMA source changed near: ' + pattern)
    return re.sub(pattern, new, text)


def channel_ram_text(text):
    text = _replace(text, '   signal dmaSettings : dmaRecord;\n', '''   signal dmaSettings : dmaRecord;

   -- MADR/BCR tables: one write port, asynchronous reads (MLAB).
   type tmadrMem is array(0 to 7) of unsigned(23 downto 0);
   type tbcrMem  is array(0 to 7) of unsigned(31 downto 0);
   signal madrMem_bus  : tmadrMem;
   signal madrMem_trig : tmadrMem;
   signal bcrMem_bus   : tbcrMem;
   signal bcrMem_trig  : tbcrMem;
   attribute ramstyle : string;
   attribute ramstyle of madrMem_bus  : signal is "MLAB, no_rw_check";
   attribute ramstyle of madrMem_trig : signal is "MLAB, no_rw_check";
   attribute ramstyle of bcrMem_bus   : signal is "MLAB, no_rw_check";
   attribute ramstyle of bcrMem_trig  : signal is "MLAB, no_rw_check";
   signal madrValid    : std_logic_vector(7 downto 0) := (others => '0');
   signal bcrValid     : std_logic_vector(7 downto 0) := (others => '0');
   signal bus_channel  : integer range 0 to 7;
   signal bus_MADR     : unsigned(23 downto 0);
   signal bus_BCR      : unsigned(31 downto 0);
   signal trig_MADR    : unsigned(23 downto 0);
   signal trig_BCR     : unsigned(31 downto 0);
''')
    # Savestate readback of the moved registers is not supported.
    text = _sub(text, r'      ss_out\(28 \+ i\)\(23 downto 0\) <= std_logic_vector\(dmaArray\(i\)\.D_MADR\); *\n', '')
    text = _sub(text, r'      ss_out\(35 \+ i\)              <= std_logic_vector\(dmaArray\(i\)\.D_BCR\); *\n', '')
    text = _replace(text, '   process (clk1x)\n      variable channel         : integer range 0 to 7;\n', '''   bus_channel <= to_integer(unsigned(bus_addr(6 downto 4)));
   bus_MADR  <= madrMem_bus(bus_channel)     when madrValid(bus_channel) = '1'    else (others => '0');
   bus_BCR   <= bcrMem_bus(bus_channel)      when bcrValid(bus_channel) = '1'     else (others => '0');
   trig_MADR <= madrMem_trig(triggerchannel) when madrValid(triggerchannel) = '1' else (others => '0');
   trig_BCR  <= bcrMem_trig(triggerchannel)  when bcrValid(triggerchannel) = '1'  else (others => '0');

   -- synthesis translate_off
   process (clk1x)
   begin
      if rising_edge(clk1x) then
         assert SS_wren = '0' and SS_rden = '0' and loading_savestate = '0'
            report "DMA channel RAM requires savestate transfers off" severity failure;
      end if;
   end process;
   -- synthesis translate_on

   process (clk1x)
      variable madr_we         : std_logic;
      variable madr_wa         : integer range 0 to 7;
      variable madr_wd         : unsigned(23 downto 0);
      variable bcr_we          : std_logic;
      variable bcr_wa          : integer range 0 to 7;
      variable bcr_wd          : unsigned(31 downto 0);
      variable bus_madr_we     : std_logic;
      variable bus_bcr_we      : std_logic;
      variable channel         : integer range 0 to 7;
''')
    text = _replace(text, '      if rising_edge(clk1x) then\n      \n         fifoIn_reset  <= \'0\';\n', '''      if rising_edge(clk1x) then

         madr_we := '0'; madr_wa := 0; madr_wd := (others => '0');
         bcr_we  := '0'; bcr_wa  := 0; bcr_wd  := (others => '0');
         bus_madr_we := '0'; bus_bcr_we := '0';

         fifoIn_reset  <= '0';
''')
    text = _replace(text, '''                  dmaArray(i).D_MADR            <= (others => '0');
                  dmaArray(i).D_BCR             <= (others => '0');
''', '')
    text = _replace(text, '''               DPCR           <= x"07654321";
''', '''               madrValid      <= (others => '0');
               bcrValid       <= (others => '0');
               DPCR           <= x"07654321";
''')
    text = _sub(text, r'                  if \(to_integer\(SS_Adr\) = \(28 \+ i\)\) then dmaArray\(i\)\.D_MADR .*\n', '')
    text = _sub(text, r'                  if \(to_integer\(SS_Adr\) = \(35 \+ i\)\) then dmaArray\(i\)\.D_BCR .*\n', '')
    text = _replace(text, 'when "00" => bus_dataRead <= x"00" & std_logic_vector(dmaArray(channel).D_MADR);',
                    'when "00" => bus_dataRead <= x"00" & std_logic_vector(bus_MADR);')
    text = _replace(text, 'when "01" => bus_dataRead <= std_logic_vector(dmaArray(channel).D_BCR); ',
                    'when "01" => bus_dataRead <= std_logic_vector(bus_BCR); ')
    text = _replace(text, 'when "00" => dmaArray(channel).D_MADR <= unsigned(bus_dataWrite(23 downto 0));',
                    'when "00" => madr_we := \'1\'; madr_wa := channel; madr_wd := unsigned(bus_dataWrite(23 downto 0)); bus_madr_we := \'1\';')
    text = _replace(text, 'when "01" => dmaArray(channel).D_BCR  <= unsigned(bus_dataWrite);',
                    'when "01" => bcr_we := \'1\'; bcr_wa := channel; bcr_wd := unsigned(bus_dataWrite); bus_bcr_we := \'1\';')
    text = _replace(text, 'dmaSettings.D_MADR <= dmaArray(triggerchannel).D_MADR;', 'dmaSettings.D_MADR <= trig_MADR;')
    text = _replace(text, 'dmaSettings.D_BCR  <= dmaArray(triggerchannel).D_BCR;', 'dmaSettings.D_BCR  <= trig_BCR;')
    text = _replace(text, 'ram_Adr  <= std_logic_vector(dmaArray(triggerchannel).D_MADR(22 downto 2)) & "00";',
                    'ram_Adr  <= std_logic_vector(trig_MADR(22 downto 2)) & "00";')
    writeback = '''                        dmaArray(activeChannel).D_MADR <= dmaSettings.D_MADR;
                        dmaArray(activeChannel).D_BCR  <= dmaSettings.D_BCR;
'''
    text = _replace(text, writeback, '''                        -- synthesis translate_off
                        assert bus_madr_we = '0' and bus_bcr_we = '0'
                           report "DMA writeback coincides with a guest register write" severity failure;
                        -- synthesis translate_on
                        madr_we := '1'; madr_wa := activeChannel; madr_wd := dmaSettings.D_MADR;
                        bcr_we  := '1'; bcr_wa  := activeChannel; bcr_wd  := dmaSettings.D_BCR;
''', count=2)
    # Commit table writes after every original assignment of this edge.
    marker = '''--##############################################################
--############################### ram read handling
--##############################################################
'''
    if text.count(marker) != 1: raise ValueError('Pinned DMA ram-read marker changed')
    index = text.index(marker)
    tail = text[index:]
    end = '''      end if;
   end process;
'''
    if end not in tail: raise ValueError('Pinned DMA process end changed')
    cut = index + tail.index(end)
    commit = '''
         if (madr_we = '1') then
            madrMem_bus(madr_wa)  <= madr_wd;
            madrMem_trig(madr_wa) <= madr_wd;
            madrValid(madr_wa)    <= '1';
         end if;
         if (bcr_we = '1') then
            bcrMem_bus(bcr_wa)  <= bcr_wd;
            bcrMem_trig(bcr_wa) <= bcr_wd;
            bcrValid(bcr_wa)    <= '1';
         end if;

'''
    text = text[:cut] + commit + text[cut:]
    if re.search(r'dmaArray\([^)]*\)\.D_(MADR|BCR)', text):
        raise ValueError('An original MADR/BCR register access remains')
    return text


def channel_ram_source(directory, original=None):
    if original is None:
        actual = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != PIN or subprocess.run(['git', '-C', str(UPSTREAM), 'diff', '--quiet', 'HEAD']).returncode:
            raise ValueError('DMA reference differs from the recorded pin')
        original = UPSTREAM / 'rtl/dma.vhd'
    original = Path(original)
    text = channel_ram_text(original.read_text())
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'dma.vhd'
    path.write_text('-- GPL-2.0 DMA specialization; MADR/BCR kept in LUT RAM, behavior unchanged.\n' + text)
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    return {'path': str(path), 'library': 'work', 'sha256': digest(path),
            'original': str(original), 'original_sha256': digest(original),
            'transform': 'Move per-channel MADR/BCR registers to asynchronous-read LUT RAM with valid bits',
            'required_controls': {'savestate_transfers': 'off (reset-only)'},
            'game_verified': False, 'hardware_verified': False}


def apply_dma_channel_ram(manifest):
    if set(manifest['targets']) != {'psx_top'}:
        raise ValueError('DMA channel RAM requires the connected reset-only system')
    target = manifest['targets']['psx_top']
    if 'dma_channel_ram' in target: raise ValueError('DMA channel RAM already applied')
    if 'reset_only_specialization' not in target: raise ValueError('DMA channel RAM requires the reset-only sequencer')
    directory = Path(target['directory']); copy = channel_ram_source(directory / 'dma-channel-ram')
    changes = 0
    for index, source in enumerate(target['quartus_sources']):
        if source['path'] == copy['original']: target['quartus_sources'][index] = copy; changes += 1
    if changes != 1: raise ValueError('Expected one DMA source')
    qsf = directory / (target['top'] + '.qsf'); text = qsf.read_text()
    if text.count(copy['original']) != 1: raise ValueError('Expected one DMA assignment')
    qsf.write_text(text.replace(copy['original'], copy['path']))
    target['project_sha256']['.qsf'] = digest(qsf)
    target['dma_channel_ram'] = copy; manifest['dma_channel_ram'] = copy
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
