#!/usr/bin/env python3
"""Private alternative to PXS5's added scalar readback mux.

A full sequence of 16 reads at words32..47 rotates all scalar context back
to its starting point. Sixteen writes insert the replacement context.
This changes the private scan ABI; it is not compatible with PXS5 firmware.
DAC outputs retain the direct word7 port: SPUon may clamp them to zero
even while held, so they cannot carry circulating context bits.
No ordinary guest register, RAM port, datapath clock or sample schedule is
removed. An abandoned read sequence completes its rotation when hold drops.
"""
import hashlib
import re

BASE = '1babb215ccd4c55e095feaebd0dfc97626e4883f393178f8cb4605b4911a2553'
FIELDS = [('capturePosition', 10, 'unsigned'), ('ramTransferAddr', 19, 'unsigned'),
          ('reverbCurrentAddress', 18, 'unsigned'), ('reverbRight', 1, 'bit'),
          ('noiseLevel', 16, 'unsigned'), ('noiseCnt', 32, 'unsigned'),
          ('voice_adsrWrite', 24, 'vector'), ('voice_repeatWrite', 24, 'vector')]
FIELDS += [(f'reverb{side}_d{delay}', 16, 'signed') for side in 'LR' for delay in range(1, 5)]
FIELDS += [(name, 16, 'signed') for name in ('reverbDownLeft', 'reverbDownRight')]
FIELDS += [(f'reverbPart{part}_{side}', 25, 'signed') for part, side in (('A','L'), ('B','L'), ('A','R'), ('B','R'))]
FIELDS += [('rvLastValid', 2, 'vector'), ('cd_next_left', 17, 'signed'),
           ('cd_next_right', 17, 'signed'), ('irqSaved', 1, 'bit'),
           ('ENDX', 32, 'vector'), ('IRQ9', 1, 'bit'),
           ('CURVOL_L', 16, 'vector'), ('CURVOL_R', 16, 'vector')]
BITS = sum(width for _, width, _ in FIELDS)
WORDS = (BITS + 31) // 32
PAD = WORDS * 32 - BITS


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Global ring anchor changed: ' + old[:100])
    return text.replace(old, new)


