#!/usr/bin/env python3
"""Freeze the PXS7 experiment with one shared decoded-sample write path."""
import argparse
import hashlib
import json
from pathlib import Path

from prepare_spu_ring_paired import once, sha

SPU_SHA = '77170eda3749b24b9ec5a28afd9183c854bb5905bc1e2a2a0f748a4708d6803b'
FIRMWARE_SHA = '775a58c7d872873eb6e0d040138cefbcc4ef9632584ef04787dbae779e2cb224'


def transform(text):
    if hashlib.sha256(text.encode()).hexdigest() != SPU_SHA:
        raise ValueError('Requires the measured PXS6 pending-write source')
    text = once(text, '   signal sample_save_addr_a : unsigned(7 downto 0);\n',
                "   signal sample_restore_second : std_logic := '0';\n")
    text = once(text, '   signal sample_save_data_a : tadpcm_data;\n', '')
    text = once(text, '   signal sample_save_we_a : std_logic_vector(3 downto 0);\n', '')
    text = once(text, "   sample_save_addr_a <= sample_save_page(0) & SS_Adr(6 downto 0) when sample_save_scan = '1' else adpcm_ram_address_a;\n", '')
    start = text.index('      sample_save_data_a(bank) <=')
    end = text.index('   end generate;', start)
    text = text[:start] + text[end:]
    for old, new in (('sample_save_addr_a', 'adpcm_ram_address_a'),
                     ('sample_save_data_a(i)', 'adpcm_ram_data_a(i)'),
                     ('sample_save_we_a(i)', 'adpcm_ram_wren_a(i)')):
        text = once(text, old, new)
    text = once(text, '            end if; -- legacy context write, not decoded sample memory',
                """            else
               sample_restore_second <= '1';
               adpcm_ram_address_a <= sample_save_page(0) & SS_Adr(6 downto 0);
               adpcm_feed_mode <= 1;
               voice.adpcmLast0 <= signed(SS_DataWrite(15 downto 0));
               voice.adpcmLast1 <= signed(SS_DataWrite(31 downto 16));
               adpcm_ram_wren_a(to_integer(sample_save_page(1 downto 1)) * 2) <= '1';
               -- synthesis translate_off
               for bank in 0 to 3 loop
                  if bank mod 2 = 0 then
                     adpcm_expected(bank) <= SS_DataWrite(15 downto 0);
                  else
                     adpcm_expected(bank) <= SS_DataWrite(31 downto 16);
                  end if;
               end loop;
               -- synthesis translate_on
            end if;""")
    # Keep this override local to the sample write signals. An extra branch
    # above the main guest state machine changes unrelated register enables.
    text = once(text, "         if (SPUon = '0') then",
                """         if sample_restore_second = '1' and SS_reset = '0' then
            -- The first lane commits now. Queue the partner for next edge.
            sample_restore_second <= '0';
            voice.adpcmLast0 <= voice.adpcmLast1;
            adpcm_ram_wren_a(to_integer(sample_save_page(1 downto 1)) * 2 + 1) <= '1';
         end if;

         if (SPUon = '0') then""")
    text = once(text, "            sample_save_page <= (others => '0');",
                "            sample_restore_second <= '0';\n            sample_save_page <= (others => '0');")
    text = once(text, "         if RamVoiceVolumes_write = '1' then",
                '''         if sample_restore_second = '1' then
            assert save_spu_quiet = '1' and SS_wren = '0' and SS_rden = '0'
               report "Overlapping decoded sample restore request" severity failure;
         end if;
         if RamVoiceVolumes_write = '1' then''')
    return clear_dac_on_restore(text)


def clear_dac_on_restore(text):
    text = once(text, '   ss_out(7) <= sound_out_right & sound_out_left;\n', '')
    text = once(text, '            elsif SS_Adr = 7 then\n               SS_DataRead <= sound_out_right & sound_out_left;\n', '')
    text = once(text, '            if SS_Adr = 7 then sound_out_left <= SS_DataWrite(15 downto 0); sound_out_right <= SS_DataWrite(31 downto 16); end if;\n', '')
    # Save reads leave the live DAC alone. Only scalar restore writes clear it.
    text = once(text, "         if (SPUon = '0') then\n            sound_out_left",
                "         if SPUon = '0' or (global_scan_access = '1' and SS_wren = '1') then\n            sound_out_left")
    return text


def firmware_text(text):
    if hashlib.sha256(text.encode()).hexdigest() != FIRMWARE_SHA:
        raise ValueError('Requires the matching PXS6 firmware')
    text = once(text, '0x36535850       # "PXS6" (cyclic scalar port, full PXS5 state)',
                '0x37535850       # "PXS7" (restart DAC output after Load)')
    return once(text, '.hword  7, 8, 32, 63,', '.hword  32, 63,')


def cpu_bench(text):
    from prepare_spu_ring_paired import cpu_bench as ring_bench
    text = ring_bench(text)
    text = once(text, 'return n = 7 or (n >= 32', 'return (n >= 32')
    text = once(text, 'x"36" report "Expected a PXS6 source state"',
                'x"37" report "Expected a PXS7 source state"')
    return once(text, 'x"35"; -- preceding indexed PXS5 format',
                'x"36"; -- preceding PXS6 format')


def prepare(source, output):
    source = source.resolve()
    return freeze_restore(
        source, output, transform((source / 'save-states/spu.vhd').read_text()),
        firmware_text((source / 'staged-ring.S').read_text()), 'lean',
    )


def freeze_restore(source, output, body, firmware, label):
    from build_staged_save_fw import image_parts, rom1, rom2, rom40_hex
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import snapshot, hold_guard

    output = output.resolve()
    manifest = snapshot(source, source / 'fit-inputs.json', output, 2)
    spu = output / 'save-states/spu.vhd'
    spu.write_text(body)
    fw = output / f'staged-{label}.S'
    fw.write_text(firmware)
    image, stage = image_parts(source=fw)
    rom = output / 'save-states/psx_save_rom40.hex'
    rom.write_text(rom40_hex(rom1(image), rom2(image)))
    current = inputs(output)
    previous = manifest['quartus_sources']
    if len(previous) != len(current) or {b['path'] for a, b in zip(previous, current) if a != b} != {str(spu), str(rom)}:
        raise ValueError('Unexpected change in selected compilation inputs')
    hold_guard(output, current)
    manifest['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    report = dict(spu_sha256=sha(spu), firmware_source_sha256=sha(fw),
                  rom_sha256=sha(rom), stage2_bytes=len(stage),
                  saved_disc_integrated=False, sleep_verified=False, selected=False)
    (output / f'save-{label}-restore.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), indent=2))
