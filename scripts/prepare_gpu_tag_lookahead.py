#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read the selected GPU's texture tags from M10K without a half-cycle cone.

Predict the exact next stage0 query, including stall replay and mode capture.
The RAM reads on the rising edge that captures stage0; the existing consumer
has a full cycle. A shared write-word register forwards same-address writes.
No extra guest pipeline stage or clock is introduced. Qualification and full
fit/timing are required before selection.
"""
import hashlib
import argparse
import json
from pathlib import Path
import re

SOURCE = 'e97e067ed27b862d69c87398531452d438b9e9d5d05dc1151e5750ae345825da'
RAM_SOURCE = 'b826fea79bbb6194664f52c9aa5c881240a98ac99e9dd0013237a92e793a9ee3'
MARK = 'gpu-tag-lookahead'
# Accepted sources besides the reviewed one (each checked: lookahead_text gives the reviewed output with
# exactly the input difference, so the transform is the same; see the save thread's findings-3.md):
# - the 0.5.8 release pipeline (main-hold-cells): the reviewed file without its MEMORY_SPEED_HOLD identity
#   cells on the stageS colour/UV inputs, which nothing in the tag logic reads.
# - the same file with prepare_main_hold_cells.py --tex (16 more identity cells on stage3_tex -> stage4_tex,
#   also nothing the tag logic reads); run_main_hold_cells.py --composed checks that this step and the
#   texture cells commute on it.
SOURCES = {SOURCE, '8e7bdac86a17cba6441f5153a94c07f4a6f4a951744743330511cfec58e233b4',
           'f8740ab0a962cab1f5a4c1148a0f6e6d54b0c59bfeaceb2422d8c6dfbf1ad39e'}
# - the one-clock M10K primitive with the release's GPL credit line above it.
RAM_CREDIT = '-- GPL-2.0 MiSTer PSX_MiSTer dpram.vhd, simple dual-port specialization.\n'

DECLARATIONS = '''   -- gpu-tag-lookahead: predict the stage0 value captured at this edge.
   signal tag_next_u, tag_next_v, tag_next_u11, tag_next_v11 : unsigned(7 downto 0);
   signal tag_next_mode : unsigned(13 downto 0);
   signal tag_next_u_array, tag_next_v_array : t_filterarray_u8;
   signal tag_read_address : t_filterarray_u8;
   signal tag_ram_q : t_filterarray_b18;
   signal tag_forward_valid : std_logic_vector(0 to 3) := (others => '0');
   signal tag_forward_word : std_logic_vector(17 downto 0) := (others => '0');

   function next_tag_index(u, v : unsigned(7 downto 0);
                           mode : unsigned(13 downto 0)) return unsigned is
   begin
      -- Page X is a multiple of64 halfwords, so it cannot affect these bits.
      if (mode(8) = '0') then
         if (mode(7) = '0') then return v(5 downto 0) & u(5 downto 4);
         else return v(5 downto 0) & u(4 downto 3); end if;
      else return v(4 downto 0) & u(4 downto 2); end if;
   end function;

'''

CONNECTIONS = '''   tag_next_u   <= stageS_u   when pipeline_stall_1 = '1' else pipeline_u;
   tag_next_v   <= stageS_v   when pipeline_stall_1 = '1' else pipeline_v;
   tag_next_u11 <= stageS_u11 when pipeline_stall_1 = '1' else pipeline_u11;
   tag_next_v11 <= stageS_v11 when pipeline_stall_1 = '1' else pipeline_v11;
   tag_next_mode <= drawMode_in when pipeline_busy = '0' else drawMode;
   tag_next_u_array(0) <= tag_next_u;   tag_next_v_array(0) <= tag_next_v;
   tag_next_u_array(1) <= tag_next_u;   tag_next_v_array(1) <= tag_next_v11;
   tag_next_u_array(2) <= tag_next_u11; tag_next_v_array(2) <= tag_next_v;
   tag_next_u_array(3) <= tag_next_u11; tag_next_v_array(3) <= tag_next_v11;

   process(clk2x)
   begin
      if rising_edge(clk2x) then
         if tag_wren_a = '1' then tag_forward_word <= tag_data_a; end if;
         for i in 0 to 3 loop
            tag_forward_valid(i) <= '0';
            if tag_wren_a = '1' and tag_address_a = tag_read_address(i) then
               tag_forward_valid(i) <= '1';
            end if;
         end loop;
      end if;
   end process;

'''

OLD_RAM = '''      itagram : entity mem.RamMLAB
      GENERIC MAP 
      (
         width                               => 18,
         widthad                             => 8
      )
      PORT MAP (
         inclock    => clk2x,
         wren       => tag_wren_a,
         data       => tag_data_a,
         wraddress  => std_logic_vector(tag_address_a),
         rdaddress  => std_logic_vector(tag_addr(i)),
         q          => tag_q_b(i)
      );
'''

NEW_RAM = '''      -- Read the value stage0 will have after this rising edge.
      tag_read_address(i) <= next_tag_index(tag_next_u_array(i), tag_next_v_array(i), tag_next_mode)
         when reset = '0' and ce = '1' and pipeline_stall = '0' else tag_addr(i);
      itagram : entity work.pocket_dpram_sdp
      generic map (addr_width => 8, data_width => 18)
      port map (
         clock_a => clk2x, address_a => std_logic_vector(tag_address_a),
         data_a => tag_data_a, wren_a => tag_wren_a,
         clock_b => clk2x, address_b => std_logic_vector(tag_read_address(i)),
         data_b => (others => '0'), wren_b => '0', q_b => tag_ram_q(i)
      );
      -- Mixed-port same-address data is not relied on.
      tag_q_b(i) <= tag_forward_word when tag_forward_valid(i) = '1' else tag_ram_q(i);
'''


def lookahead_text(text):
    if hashlib.sha256(text.encode()).hexdigest() not in SOURCES:
        raise ValueError('Unreviewed GPU tag-lookahead source')
    original = text
    changes = []

    def replace(before, after):
        nonlocal text
        if text.count(before) != 1:
            raise ValueError('GPU tag-lookahead anchor changed')
        offset = text.index(before)
        text = text[:offset] + after + text[offset + len(before):]
        changes.append((offset, before, after))

    replace('\nbegin \n', '\n' + DECLARATIONS + 'begin \n' + CONNECTIONS)
    replace(OLD_RAM, NEW_RAM)
    for name in ('u', 'v', 'u11', 'v11'):
        for producer in ('stageS_', 'pipeline_'):
            pattern = re.compile(r'(\bstage0_' + name + r'\s*<=\s*)' + producer + name + r'(\s*;)')
            found = list(pattern.finditer(text))
            if len(found) != 1:
                raise ValueError('GPU stage0 producer changed')
            match = found[0]
            replace(match[0], match[1] + 'tag_next_' + name + match[2])
    inverse = text
    for offset, before, after in reversed(changes):
        if inverse[offset:offset + len(after)] != after:
            raise ValueError('GPU tag-lookahead inverse location changed')
        inverse = inverse[:offset] + before + inverse[offset + len(after):]
    if inverse != original:
        raise ValueError('GPU tag-lookahead inverse failed')
    return text


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_project(project):
    project = Path(project).resolve()
    if any((project / n).exists() for n in ('db', 'incremental_db', 'output_files', MARK)):
        raise ValueError('Fresh pre-fit project required')
    qsf = project / 'apf_top.qsf'
    q = qsf.read_text()
    entries = re.findall(r'(?m)^set_global_assignment -name VHDL_FILE "([^"\n]+)"(?:\s+-library\s+\S+)?\s*$', q)
    chosen = {}
    for name in ('gpu_pixelpipeline.vhd', 'pocket_dpram_sdp.vhd'):
        # The reviewed primitive is compiled into both work and mem from
        # the same file. Require one file, not one library assignment.
        found = list(dict.fromkeys(s for s in entries if Path(s).name == name))
        if len(found) != 1:
            raise ValueError('One selected tag/RAM source required: ' + name)
        p = Path(found[0])
        chosen[name] = (found[0], p if p.is_absolute() else project / p)
    source = chosen['gpu_pixelpipeline.vhd'][1]
    ram = chosen['pocket_dpram_sdp.vhd'][1]
    ram_text = ram.read_text()
    if digest(ram) != RAM_SOURCE and not (ram_text.startswith(RAM_CREDIT) and
            hashlib.sha256(ram_text[len(RAM_CREDIT):].encode()).hexdigest() == RAM_SOURCE):
        raise ValueError('Reviewed one-clock M10K primitive required')
    changed = lookahead_text(source.read_text())
    token = '"' + chosen[source.name][0] + '"'
    if q.count(token) != 1:
        raise ValueError('One quoted pixel-pipeline assignment required')
    protected = {str(p): digest(p) for _, p in chosen.values()}
    out = project / MARK / source.name
    if any(digest(p) != sha for p, sha in protected.items()):
        raise ValueError('Original tag/RAM sources changed')
    out.parent.mkdir()
    out.write_text(changed)
    qsf.write_text(q.replace(token, '"' + str(out) + '"'))
    report = dict(source=str(source), source_sha256=protected[str(source)],
                  path=str(out), sha256=digest(out), protected_sha256=protected,
                  ram_blocks_per_retained_lookup=1, generic_lookups=4,
                  forwarding_word_register_bits=18, forwarding_flags=4,
                  stage0_capture_schedule_changed=False, extra_guest_pipeline_cycles=0,
                  new_clocks=False, clock_frequencies_changed=False, constraints_changed=False,
                  scope='Selected generation8 pipeline; next-stage0 prediction and same-address forwarding',
                  fit_verified=False, timing_verified=False, hardware_verified=False,
                  production_selected=False)
    (out.parent / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def prepare_frozen(source, output):
    """docs/ss-pxs6-recipe.sh: snapshot a frozen project, apply the lookahead, rebind fit-inputs.json."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run_frozen_pocket_fit import snapshot, hold_guard, load_source
    from measure_pocket_seeds import inputs
    source, output = Path(source).resolve(), Path(output).resolve()
    result = snapshot(source, source / 'fit-inputs.json', output, 2)
    report = prepare_project(output)
    current = inputs(output)
    hold_guard(output, current)
    result['quartus_sources'] = current
    result['project_sha256'] = {s: digest(output / ('apf_top' + s)) for s in ('.qsf', '.qpf')}
    (output / 'fit-inputs.json').write_text(json.dumps(result, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project', type=Path)
    p.add_argument('--output', type=Path, help='frozen mode: snapshot PROJECT into OUTPUT first')
    a = p.parse_args()
    print(json.dumps(prepare_frozen(a.project, a.output) if a.output else prepare_project(a.project), indent=2))
