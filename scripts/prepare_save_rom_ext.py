#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save device step "save-rom-ext": one more M10K of routine ROM (RTL: goes into the next fit).

The routine ROM (psx_save_exp1: 1024 x 40 bits, four M10K) is full. This step adds a second
ROM of 1024 x 8 bits, one M10K, 1024 more bytes:

  - psx_save_rom_ext (a module of its own in psx_save_exp1.sv, so that a fit writes its contents
    to a memory file of its own): address bus_addr[9:0], read every clk2x like the routine ROM,
    contents from save-states/psx_save_rom_ext.hex (1024 lines of one byte).
  - It is read in the second ROM's window, 0x1C00-0x1FFF, while the new register bit rom_bank is
    set. rom_bank is bit 1 of the SAVE_MODE write (0x180E): 1 = SAVE_MODE with the second ROM
    (as today), 3 = SAVE_MODE with the extension. Reset and the core reset edge clear it with
    SAVE_MODE. Nothing else changes: with rom_bank clear every read and every output is what it
    was, and no address of the routine moves.
  - Why 8 bits wide: in the ROM's own 40-bit packing one M10K is 256 x 40 = 1280 bytes, but a
    fifth 256 x 40 block needs a five-way lane select or a 40-bit block select in front of the
    read multiplexer. A 1024 x 8 block needs none: one more input on the second ROM's byte.
    The 13-bit EXP1 address space has no free 1 KiB (0x1840-0x1BFF is 960 bytes), hence the bank.
  - Cost expected: 1 M10K, 1 register, 8 two-way multiplexer bits.

Routine side (firmware_text): the stub's loader copies the second ROM's window a second time
with SAVE_MODE = 3, then writes 1 again; six instructions (24 bytes of the stub). The packed
stage 2 goes on in the extension (build_staged_save_fw.image_parts_ext). A routine without the
loader builds the same four blocks as before and an all-zero extension.

ROM update of a finished fit: scripts/update_save_fit.py replaces the routine ROM's and the
extension's memory files together (signoff-request.json save_firmware: rom40 + ext).

Check: scripts/run_save_rom_ext.py (device), run_save_stage2_mark.py --routine-step rom-ext (CPU).
Usage: prepare_save_rom_ext.py DEVICE.sv --text-only OUT              (one psx_save_exp1.sv)
       prepare_save_rom_ext.py SOURCE --output OUTPUT [--seed N]      (frozen project snapshot)
       prepare_save_rom_ext.py --routine ROUTINE.S --output DIR       (routine with the loader:
           DIR/staged-rom-ext.S, psx_save_rom40.hex, psx_save_rom_ext.hex, report.json)
