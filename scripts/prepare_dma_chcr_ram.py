"""Keep the static DMA CHCR fields in LUT RAM as well (after dma-channel-ram).

CHCR bits 0-1, 8-10, 16-18, 20-22 and 29-30 are written only by guest bus
writes and read only for the guest readback channel or the triggering
channel, like MADR/BCR. They move to the same pair of asynchronous-read
MLAB copies (bus and trigger port) with valid bits for the reset clear.
Bits 24 (start) and 28 (trigger), which the arbiter reads for every channel
and the engine rewrites, stay in flip-flops. Savestate transfer of CHCR is
removed (reset-only build).
"""
import json
from pathlib import Path
import re

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('Pinned DMA source changed near: ' + old.strip().splitlines()[0])
    return text.replace(old, new)


def chcr_ram_text(text):
    if 'madrMem_trig' not in text: raise ValueError('DMA CHCR RAM requires dma-channel-ram')
    text = _replace(text, '   signal trig_BCR     : unsigned(31 downto 0);\n', '''   signal trig_BCR     : unsigned(31 downto 0);
   -- Static CHCR fields {30:29, 22:20, 18:16, 10:8, 1:0} (dma-chcr-ram).
   type tchcrMem is array(0 to 7) of unsigned(12 downto 0);
   signal chcrMem_bus  : tchcrMem;
   signal chcrMem_trig : tchcrMem;
   attribute ramstyle of chcrMem_bus  : signal is "MLAB, no_rw_check";
   attribute ramstyle of chcrMem_trig : signal is "MLAB, no_rw_check";
   signal chcrValid    : std_logic_vector(7 downto 0) := (others => '0');
   signal bus_CHCRs    : unsigned(12 downto 0);
   signal trig_CHCRs   : unsigned(12 downto 0);
   function chcrPack(v : unsigned(31 downto 0)) return unsigned is
   begin
      return v(30 downto 29) & v(22 downto 20) & v(18 downto 16) & v(10 downto 8) & v(1 downto 0);
   end function;
   function chcrExpand(s : unsigned(12 downto 0); b24 : std_logic; b28 : std_logic) return unsigned is
      variable v : unsigned(31 downto 0) := (others => '0');
   begin
      v(30 downto 29) := s(12 downto 11); v(28) := b28; v(24) := b24;
      v(22 downto 20) := s(10 downto 8); v(18 downto 16) := s(7 downto 5);
      v(10 downto 8) := s(4 downto 2); v(1 downto 0) := s(1 downto 0);
      return v;
   end function;
''')
    text = _replace(text, "   trig_BCR  <= bcrMem_trig(triggerchannel)  when bcrValid(triggerchannel) = '1'  else (others => '0');\n",
                    "   trig_BCR  <= bcrMem_trig(triggerchannel)  when bcrValid(triggerchannel) = '1'  else (others => '0');\n"
                    "   bus_CHCRs  <= chcrMem_bus(bus_channel)     when chcrValid(bus_channel) = '1'    else (others => '0');\n"
                    "   trig_CHCRs <= chcrMem_trig(triggerchannel) when chcrValid(triggerchannel) = '1' else (others => '0');\n")
    text = _replace(text, '      variable bcr_wd          : unsigned(31 downto 0);\n',
                    '      variable bcr_wd          : unsigned(31 downto 0);\n      variable chcr_we         : std_logic;\n      variable chcr_wa         : integer range 0 to 7;\n      variable chcr_wd         : unsigned(12 downto 0);\n')
    text = _replace(text, "         bus_madr_we := '0'; bus_bcr_we := '0';\n",
                    "         bus_madr_we := '0'; bus_bcr_we := '0';\n         chcr_we := '0'; chcr_wa := 0; chcr_wd := (others => '0');\n")
    text = _replace(text, "               madrValid      <= (others => '0');\n", "               madrValid      <= (others => '0');\n               chcrValid      <= (others => '0');\n")
    text, n = re.subn(r'      ss_out\(42 \+ i\) +<= std_logic_vector\(dmaArray\(i\)\.D_CHCR\); *\n', '', text)
    if n != 1: raise ValueError('Pinned DMA CHCR savestate output changed')
    text, n = re.subn(r'                  if \(to_integer\(SS_Adr\) = \(42 \+ i\)\) then dmaArray\(i\)\.D_CHCR .*\n', '', text)
    if n != 1: raise ValueError('Pinned DMA CHCR savestate input changed')
    text = _replace(text, 'when "10" => bus_dataRead <= std_logic_vector(dmaArray(channel).D_CHCR);',
                    'when "10" => bus_dataRead <= std_logic_vector(chcrExpand(bus_CHCRs, dmaArray(channel).D_CHCR(24), dmaArray(channel).D_CHCR(28)));')
    old = '''                        dmaArray(channel).D_CHCR( 1 downto  0) <= unsigned(bus_dataWrite( 1 downto  0));
                        dmaArray(channel).D_CHCR(10 downto  8) <= unsigned(bus_dataWrite(10 downto  8));
                        dmaArray(channel).D_CHCR(18 downto 16) <= unsigned(bus_dataWrite(18 downto 16));
                        dmaArray(channel).D_CHCR(22 downto 20) <= unsigned(bus_dataWrite(22 downto 20));
                        dmaArray(channel).D_CHCR(          24) <= bus_dataWrite(24);
                        dmaArray(channel).D_CHCR(30 downto 28) <= unsigned(bus_dataWrite(30 downto 28));
'''
    text = _replace(text, old, '''                        chcr_we := '1'; chcr_wa := channel; chcr_wd := chcrPack(unsigned(bus_dataWrite));
                        dmaArray(channel).D_CHCR(          24) <= bus_dataWrite(24);
                        dmaArray(channel).D_CHCR(          28) <= bus_dataWrite(28);
''')
    text = _replace(text, 'dmaSettings.D_CHCR <= dmaArray(triggerchannel).D_CHCR;',
                    'dmaSettings.D_CHCR <= chcrExpand(trig_CHCRs, dmaArray(triggerchannel).D_CHCR(24), dmaArray(triggerchannel).D_CHCR(28));')
    text = _replace(text, "if (dmaArray(triggerchannel).D_CHCR(0) = '1') then", "if (trig_CHCRs(0) = '1') then")
    text = _replace(text, "if (dmaArray(triggerchannel).D_CHCR(10) = '0' and dmaArray(triggerchannel).D_CHCR(1) = '1') then",
                    "if (trig_CHCRs(4) = '0' and trig_CHCRs(1) = '1') then")
    text = _replace(text, "         if (madr_we = '1') then\n", '''         if (chcr_we = '1') then
            chcrMem_bus(chcr_wa)  <= chcr_wd;
            chcrMem_trig(chcr_wa) <= chcr_wd;
            chcrValid(chcr_wa)    <= '1';
         end if;
         if (madr_we = '1') then
''')
    for m in re.finditer(r'dmaArray\([^)]*\)\.D_CHCR(\([^)]*\))?', text):
        if m.group(1) not in ('(24)', '(28)', '(          24)', '(          28)', None) and m.group(1).strip('( )') not in ('24', '28'):
            raise ValueError('A static CHCR register access remains: ' + m.group(0))
    return text


def apply_dma_chcr_ram(manifest):
    target = manifest['targets']['psx_top']
    if 'dma_channel_ram' not in target: raise ValueError('DMA CHCR RAM requires dma-channel-ram')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'dma.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'dma-chcr-ram'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'dma.vhd'; path.write_text(chcr_ram_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='Static CHCR fields in LUT RAM')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one DMA assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['dma_chcr_ram'] = True; manifest['dma_chcr_ram'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
