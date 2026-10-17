"""Prepare a source-bound GPU metadata RAM candidate into a fresh directory.

The selected MiSTer shader, pixel latency and throughput stay unchanged.
This moves unchanged stage0..3 fields into an eight-packet delay RAM;
related half-cycle timing and whole-top capacity still require measurement.
No release preset, clock exception or feature change is selected here.
"""
import hashlib
import re
import argparse
import json
from pathlib import Path
import shutil
from prepare_resource_targets import UPSTREAM, digest

EXPECTED = '02890a423566cef6b88d1cd0c53a21292df8d823c80173511ea2c48cef12bf43'
FIELDS = ['texture', 'transparent', 'rawTexture', 'dithering',
          'cr', 'cg', 'cb', 'x', 'y', 'oldPixel', 'oldPixel2']

def metadata_text(source):
    if hashlib.sha256(source.encode()).hexdigest() != EXPECTED:
        raise ValueError("Unreviewed pixel metadata source")
    text = source
    # Keep stage0_texture because the current cache-miss guard uses it.
    for stage in range(4):
        for field in FIELDS:
            if stage == 0 and field == 'texture':
                continue
            pattern = rf'(?m)^\s*stage{stage}_{field}\s*<=[^;]*;[ \t]*\n'
            text, count = re.subn(pattern, '', text)
            if count != (2 if stage == 0 else 1):
                raise ValueError(f"Pixel metadata assignment changed: {stage}/{field}")
            if stage != 3:
                text, count = re.subn(rf'(?m)^   signal stage{stage}_{field}\s*:[^;]*;[^\n]*\n', '', text)
                if count != 1:
                    raise ValueError(f"Pixel metadata declaration changed: {stage}/{field}")

    marker = '\nbegin \n'
    if text.count(marker) != 1:
        raise ValueError("Pixel metadata architecture marker changed")
    declarations = '''
   -- One input packet per original pipeline advance. No pointer reset:
   -- upstream metadata holds through reset; only valid bits are cleared.
   signal metaPtr      : unsigned(2 downto 0) := (others => '0');
   signal metaAdvance  : std_logic;
   signal metaReadAddr : std_logic_vector(2 downto 0);
   signal metaData     : std_logic_vector(78 downto 0);
   signal metaQ        : std_logic_vector(78 downto 0);
'''
    wiring = '''
   metaAdvance <= ce and (not reset) and (not pipeline_stall);
   metaData <= (stageS_texture and (not noTexture)) & stageS_transparent & stageS_rawTexture & stageS_dithering &
               std_logic_vector(stageS_cr) & std_logic_vector(stageS_cg) & std_logic_vector(stageS_cb) &
               std_logic_vector(stageS_x) & std_logic_vector(stageS_y) & stageS_oldPixel & stageS_oldPixel2
               when pipeline_stall_1 = '1' else
               (pipeline_texture and (not noTexture)) & pipeline_transparent & pipeline_rawTexture & pipeline_dithering &
               std_logic_vector(pipeline_cr) & std_logic_vector(pipeline_cg) & std_logic_vector(pipeline_cb) &
               std_logic_vector(pipeline_x) & std_logic_vector(pipeline_y) & vramLineData & vramLineData2;
   -- After an advance metaPtr points to the NEXT input slot; stage3 is
   -- four accepted packets behind it. Read before original stage4 edge.
   metaReadAddr <= std_logic_vector(metaPtr - to_unsigned(4, 3));
   process(clk2x) begin
      if rising_edge(clk2x) then
         if metaAdvance = '1' then metaPtr <= metaPtr + 1; end if;
      end if;
   end process;
   imetadata : entity work.dpram
   generic map (addr_width => 3, data_width => 79)
   port map (
      clock_a => clk2x, address_a => std_logic_vector(metaPtr),
      data_a => metaData, wren_a => metaAdvance, q_a => open,
      clock_b => not clk2x, address_b => metaReadAddr,
      data_b => (others => '0'), wren_b => '0', q_b => metaQ
   );
   stage3_texture     <= metaQ(78);
   stage3_transparent <= metaQ(77);
   stage3_rawTexture  <= metaQ(76);
   stage3_dithering   <= metaQ(75);
   stage3_cr         <= unsigned(metaQ(74 downto 67));
   stage3_cg         <= unsigned(metaQ(66 downto 59));
   stage3_cb         <= unsigned(metaQ(58 downto 51));
   stage3_x          <= unsigned(metaQ(50 downto 41));
   stage3_y          <= unsigned(metaQ(40 downto 32));
   stage3_oldPixel   <= metaQ(31 downto 16);
   stage3_oldPixel2  <= metaQ(15 downto 0);
'''
    return text.replace(marker, declarations + marker + wiring)


def prepare(selected, directory, expected_sha256=EXPECTED):
    selected = Path(selected).resolve()
    if expected_sha256 != EXPECTED or digest(selected) != EXPECTED:
        raise ValueError('Selected pixel metadata source identity mismatch')
    changed = metadata_text(selected.read_text())
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    output = directory / 'gpu_pixelpipeline.vhd'
    output.write_text(changed)
    shutil.copyfile(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    if digest(selected) != expected_sha256:
        raise ValueError('Selected pixel source changed during preparation')
    result = dict(path=str(output), sha256=digest(output), assignment='VHDL_FILE', library='work',
        selected=str(selected), selected_sha256=expected_sha256,
        preparer_sha256=digest(Path(__file__)),
        transform='Stage0..3 unchanged metadata in8-slot RAM, rising writes/falling reads, original stage4 consumption',
        added_pipeline_clocks=0, new_clock_domain=False,
        production_selected=False, fit_verified=False, timing_verified=False, hardware_verified=False,
        required_contracts=[
            'Keep original valid/stall/skid/CE and shader schedule',
            'Advance pointer only when original pipeline advances; retain pointer through reset',
            'Keep stage0_texture for original cache-miss decisions',
            'Read address is next input pointer minus four; no read/write address collision',
            'Measure both related half-cycle paths and unchanged whole-core constraints',
            'Measure aggregate LAB and physical RAM use before selecting a release'],
        limitations=['Exact selected source only', 'Bounded GPU/pixel checks are not game or Pocket qualification'])
    (directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selected', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expected-sha256', default=EXPECTED)
    args = parser.parse_args()
    print(json.dumps(prepare(args.selected, args.output, args.expected_sha256), indent=2))