"""
import argparse
import hashlib
import importlib.util
import json
import random
import re
import sys
from pathlib import Path

TAG = 'save-rom-ext'
EXT_NAME = 'psx_save_rom_ext.hex'
EXT_SIZE = 0x400


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f'{TAG}: expected {count} match(es), found {text.count(old)}: {old.strip()[:80]}')
    return text.replace(old, new)


# ---------------------------------------------------------------------------------------------
# the device
# ---------------------------------------------------------------------------------------------
PARAM_OLD = '    parameter ROM_HEX = "",     // 1024 x 40 bits: {second ROM byte i, first ROM word i}\n'
PARAM_NEW = PARAM_OLD + f'    parameter EXT_HEX = "",     // {TAG}: 1024 x 8 bits, the extension ROM (one byte per line)\n'

ROM_OLD = '    wire [7:0] rom1_q = rom_q[8*bus_addr[1:0] +: 8];\n'
EXT_INSTANCE = '    psx_save_rom_ext #(.HEX(EXT_HEX)) rom_ext (.clk(clk2x), .addr(bus_addr[9:0]), .q(ext_q));\n'
ROM_NEW = ROM_OLD + f'''\
    // {TAG}: one more M10K (1024 x 8) in the second ROM's window 0x1C00-0x1FFF while rom_bank
    // (SAVE_MODE write bit 1) is set. Read every clock with the second ROM's address bits.
    reg rom_bank = 1'b0;
    wire [7:0] ext_q;
''' + EXT_INSTANCE

MUX_OLD = '        else if (bus_addr[12] && bus_addr[11] && bus_addr[10]) bus_dataRead = rom2_q;\n'
MUX_NEW = '        else if (bus_addr[12] && bus_addr[11] && bus_addr[10]) bus_dataRead = rom_bank ? ext_q : rom2_q;   // save-rom-ext\n'

WRITE_OLD = "bus_addr[3:0] == 4'd14) save_mode <= bus_dataWrite[0];\n"
WRITE_NEW = "bus_addr[3:0] == 4'd14) begin save_mode <= bus_dataWrite[0]; rom_bank <= bus_dataWrite[1]; end   // save-rom-ext\n"

CLEAR_OLD = "save_mode <= 1'b0; cd_freeze <= 1'b0;"       # twice: reset_2x and the core reset edge
CLEAR_NEW = "save_mode <= 1'b0; rom_bank <= 1'b0; cd_freeze <= 1'b0;"

TAIL_OLD = 'endmodule\n`default_nettype wire\n'
MODULE = f'''\
endmodule

// {TAG}: the routine ROM's extension, one M10K (1024 x 8; read like the routine ROM: the address
// is taken on every clock). A module of its own: its contents get their own memory file in a fit,
// which a ROM update replaces together with the routine ROM's (scripts/update_save_fit.py).
module psx_save_rom_ext #(
    parameter HEX = ""          // 1024 lines of one byte
) (
    input  wire       clk,
    input  wire [9:0] addr,
    output reg  [7:0] q = 8'd0
);
    (* romstyle = "M10K" *) reg [7:0] rom [0:1023];
    initial if (HEX != "") $readmemh(HEX, rom);
    always @(posedge clk) q <= rom[addr];
endmodule
`default_nettype wire
'''

# Planted faults: (old, new) on the transformed device text.
FAULTS = {
    # extension address bits 0 and 1 swapped
    'addr_bits_swapped': [('.addr(bus_addr[9:0])', '.addr({bus_addr[9:2], bus_addr[0], bus_addr[1]})')],
    # the top address bit of the extension is the first ROM's (word address) bit
    'addr_top_bit': [('.addr(bus_addr[9:0])', '.addr({bus_addr[11], bus_addr[8:0]})')],
    # the extension byte's two halves in the wrong order
    'data_lanes_swapped': [('bus_dataRead = rom_bank ? ext_q : rom2_q;', 'bus_dataRead = rom_bank ? {ext_q[3:0], ext_q[7:4]} : rom2_q;')],
    # the bank is SAVE_MODE's own bit: the second ROM is never read while the routine unpacks
    'bank_is_save_mode': [('rom_bank <= bus_dataWrite[1]; end', 'rom_bank <= bus_dataWrite[0]; end')],
    # the core reset edge leaves the bank set
    'bank_survives_core_reset': [("save_mode <= 1'b0; rom_bank <= 1'b0; cd_freeze <= 1'b0; disc_silent",
                                  "save_mode <= 1'b0; cd_freeze <= 1'b0; disc_silent")],
    # the bank reaches the first ROM too (its top byte lane shows the extension)
    'bank_in_first_rom': [('    wire [7:0] rom1_q = rom_q[8*bus_addr[1:0] +: 8];\n',
                           "    wire [7:0] rom1_q = rom_bank && bus_addr[1:0] == 2'd3 ? ext_q : rom_q[8*bus_addr[1:0] +: 8];\n")],
}


def device_text(text):
    """psx_save_exp1.sv (a project's pinned copy) with the extension ROM."""
    if TAG in text or 'rom_bank' in text or 'psx_save_rom_ext' in text:
        raise ValueError(f'{TAG}: already applied')
    if 'core_reset' not in text:
        raise ValueError(f'{TAG}: run after prepare_save_core_reset')
    text = _once(text, PARAM_OLD, PARAM_NEW)
    text = _once(text, ROM_OLD, ROM_NEW)
    text = _once(text, MUX_OLD, MUX_NEW)
    text = _once(text, WRITE_OLD, WRITE_NEW)
    text = _once(text, CLEAR_OLD, CLEAR_NEW, count=2)
    if not text.endswith(TAIL_OLD) or text.count('endmodule') != 1:
        raise ValueError(f'{TAG}: the device file does not end with its one module')
    text = text[:-len(TAIL_OLD)] + MODULE
    if len(re.findall(r'\brom_bank\b', text)) != 6 or len(re.findall(r'\bext_q\b', text)) != 3:
        raise ValueError(f'{TAG}: unexpected use count')
    return text


def restore(text):
    """The device text before the step (the inverse of device_text, exact)."""
    text = _once(text, PARAM_NEW, PARAM_OLD)
    text = _once(text, ROM_NEW, ROM_OLD)
    text = _once(text, MUX_NEW, MUX_OLD)
    text = _once(text, WRITE_NEW, WRITE_OLD)
    text = _once(text, CLEAR_NEW, CLEAR_OLD, count=2)
    if not text.endswith(MODULE):
        raise ValueError(f'{TAG}: extension module not at the end')
    return text[:-len(MODULE)] + TAIL_OLD


INSTANCE = re.compile(r'psx_save_exp1 #\(\.ROM_HEX\("([^"\n]+)/psx_save_rom40\.hex"\), ')


def core_top_text(text):
    """core_top.sv: the instance names the extension's contents, next to the routine ROM's file."""
    if 'EXT_HEX' in text:
        raise ValueError(f'{TAG}: core_top already names the extension')
    found = INSTANCE.findall(text)
    if len(found) != 1:
        raise ValueError(f'{TAG}: expected one psx_save_exp1 instance with ROM_HEX, found {len(found)}')
    return INSTANCE.sub(lambda m: m[0] + f'.EXT_HEX("{m[1]}/{EXT_NAME}"), ', text)


def ext_hex(data=bytes(EXT_SIZE)):
    if len(data) != EXT_SIZE:
        raise ValueError(f'{TAG}: the extension is {EXT_SIZE} bytes')
    return ''.join(f'{b:02x}\n' for b in data)


def fault_text(text, fault):
    for old, new in FAULTS[fault]:
        text = _once(text, old, new)
    return text


# ---------------------------------------------------------------------------------------------
# the routine: the stub's loader reads the extension
# ---------------------------------------------------------------------------------------------
LOADER_OLD = '''\
        ori     $t3, $t5, 0x1C00        # ... the rest is the second ROM
        ori     $t6, $t5, 0x2000
4:      lw      $t0, 0($t3)
        addiu   $t3, $t3, 4
        sw      $t0, 0($t1)
        bne     $t3, $t6, 4b
        addiu   $t1, $t1, 4
        lw      $t6, 4($a0)             # unpacked length
'''
LOADER_NEW = '''\
        li      $t2, 1                  # rom-ext: the SAVE_MODE value, bit 1 = the window shows the extension ROM
rom_ext:                                # (build_staged_save_fw.py: with this label the stream goes on in the extension)
        ori     $t3, $t5, 0x1C00        # ... the rest is the second ROM, then the extension ROM in the same window
        ori     $t6, $t5, 0x2000
4:      lw      $t0, 0($t3)
        addiu   $t3, $t3, 4
        sw      $t0, 0($t1)
        bne     $t3, $t6, 4b
        addiu   $t1, $t1, 4
        xori    $t2, $t2, 2             # rom-ext: 3 after the second ROM (copy the window again), 1 after the extension.
        sb      $t2, SAVE_MODE($k0)     # Bit 0 stays set: SAVE_MODE itself does not change
        andi    $t0, $t2, 2
        bnez    $t0, rom_ext
        nop
        lw      $t6, 4($a0)             # unpacked length
'''
# Planted routine faults: (old, new) on the routine with the loader.
ROUTINE_FAULTS = {
    # the window is copied twice without the bank: the second ROM's bytes again
    'no-bank': [('        sb      $t2, SAVE_MODE($k0)     # Bit 0 stays set: SAVE_MODE itself does not change\n',
                 '        nop                             # (fault: no SAVE_MODE write)\n')],
}
PAD_AT = '''\
stage2:
        .word   0                       # stamp (build_save_fw.py: CRC-32 of the rest)
'''
PAD_SEED = 20261112


def firmware_text(text):
    """A staged routine (staged_handler.S or any routine step's output) whose stub reads the extension ROM."""
    if 'rom_ext' in text:
        raise ValueError(f'{TAG}: the routine already reads the extension')
    return _once(text, LOADER_OLD, LOADER_NEW)


def routine_fault_text(text, fault):
    for old, new in ROUTINE_FAULTS[fault]:
        text = _once(text, old, new)
    return text


def pad_text(text, count):
    """Bench only: COUNT seeded random bytes (they do not pack) at the start of stage 2, after its
    stamp, so that the packed stream of the code behind them reaches into the extension ROM."""
    if count <= 0 or count % 4:
        raise ValueError(f'{TAG}: the pad is a positive number of words')
    rng = random.Random(PAD_SEED)
    rows = ''.join('        .byte   ' + ', '.join(f'0x{rng.randrange(256):02X}' for _ in range(16)) + '\n'
                   for _ in range(count // 16))
    if count % 16:
        rows += '        .byte   ' + ', '.join(f'0x{rng.randrange(256):02X}' for _ in range(count % 16)) + '\n'
    return _once(text, PAD_AT, PAD_AT + f'        # rom-ext bench pad: {count} bytes that do not pack (never executed)\n' + rows)


def builder():
    """This checkout's build_staged_save_fw.py, whatever module of that name is first on the path
    (the CPU bench puts a fixture checkout's scripts first)."""
    path = Path(__file__).resolve().with_name('build_staged_save_fw.py')
    spec = importlib.util.spec_from_file_location('build_staged_save_fw_rom_ext', path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    if not hasattr(module, 'image_parts_ext'):
        raise ValueError(f'{TAG}: {path} has no image_parts_ext')
    return module


def cpu_bench_text(text):
    """The CPU bench (sim/reference/tb_staged_save_cpu.vhd after run_save_stage2_mark.bench_text) with
    the extension in its device model: rom_ext.hex (256 words) in the second ROM's window while
    SAVE_MODE bit 1 is set; a count of the extension bytes read; and, when SAVE_MODE falls, a
    compare of all of stage 2's SDRAM with stage2.hex."""
    text = _once(text, '   constant exp1_rom : words(0 to 2047) := load("rom_exp1.hex", 2048, false);\n',
                 '   constant exp1_rom : words(0 to 2047) := load("rom_exp1.hex", 2048, false);\n'
                 '   constant ext_rom : words(0 to 255) := load("rom_ext.hex", 256, false);   -- rom-ext bench: the extension ROM\n'
                 "   signal rom_bank_s : std_logic := '0';\n"
                 '   signal ext_reads : natural := 0;\n'
                 '   signal bank_sets : natural := 0;\n')
    text = _once(text, '         if a < 16#1000# or a >= 16#1C00# then\n            w := exp1_rom(a / 4);\n',
                 "         if a >= 16#1C00# and rom_bank_s = '1' then\n"
                 '            w := ext_rom((a - 16#1C00#) / 4);\n'
                 '            exp1_q <= w(8 * (a mod 4) + 7 downto 8 * (a mod 4));\n'
                 "            if mux_bus_exp1_write = '0' then ext_reads <= ext_reads + 1; end if;\n"
                 '         elsif a < 16#1000# or a >= 16#1C00# then\n            w := exp1_rom(a / 4);\n')
    text = _once(text, "         if mux_bus_exp1_write = '1' and a = 16#180E# then save_mode_s <= mux_bus_exp1_dataWrite(0); end if;\n",
                 "         if mux_bus_exp1_write = '1' and a = 16#180E# then save_mode_s <= mux_bus_exp1_dataWrite(0); end if;\n"
                 "         if mux_bus_exp1_write = '1' and a = 16#180E# then\n"
                 "            if mux_bus_exp1_dataWrite(1) = '1' and rom_bank_s = '0' then bank_sets <= bank_sets + 1; end if;\n"
                 '            rom_bank_s <= mux_bus_exp1_dataWrite(1);\n'
                 '         end if;\n')
    text = _once(text, '      variable warm_done : boolean := false;\n',
                 '      variable warm_done : boolean := false;\n'
                 "      variable mode_was : std_logic := '0';     -- rom-ext bench\n"
                 '      variable image_bad : natural := 0;\n')
    text = _once(text, "         at_cpu_edge := clk1x = '1' and clk1x'last_event = 0 ns;\n         cache_wr <= \"0000\";\n",
                 "         if mode_was = '1' and save_mode_s = '0' then\n"
                 '            image_bad := 0;\n'
                 '            for k in 0 to 2047 loop\n'
                 '               if high(k) /= stage2_ref(k) then image_bad := image_bad + 1; end if;\n'
                 '            end loop;\n'
                 '            report "ROMEXT stage2 image words_differing=" & integer\'image(image_bad) severity note;\n'
                 '            assert image_bad = 0 report "rom-ext: stage 2 in SDRAM differs from stage2.hex" severity failure;\n'
                 '         end if;\n'
                 '         mode_was := save_mode_s;\n'
                 "         at_cpu_edge := clk1x = '1' and clk1x'last_event = 0 ns;\n         cache_wr <= \"0000\";\n")
    text = _once(text, '                  report "PASS save trap run";\n',
                 '                  report "ROMEXT ext_reads=" & integer\'image(ext_reads) & " bank_sets=" & integer\'image(bank_sets) severity note;\n'
                 '                  report "PASS save trap run";\n')
    return text


class RoutineStep:
    """The routine step as run_save_project_cpu.py takes one (--routine-step rom-ext)."""
    FAULTS = ROUTINE_FAULTS
    firmware_text = staticmethod(firmware_text)
    fault_text = staticmethod(routine_fault_text)


def cpu_log_check(text, used, cold=True):
    """The ROMEXT lines of a bench log (checks.log) -> (rows, failures). used: extension bytes the
    routine's packed stream takes. A run that unpacked (it read the extension) must have set the
    bank exactly once, and every run's stage 2 in SDRAM must equal stage2.hex when SAVE_MODE falls."""
    images = [int(n) for n in re.findall(r'ROMEXT stage2 image words_differing=(\d+)', text)]
    runs = [(int(a), int(b)) for a, b in re.findall(r'ROMEXT ext_reads=(\d+) bank_sets=(\d+)', text)]
    failures = []
    if not runs:
        failures.append('rom-ext: no run reported its extension reads')
    if any(images):
        failures.append(f'rom-ext: stage 2 in SDRAM differs from stage2.hex ({images})')
    if not images:
        failures.append('rom-ext: stage 2 in SDRAM was never compared')
    if any((reads > 0) != (sets == 1) or sets > 1 for reads, sets in runs):
        failures.append(f'rom-ext: extension reads without exactly one bank set: {runs}')
    # (cold: a scenario of the run starts without stage 2 in SDRAM; a warm run never reads the ROM's stream)
    if cold and used and not any(reads >= 1024 for reads, _ in runs):
        failures.append(f'rom-ext: no run read the whole extension although the stream uses {used} bytes of it: {runs}')
    return dict(extension_reads_and_bank_sets=runs, stage2_image_words_differing=images), failures


def routine(source, output):
    """ROUTINE.S -> OUTPUT/staged-rom-ext.S (with the loader), the two ROM files and report.json."""
    b = builder()
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    old_text = source.read_text()
    old_image, old_stage, old_ext = b.image_parts_ext(source=source)
    out = output / 'staged-rom-ext.S'
    out.write_text(firmware_text(old_text))
    image, stage, ext = b.image_parts_ext(source=out)
    if len(stage) != len(old_stage):      # (its words differ where it names stub addresses, which move)
        raise ValueError(f'{TAG}: stage 2 changed its length')
    (output / 'psx_save_rom40.hex').write_text(b.rom40_hex(b.rom1(image), b.rom2(image)))
    (output / EXT_NAME).write_text(ext_hex(ext))
    report = dict(source=str(source), source_sha256=sha(old_text), routine_sha256=sha(out.read_text()),
                  stage2_bytes=len(stage), free_before=b.free_bytes(old_image), free_after=b.free_bytes(image, ext),
                  extension_bytes_used=len(ext.rstrip(b'\0')),
                  rom40_sha256=sha((output / 'psx_save_rom40.hex').read_text()),
                  ext_sha256=sha((output / EXT_NAME).read_text()))
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


# ---------------------------------------------------------------------------------------------
# frozen project step
# ---------------------------------------------------------------------------------------------
def prepare(source, output, seed=None):
    """Snapshot SOURCE into OUTPUT, patch the device and core_top there, add the all-zero extension file."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    names = {'psx_save_exp1.sv': device_text, 'core_top.sv': core_top_text}
    rows = {n: [r for r in manifest['quartus_sources'] if Path(r['path']).name == n] for n in names}
    rom = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'psx_save_rom40.hex']
    if any(len(r) != 1 for r in rows.values()) or len(rom) != 1:
        raise SystemExit(f'{TAG}: {source} does not compile one psx_save_exp1.sv, one core_top.sv and one routine ROM')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    changed = {}
    for name, transform in names.items():
        path = next(Path(r['path']) for r in before if Path(r['path']).name == name)
        # the snapshot relocates the project paths written in core_top.sv; the device has none
        if name == 'psx_save_exp1.sv' and digest(path) != rows[name][0]['sha256']:
            raise SystemExit(f'{TAG}: snapshot changed {name}')
        changed[str(path)] = digest(path)
        path.write_text(transform(path.read_text()))
    new_rom = next(Path(r['path']) for r in before if Path(r['path']).name == 'psx_save_rom40.hex')
    if digest(new_rom) != rom[0]['sha256']:
        raise SystemExit(f'{TAG}: snapshot changed the routine ROM')
    ext = new_rom.with_name(EXT_NAME)
    if ext.exists():
        raise SystemExit(f'{TAG}: {ext} exists')
    ext.write_text(ext_hex())
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if set(a) - set(b) != {str(ext)} or set(b) - set(a) or {p for p in b if a[p] != b[p]} != set(changed):
        raise SystemExit(f'{TAG}: expected psx_save_exp1.sv and core_top.sv to change and {EXT_NAME} to be added, nothing else')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    if any(digest(Path(r[0]['path'])) != r[0]['sha256'] for r in list(rows.values()) + [rom]):
        raise SystemExit(f'{TAG}: the source project changed')
    return seed, {p: (old, digest(Path(p))) for p, old in changed.items()}, ext


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path, nargs='?')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--text-only', type=Path, metavar='OUT')
    parser.add_argument('--routine', type=Path, metavar='ROUTINE.S')
    args = parser.parse_args()
    try:
        if args.routine:
            if not args.output or args.source:
                parser.error('--routine ROUTINE.S --output DIR')
            for key, value in routine(args.routine, args.output).items():
                print(f'{TAG}: {key} = {value}')
        elif args.text_only:
            if not args.source or args.text_only.resolve() == args.source.resolve():
                raise SystemExit(f'{TAG}: refusing to overwrite the input')
            args.text_only.write_text(device_text(args.source.read_text()))
            print(f'{TAG}: wrote {args.text_only} (sha256 {sha(args.text_only.read_text())})')
        else:
            if not args.output or not args.source:
                parser.error('SOURCE --output OUTPUT required')
            seed, changed, ext = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            for path, (old, new) in changed.items():
                print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
            print(f'{TAG}: added {ext} ({EXT_SIZE} zero bytes); 1 M10K, 1 register; no SDC change')
    except ValueError as error:
        raise SystemExit(str(error))