def transform(text, fault=False):
    if hashlib.sha256(text.encode()).hexdigest() != BASE:
        raise ValueError('Requires exact qualified experimental PXS5 SPU')
    assert BITS == 506 and WORDS == 16 and PAD == 6
    decl = f'''   signal global_scan_pad : std_logic_vector({PAD-1} downto 0) := (others => '0');
   signal global_scan : std_logic_vector({WORDS*32-1} downto 0);
   signal global_scan_next : std_logic_vector({WORDS*32-1} downto 0);
   signal global_scan_index : integer range 0 to {WORDS-1} := 0;
   signal global_scan_access : std_logic;
'''
    text = once(text, '   signal sample_save_scan : std_logic;', '   signal sample_save_scan : std_logic;\n' + decl)
    elements = [name if kind in ('vector', 'bit') else f'std_logic_vector({name})' for name, _, kind in reversed(FIELDS)]
    route = '   global_scan <= global_scan_pad & ' + ' & '.join(elements) + ';\n'
    route += "   global_scan_access <= '1' when save_spu_quiet = '1' and SS_Adr >= 32 and SS_Adr <= 47 else '0';\n"
    shift = 31 if fault else 32
    route += f'''   global_scan_next <= SS_DataWrite & global_scan({WORDS*32-1} downto 32) when global_scan_access = '1' and SS_wren = '1' else
                       global_scan({shift-1} downto 0) & global_scan({WORDS*32-1} downto {shift});
'''
    text = once(text, '   sample_save_scan <=', route + '   sample_save_scan <=')
    text = once(text, "            sample_save_page <= (others => '0');", f"            global_scan_pad <= (others => '0'); global_scan_index <= 0;\n            sample_save_page <= (others => '0');")
    assignments = []
    low = 0
    for name, width, kind in FIELDS:
        val = f'global_scan_next({low})' if kind == 'bit' else f'global_scan_next({low+width-1} downto {low})'
        if kind in ('signed', 'unsigned'):
            val = kind + '(' + val + ')'
        assignments.append(f'            {name} <= {val};')
        low += width
    assignments += [f'            global_scan_pad <= global_scan_next({WORDS*32-1} downto {BITS});',
                    f'            if global_scan_index = {WORDS-1} then global_scan_index <= 0; else global_scan_index <= global_scan_index + 1; end if;']
    main = '''         elsif ((global_scan_access = '1' and (SS_rden = '1' or SS_wren = '1')) or
                (save_spu_hold = '0' and global_scan_index /= 0)) then
''' + '\n'.join(assignments) + '\n'
    text = once(text, "         elsif (SS_wren = '1') then\n", main + "         elsif (SS_wren = '1') then\n")
    start = text.index("         elsif (SS_wren = '1') then\n")
    end = text.index("         elsif (ce = '1') then\n", start)
    part = text[start:end]
    # Drop the superseded scalar write decoders. All their fields are in the
    # ring, including ENDX/current volume/IRQ formerly supplied by bus reads.
    scalar = '|'.join(re.escape(name) for name, _, _ in FIELDS)
    pattern = r'^            if (?:\(to_integer\(SS_Adr\).*|SS_Adr = \d+ then.*)$'
    kept = []
    for line in part.splitlines(keepends=True):
        if re.match(pattern, line) and re.search(r'\b(?:' + scalar + r')\b\s*<=', line):
            continue
        kept.append(line)
    part = ''.join(kept)
    text = text[:start] + part + text[end:]
    # Scalar output is a fixed 32-bit tap. The old indexed array is dead and
    # will be pruned; no second readback copy of the same state remains.
    text = once(text, "            if SS_Adr(8 downto 7) = \"01\" then\n", '''            if global_scan_access = '1' then
               SS_DataRead <= global_scan(31 downto 0);
            elsif SS_Adr = 7 then
               SS_DataRead <= sound_out_right & sound_out_left;
            elsif SS_Adr(8 downto 7) = "01" then
''')
    text = once(text, '               SS_DataRead <= ss_out(to_integer(SS_Adr(5 downto 0)));', "               SS_DataRead <= (others => '0');")
    # Avoid collision with the 18 scalar words. Low four address bits still
    # select all fifteen existing rvWork entries through its original port.
    text = text.replace('SS_Adr >= 48 and SS_Adr <= 62', 'SS_Adr >= 96 and SS_Adr <= 110')
    return text


def area_screen(source, output):
    """Freeze exactly one RTL change; deliberately retain unpaired PXS5 ROM.

    The result is only for synthesis cost, never assembly/installation. A
    firmware implementing the new scan ABI must be qualified separately.
    """
    import json
    from pathlib import Path
    from run_frozen_pocket_fit import snapshot, hold_guard
    from measure_pocket_seeds import inputs
    source, output = Path(source).resolve(), Path(output).resolve()
    original = source / 'save-states/spu.vhd'
    if hashlib.sha256(original.read_bytes()).hexdigest() != BASE:
        raise ValueError('Area control must be the closed expanded PXS5 graph')
    result = snapshot(source, source / 'fit-inputs.json', output, 2)
    target = output / 'save-states/spu.vhd'
    target.write_text(transform(target.read_text()))
    current = inputs(output)
    changed = {a['path'] for a, b in zip(result['quartus_sources'], current) if a != b}
    if len(current) != len(result['quartus_sources']) or changed != {str(target)}:
        raise ValueError('Cyclic scalar screen changed other selected inputs')
    hold_guard(output, current)
    result['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(result, indent=2) + '\n')
    report = dict(source=str(source), selected=False, area_screen_only=True,
                  firmware_compatible=False, hardware_verified=False,
                  scalar_bits=BITS, scalar_words=WORDS,
                  input_spu_sha256=BASE,
                  output_spu_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    (output / 'save-global-ring-area-only.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--area-screen-only', required=True, action='store_true')
    args = parser.parse_args()
    print(json.dumps(area_screen(args.source, args.output), indent=2))
