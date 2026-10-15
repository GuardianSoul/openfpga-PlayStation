"""Texture-cache valid bits as a generation number stored with each tag.

The 256 valid flip-flops of gpu_pixelpipeline's texture cache become an 8-bit
generation field in the existing tag MLAB: an entry is valid when its stored
generation equals the current one, a fill stores the current generation, and a
cache clear increments it. Every 255th clear wraps the generation, so the tags
are first rewritten with generation 0 over 256 clocks in a new CLEARTAGS
state, during which the pixel pipeline stalls exactly as it does for any
non-IDLE state. Pixels are unchanged; that clear takes 256 extra clocks.
Opt-in (approved tested, reversible latency changes; Codex handed this
block to Claude on 2026-10-13).
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import UPSTREAM, digest
else:
    from prepare_resource_targets import UPSTREAM, digest


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f'gpu_pixelpipeline changed ({text.count(old)} matches): ' + old.strip()[:70])
    return text.replace(old, new)


def tag_generation_text(text, bits=8):
    """bits < 8 is for tests: the generation wraps sooner, exercising CLEARTAGS."""
    top = f'x"{(1 << bits) - 1:02X}"'
    text = _replace(text, '   type t_filterarray_b10 is array(0 to 3) of std_logic_vector(9 downto 0);\n',
                    '   type t_filterarray_b10 is array(0 to 3) of std_logic_vector(9 downto 0);\n'
                    '   type t_filterarray_b18 is array(0 to 3) of std_logic_vector(17 downto 0);\n')
    text = _replace(text, '   signal tag_data_a          : std_logic_vector(9 downto 0) := (others => \'0\');\n',
                    '   signal tag_data_a          : std_logic_vector(17 downto 0) := (others => \'0\');\n')
    text = _replace(text, '   signal tag_q_b             : t_filterarray_b10;\n', '   signal tag_q_b             : t_filterarray_b18;\n')
    text = _replace(text, '   signal tagValid            : std_logic_vector(0 to 255) := (others => \'0\');\n',
                    '''   -- Tag generation: an entry is valid when its stored generation is current.
   signal tagGeneration       : unsigned(7 downto 0) := x"01";
   signal clearAddress        : unsigned(7 downto 0) := (others => '0');
''')
    text = _replace(text, '''         width                               => 10,
         widthad                             => 8''', '''         width                               => 18,
         widthad                             => 8''')
    text = _replace(text, "      cachehit(i)        <= '1' when (unsigned(tag_q_b(i)) = tag_data(i) and tagValid(to_integer(tag_addr(i))) = '1') else '0';",
                    "      cachehit(i)        <= '1' when (unsigned(tag_q_b(i)(9 downto 0)) = tag_data(i) and unsigned(tag_q_b(i)(17 downto 10)) = tagGeneration) else '0';")
    text = _replace(text, '''               tag_wren_a    <= '1';
               tagValid(to_integer(tag_address_a)) <= '1';
''', '''               tag_wren_a    <= '1';
''')
    text = _replace(text, '''                  if (clearCacheBuffer = '1' and pipeline_busy = '0') then
                     clearCacheBuffer <= '0';
                     tagValid         <= (others => '0');
                  end if;
                  if (textPalReq = '1' and pipeline_busy = '0') then''', '''                  if (clearCacheBuffer = '1' and pipeline_busy = '0') then
                     clearCacheBuffer <= '0';
                     if (tagGeneration = __GENTOP__) then
                        tagGeneration <= x"01";
                        clearAddress  <= (others => '0');
                        state         <= CLEARTAGS;
                     else
                        tagGeneration <= tagGeneration + 1;
                     end if;
                  end if;
                  if (clearCacheBuffer = '1' and pipeline_busy = '0' and tagGeneration = __GENTOP__) then
                     null; -- the tag clear runs first; other requests wait for IDLE
                  elsif (textPalReq = '1' and pipeline_busy = '0') then''')
    text = _replace(text, "                     tag_data_a      <= std_logic_vector(tag_data(0));\n",
                    "                     tag_data_a      <= std_logic_vector(tagGeneration) & std_logic_vector(tag_data(0));\n")
    text = _replace(text, "                  tag_data_a      <= std_logic_vector(tag_data_1(selectIndex));\n",
                    "                  tag_data_a      <= std_logic_vector(tagGeneration) & std_logic_vector(tag_data_1(selectIndex));\n")
    text = _replace(text, '''               when WAITTEXTURE => null; -- handled outside due to ce
''', '''               when WAITTEXTURE => null; -- handled outside due to ce

               when CLEARTAGS =>
                  tag_wren_a    <= '1';
                  tag_address_a <= clearAddress;
                  tag_data_a    <= (others => '0');
                  clearAddress  <= clearAddress + 1;
                  if (clearAddress = x"FF") then
                     state <= IDLE;
                  end if;
''')
    import re
    states = re.search(r'type tState is\s*\(\s*IDLE,', text)
    if not states: raise ValueError('gpu_pixelpipeline state type changed')
    text = text[:states.end()] + '\n      CLEARTAGS,' + text[states.end():]
    text = text.replace('__GENTOP__', top)
    if 'tagValid' in text: raise ValueError('A tag valid access remains')
    return text


def apply_gpu_tag_generation(manifest):
    target = manifest['targets']['psx_top']
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'gpu_pixelpipeline.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gpu-tag-generation'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'gpu_pixelpipeline.vhd'; path.write_text(tag_generation_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='Texture-cache valid bits as tag generations')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one gpu_pixelpipeline assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_tag_generation'] = True; manifest['gpu_tag_generation'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
