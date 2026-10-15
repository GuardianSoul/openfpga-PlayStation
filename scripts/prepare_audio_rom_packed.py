#!/usr/bin/env python3
"""Pack the existing exact sinc coefficients by tap magnitude in a copied top.

No new coefficient bank, interpolation, arithmetic or pipeline schedule. The
same mirror.hex18 file initializes four pairs of narrow tap bands. Quartus
must measure the physical memory count; this helper does not claim a fit.
"""
import argparse
import json
from pathlib import Path
import re

from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest


def verify_bank(path):
    """Check every stored integer before choosing the lossless widths."""
    path = Path(path)
    words = [int(s, 16) for s in path.read_text().split()]
    if len(words) != 10368 or any(v < 0 or v >= 1 << 18 for v in words):
        raise ValueError('Expected the exact-size 81x128 18-bit coefficient bank')
    for i, value in enumerate(words):
        tap = i % 128
        folded = min(tap, 127-tap)
        width = (7, 10, 12, 18)[folded//16]
        # At 18 bits keep the unity sentinel verbatim, rather than interpreting
        # it as a negative coefficient. Other bands only contain signed data.
        signed = value-(1 << 18) if value & (1 << 17) else value
        if width < 18 and not -(1 << (width-1)) <= signed < (1 << (width-1)):
            raise ValueError(f'Coefficient {i} does not fit its exact tap width')
    return dict(path=str(Path(path).resolve()), sha256=digest(path), words=len(words),
                all_words_reconstruct_exactly=True)


def packed_files(path, directory):
    """Write only derived constants; verify both signed lanes byte-exactly."""
    verify_bank(path)
    values = [int(s, 16) for s in Path(path).read_text().split()]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    for band, width in enumerate((7, 10, 12, 18)):
        mask = (1 << width)-1
        packed = []
        for p in range(81):
            for t in range(16):
                a = values[p*128+band*16+t]
                b = values[p*128+127-band*16-t]
                word = (a & mask) | ((b & mask) << width)
                for lane, original in enumerate((a, b)):
                    raw = (word >> (lane*width)) & mask
                    restored = raw if width == 18 or raw < (1 << (width-1)) else raw-(1 << width)
                    if (restored & 0x3ffff) != original:
                        raise ValueError('Packed coefficient roundtrip failed')
                packed.append(word)
        out = directory/f'band{band}.hex'
        out.write_text(''.join(f'{v:0{(2*width+3)//4}x}\n' for v in packed))
        result.append(out)
    return result


OLD_ROM = '''    (* ramstyle = "M10K" *) reg [17:0] coefficients [0:10367];
    (* ramstyle = "M10K" *) reg [31:0] history [0:127];
    initial $readmemh(COEFFICIENT_FILE, coefficients);'''
OLD_REG = '    reg [17:0] coefficient_a, coefficient_b;'
OLD_READ = '''            coefficient_a <= coefficients[address_a];
            coefficient_b <= coefficients[address_b];
'''
PACKED = '''    (* ramstyle = "M10K" *) reg [31:0] history [0:127];

    // Tap bands are symmetric about the center: 0..15/112..127,
    // 16..31/96..111, 32..47/80..95, and 48..79. Each word stores
    // both sides at the SAME phase. The signed widths are exact for every
    // stored coefficient. 18-bit unity retains its original sentinel.
    wire [6:0] folded_a = tap_a[6] ? ~tap_a : tap_a;
    wire [6:0] folded_b = tap_b[6] ? ~tap_b : tap_b;
    wire [10:0] packed_address_a = {phase_a[6:0], folded_a[3:0]};
    wire [10:0] packed_address_b = {phase_b[6:0], folded_b[3:0]};
    reg [1:0] band_a, band_b;
    reg side_a, side_b;
    wire [17:0] band_coefficient_a [0:3];
    wire [17:0] band_coefficient_b [0:3];
    wire [17:0] coefficient_a = band_coefficient_a[band_a];
    wire [17:0] coefficient_b = band_coefficient_b[band_b];
PACKED_BANKS
    always @(posedge clk) if (issue) begin
        band_a <= folded_a[5:4]; band_b <= folded_b[5:4];
        side_a <= tap_a[6]; side_b <= tap_b[6];
    end
'''


def adapt_text(text, files=None):
    """Reject changed source contracts before returning a complete replacement."""
    for anchor in (OLD_ROM, OLD_REG, OLD_READ):
        if text.count(anchor) != 1:
            raise ValueError('Unsupported or already packed sinc ROM source')
    files = files or [f'band{i}.hex' for i in range(4)]
    if len(files) != 4 or any('"' in str(p) or '\n' in str(p) for p in files):
        raise ValueError('Expected four safe packed initialization paths')
    banks = []
    # Explicit declarations avoid a Quartus 25.1 elaboration stall in the
    # generate/localparam/conditional-readmemh form of the same four banks.
    for i, (width, path) in enumerate(zip((7, 10, 12, 18), files)):
        banks.append(f'''    (* ramstyle = "M10K" *) reg [{2*width-1}:0] coefficient_bank{i} [0:1295];
    reg [{2*width-1}:0] read{i}_a, read{i}_b;
    initial $readmemh("{path}", coefficient_bank{i});
    always @(posedge clk) if (issue) begin
        read{i}_a <= coefficient_bank{i}[packed_address_a];
        read{i}_b <= coefficient_bank{i}[packed_address_b];
    end''')
        for port in ('a', 'b'):
            name = f'selected{i}_{port}'
            banks.append(f'    wire [{width-1}:0] {name} = side_{port} ? read{i}_{port}[{2*width-1}:{width}] : read{i}_{port}[{width-1}:0];')
            value = f'{{{{{18-width}{{{name}[{width-1}]}}}},{name}}}' if width < 18 else name
            banks.append(f'    assign band_coefficient_{port}[{i}] = {value};')
    packed = PACKED.replace('PACKED_BANKS', '\n'.join(banks))
    return text.replace(OLD_ROM, packed.rstrip()).replace(OLD_REG, '').replace(OLD_READ, '')


def prepare_project(directory):
    directory = Path(directory).resolve()
    qsf = directory/'apf_top.qsf'
    q = qsf.read_text()
    candidates = [p for kind, p in assignments(directory, q)
                  if kind == 'SYSTEMVERILOG_FILE' and p.name == 'psx_audio_sinc.sv']
    if len(candidates) != 1:
        raise ValueError('Expected one assigned Pocket sinc converter')
    original = candidates[0]
    before = original.read_text()
    adapt_text(before)  # reject unsupported source before creating anything
    token = '"'+str(original)+'"'
    if q.count(token) != 1:
        raise ValueError('Expected one quoted absolute sinc assignment')
    work = directory/'audio-rom-packed'
    if work.exists():
        raise ValueError('Packed audio ROM already prepared')
    source_sha = digest(original)
    files = []
    for kind, p in assignments(directory, q):
        if kind == 'SYSTEMVERILOG_FILE':
            files += re.findall(r'\.COEFFICIENT_FILE\("([^"\n]+)"\)', p.read_text())
    if len(set(files)) != 1:
        raise ValueError('Expected one literal selected coefficient file')
    bank_path = Path(files[0])
    if not bank_path.is_absolute():
        bank_path = directory/bank_path
    bank = verify_bank(bank_path)
    work.mkdir()
    files = packed_files(bank_path, work/'coefficients')
    after = adapt_text(before, files)
    output = work/'psx_audio_sinc.sv'
    output.write_text(after)
    if digest(original) != source_sha:
        raise ValueError('Sinc source changed while preparing')
    q = q.replace(token, '"'+str(output)+'"')
    for p in files:
        q += f'set_global_assignment -name MISC_FILE "{p}"\n'
    qsf.write_text(q)
    report = dict(original=str(original), original_sha256=source_sha,
                  path=str(output), sha256=digest(output), preparer_sha256=digest(Path(__file__)),
                  taps=128, phases=160, fine_fraction_bits=16,
                  signed_band_widths=[7, 10, 12, 18], words_per_band=1296,
                  coefficient_bank=bank,
                  packed_files=[dict(path=str(p), sha256=digest(p)) for p in files],
                  original_coefficient_bank_unchanged=True, cycle_schedule_changed=False,
                  fit_verified=False, timing_verified=False, hardware_verified=False)
    (work/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project', type=Path)
    print(json.dumps(prepare_project(p.parse_args().project), indent=2))
