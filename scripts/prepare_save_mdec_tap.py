#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save states: the MDEC's quantisation and scale tables through a read tap.

Design, step-1 findings and bench results: docs/save-states-finish/mdec-tap.md.
A game writes the tables once per FMV (MDEC commands 2 and 3) and cannot read
them back. A state loaded into a core that has not seen those commands (wake
from Sleep, load during the BIOS logo) decodes the rest of the FMV with the
tables the core happens to hold. This step lets the save routine read them:

Hardware (a frozen project step; five files, no RAM, no shadow copy):
  mdec.vhd      save_tap_on = 1 puts save_tap_adr on the read address of the
                quantisation RAM (iramYUV port B, bytes 0-63 luminance, 64-127
                chrominance) and save_tap_adr(6:1) on the read address of the
                first scale RAM (iscaleTable1 port B). Their outputs leave the
                entity. With save_tap_on = 0 nothing changes.
  psx_top.vhd, psx_mister_psx_top_resource.vhd, core_top.sv   wiring; the tap
                address is the save device's existing spu_ss_adr (SS_ADR).
  psx_save_exp1.sv
    0x180C write SPU_HOLD: [1] MDEC table tap on (new; [0] is the SPU hold).
          Cleared wherever the hold is: reset, and the core reset of save-core-reset.
    0x1824 read: the quantisation byte at SS_ADR[6:0].
    0x1825 read: byte SS_ADR[0] of scale word SS_ADR[6:1] (0 = bits 7:0).
    (0x1826/7 and the 0x182C, 0x1834, 0x183C groups alias the two: only
    address bits 2 and 0 are decoded.)
  Cost, counted from the added logic: 1 register (mdec_tap), 13 two-way
  address multiplexer bits (7 + 6) and 8 three-way read data bits, the last
  merged into the device's read multiplexer. No M10K, no DSP.

Routine (firmware_text, a step after the big-build routine):
  save   after the R1 words, before page 0 is stored: if the MDEC is idle for
         MDEC_LOOKS looks, the 128 quantisation bytes go to page 0 bytes
         0x200-0x27F and the 128 scale bytes to 0x280-0x2FF (words 128-191,
         free until now), and bit 19 of the stored MDEC status (page 0 word
         59; the bit always reads 0) is set: "tables in the state".
  load   after the MDEC reset: if bit 19 is set, command 2 (0x40000001) with
         the 32 quantisation words and command 3 (0x60000000) with the 32
         scale words, then the R1 decode command and enables as before.
  The format magic does not change: a state without bit 19 loads as before
  (tables left alone), and an older routine ignores bit 19 and words 128-191.

Check: scripts/run_save_mdec_tap.py.
Usage: prepare_save_mdec_tap.py SOURCE --output OUTPUT [--seed N]   (frozen project snapshot)
       prepare_save_mdec_tap.py --routine OUT                       (big-build routine + this step)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'save-mdec-tap'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected one match, found {text.count(old)}: {old.strip()[:90]!r}')
    return text.replace(old, new)


def after(text, anchor, added):
    return once(text, anchor, anchor + added)


# ---------------------------------------------------------------------------
# hardware
# ---------------------------------------------------------------------------
MDEC_PORT_OLD = '      dma_readdata         : out std_logic_vector(31 downto 0);\n'
MDEC_PORT_NEW = (MDEC_PORT_OLD +
                 f'\n      -- {TAG}: read tap on the quantisation and scale tables (prepare_save_mdec_tap.py).\n'
                 '      -- While save_tap_on = 1 the tables are read at save_tap_adr; only while the MDEC is idle.\n'
                 "      save_tap_on          : in  std_logic := '0';\n"
                 "      save_tap_adr         : in  unsigned(6 downto 0) := (others => '0');\n"
                 '      save_tap_yuv         : out std_logic_vector(7 downto 0);\n'
                 '      save_tap_scale       : out std_logic_vector(15 downto 0);\n')
YUV_OLD = "   RamYUVaddrB <= '0' & currentCoeff(5 downto 0) when (currentBlock >= 2 or rec_depth(1) = '0') else '1' & currentCoeff(5 downto 0);"
YUV_NEW = ("   RamYUVaddrB <= save_tap_adr when (save_tap_on = '1') else   -- " + TAG + "\n"
           "                  '0' & currentCoeff(5 downto 0) when (currentBlock >= 2 or rec_depth(1) = '0') else '1' & currentCoeff(5 downto 0);")
YUV_OUT = '   save_tap_yuv <= RamYUVdataB;\n'
SCALE_OLD = ('   scaleT1_address_b <= std_logic_vector(to_unsigned(idct_u  * 8 + idct_y, 6)) when (idctState = IDCT_STAGE1) else '
             'std_logic_vector(to_unsigned(idct_u  * 8 + idct_x, 6));\n')
SCALE_ADR = "   scaleT1_address_b <= std_logic_vector(save_tap_adr(6 downto 1)) when (save_tap_on = '1') else   -- " + TAG + "\n"
SCALE_NEW = (SCALE_ADR +
             '                        std_logic_vector(to_unsigned(idct_u  * 8 + idct_y, 6)) when (idctState = IDCT_STAGE1) else '
             'std_logic_vector(to_unsigned(idct_u  * 8 + idct_x, 6));\n')
SCALE_OUT = '   save_tap_scale <= scaleT1_data_b;\n'


def mdec(text):
    """mdec.vhd (the project's mdec-beetle chain) with the table tap."""
    if TAG in text or 'save_tap' in text:
        raise ValueError(f'{TAG}: mdec.vhd already has the tap')
    # the two read ports the tap borrows: one address assignment each, nothing else drives them
    if len(re.findall(r'\bRamYUVaddrB\s*<=', text)) != 1 or len(re.findall(r'\bscaleT1_address_b\s*<=', text)) != 1:
        raise ValueError(f'{TAG}: table read addresses changed')
    text = once(text, MDEC_PORT_OLD, MDEC_PORT_NEW)
    text = once(text, YUV_OLD, YUV_NEW)
    text = once(text, SCALE_OLD, SCALE_NEW + YUV_OUT + SCALE_OUT)
    return text


def psx_top(text):
    text = once(text, '      save_spuram_done : out std_logic\n   );\n',
                '      save_spuram_done : out std_logic;\n'
                f"      save_mdec_tap : in std_logic := '0';   -- {TAG}\n"
                '      save_mdec_q : out std_logic_vector(23 downto 0)\n   );\n')
    text = once(text, '      dma_readdata         => DMA_MDEC_read,\n',
                '      dma_readdata         => DMA_MDEC_read,\n'
                f'      save_tap_on          => save_mdec_tap,   -- {TAG}: the address is the save device\'s SS_ADR\n'
                '      save_tap_adr         => save_spu_adr(6 downto 0),\n'
                '      save_tap_yuv         => save_mdec_q(7 downto 0),\n'
                '      save_tap_scale       => save_mdec_q(23 downto 8),\n')
    return text


def resource(text):
    text = once(text, '    save_spuram_done : out std_logic\n);\n',
                '    save_spuram_done : out std_logic;\n'
                f'    save_mdec_tap : in std_logic;   -- {TAG}\n'
                '    save_mdec_q : out std_logic_vector(23 downto 0)\n);\n')
    text = once(text, '        save_spuram_done => save_spuram_done\n    );\n',
                '        save_spuram_done => save_spuram_done,\n'
                '        save_mdec_tap => save_mdec_tap,\n'
                '        save_mdec_q => save_mdec_q\n    );\n')
    return text


def core_top(text):
    text = after(text, '    wire [18:0] save_spuram_adr; wire [15:0] save_spuram_wdata, save_spuram_rdata;\n',
                 f'    wire save_mdec_tap; wire [23:0] save_mdec_q;   // {TAG}\n')
    text = once(text, '        .cd_state(save_cd_state));\n',
                '        .mdec_tap(save_mdec_tap), .mdec_q(save_mdec_q),\n        .cd_state(save_cd_state));\n')
    text = once(text, '.save_spuram_done(save_spuram_done),', '.save_spuram_done(save_spuram_done),.save_mdec_tap(save_mdec_tap),.save_mdec_q(save_mdec_q),')
    return text


EXP1_PORT_OLD = '    input  wire        spuram_done,\n'
EXP1_PORT_NEW = (EXP1_PORT_OLD +
                 f'    // {TAG}: SPU_HOLD (0x180C) bit 1 puts spu_ss_adr[6:0] on the MDEC\'s table read addresses;\n'
                 '    // 0x1824 reads the quantisation byte, 0x1825 byte spu_ss_adr[0] of scale word spu_ss_adr[6:1]\n'
                 "    output reg         mdec_tap = 1'b0,\n"
                 '    input  wire [23:0] mdec_q,          // {scale word, quantisation byte}\n')
EXP1_READ_OLD = "        if (reg_area && bus_addr[5]) bus_dataRead = bus_addr[4:0] == 5'd2 ? {7'd0, ss_busy} :\n"
EXP1_READ_TAP = ("        if (reg_area && bus_addr[5]) bus_dataRead = bus_addr[2] ? (!bus_addr[0] ? mdec_q[7:0] : ss_adr_1x[0] ? mdec_q[23:16] : mdec_q[15:8]) :   // "
                 + TAG + "\n")
EXP1_READ_NEW = EXP1_READ_TAP + "                                                    bus_addr[4:0] == 5'd2 ? {7'd0, ss_busy} :\n"
EXP1_CLEAR_OLD = "spu_hold <= 1'b0;"       # the reset branch, and save-core-reset's branch where the project has it
EXP1_CLEAR_NEW = "spu_hold <= 1'b0; mdec_tap <= 1'b0;"
EXP1_WRITE_OLD = "                4'd12: spu_hold <= bus_dataWrite[0];\n"
EXP1_WRITE_NEW = "                4'd12: begin spu_hold <= bus_dataWrite[0]; mdec_tap <= bus_dataWrite[1]; end   // " + TAG + "\n"


def exp1(text):
    if 'save-disc-set' not in text or 'ss_adr_1x' not in text:
        raise ValueError(f'{TAG}: psx_save_exp1.sv needs the SPU scan address (SS_ADR) and save-disc-set')
    text = once(text, EXP1_PORT_OLD, EXP1_PORT_NEW)
    text = once(text, EXP1_READ_OLD, EXP1_READ_NEW)
    clears = text.count(EXP1_CLEAR_OLD)
    if clears != (2 if 'save-core-reset' in text else 1):
        raise ValueError(f'{TAG}: {clears} places clear spu_hold')
    text = text.replace(EXP1_CLEAR_OLD, EXP1_CLEAR_NEW)
    text = once(text, EXP1_WRITE_OLD, EXP1_WRITE_NEW)
    return text


# Planted hardware faults for the benches (never a fitted input): name -> (file, old, new) on the step's text
HW_FAULTS = {
    # the scale tap reads one word further (address taken from bits 5:0, not 6:1)
    'scale-address': ('mdec', SCALE_ADR, SCALE_ADR.replace('save_tap_adr(6 downto 1)', 'save_tap_adr(5 downto 0)')),
    # the quantisation tap is off by one byte
    'yuv-address': ('mdec', "   RamYUVaddrB <= save_tap_adr when (save_tap_on = '1')", "   RamYUVaddrB <= (save_tap_adr + 1) when (save_tap_on = '1')"),
    # the tap is not switched off: the decoder reads its tables at the tap address (lockstep, save mode off)
    'tap-stuck-on': ('mdec', "   RamYUVaddrB <= save_tap_adr when (save_tap_on = '1')", "   RamYUVaddrB <= save_tap_adr when (save_tap_on = '0')"),
    # the scale tap also moves the address while the tap is off (lockstep, save mode off)
    'scale-stuck-on': ('mdec', SCALE_ADR, SCALE_ADR.replace("(save_tap_on = '1')", "(save_tap_on = '0')")),
    # the device hands out the scale halves swapped
    'device-halves': ('exp1', 'ss_adr_1x[0] ? mdec_q[23:16] : mdec_q[15:8]', 'ss_adr_1x[0] ? mdec_q[15:8] : mdec_q[23:16]'),
    # SPU_HOLD bit 1 never reaches the tap
    'device-no-tap': ('exp1', 'mdec_tap <= bus_dataWrite[1]; end', "mdec_tap <= 1'b0; end"),
}
TRANSFORMS = {'mdec': mdec, 'exp1': exp1, 'psx_top': psx_top, 'resource': resource, 'core_top': core_top}
FILES = (('mdec', 'mdec.vhd', 'VHDL_FILE'), ('exp1', 'psx_save_exp1.sv', 'SYSTEMVERILOG_FILE'), ('psx_top', 'psx_top.vhd', 'VHDL_FILE'),
         ('resource', 'psx_mister_psx_top_resource.vhd', 'VHDL_FILE'), ('core_top', 'core_top.sv', 'SYSTEMVERILOG_FILE'))


def hw_fault(kind, text, name):
    """The transformed text of file `kind` with one planted fault (bench only)."""
    target, old, new = HW_FAULTS[name]
    if target != kind:
        raise ValueError(f'{TAG}: fault {name} is in {target}')
    return once(text, old, new)


def compiled(project, name, kind):
    qsf = (project / 'apf_top.qsf').read_text()
    hits = sorted({l.split('"')[1] for l in qsf.splitlines() if kind in l and l.count('"') >= 2 and l.split('"')[1].endswith('/' + name)})
    if len(hits) != 1 or not Path(hits[0]).is_relative_to(project):
        raise ValueError(f'{TAG}: expected one compiled {name} inside the snapshot, found {hits}')
    return Path(hits[0])


def prepare(source, output, seed=None):
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    load_source(source, source / 'fit-inputs.json')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    changed = {}
    for kind, name, assignment in FILES:
        path = compiled(output, name, assignment)
        old = path.read_text()
        path.write_text(TRANSFORMS[kind](old))
        changed[str(path)] = (sha(old), sha(path.read_text()))
    current = inputs(output)
    a, b = ({r['path']: r for r in rows} for rows in (current, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != set(changed):
        raise ValueError(f'{TAG}: unexpected input changes')
    hold_guard(output, current)
    manifest['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    report = dict(source=str(source), seed=seed, changed={p: dict(before=o, after=n) for p, (o, n) in sorted(changed.items())},
                  firmware_integrated=False,
                  note='the routine ROM is not part of this step: build it with --routine and install it as the other routine steps')
    (output / f'{TAG}.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


# ---------------------------------------------------------------------------
# routine
# ---------------------------------------------------------------------------
EQUATES_ANCHOR = '        .equ    MDEC_LOOKS, 1024        # MDEC idle for at least 8 clk1x a look, 0.24 ms or more\n'
EQUATES = '''\
        # mdec-tap (scripts/prepare_save_mdec_tap.py, docs/save-states-finish/mdec-tap.md)
        .equ    MDEC_TAP, 0x1824        # read: quantisation byte SS_ADR[6:0]; +1: scale byte SS_ADR[6:0]
        .equ    MT_TABLES, PAGE+0x200   # page 0 words 128-159: quantisation (luminance, chrominance),
                                        # 160-191: scale; R1_MDEC bit 19 = 1: they are in the state
        .equ    MT_FLAG, MT_TABLES+256  # page 0 word 192 byte 0: 1 = this request read the tables
'''
# The table read sits in quiet:, directly after the save's MDEC idle check and BEFORE cd_quiet takes
# the CD state, so nothing of this step runs between the CD quiet point and CD_FREEZE (the first
# version read the tables in save:, after cd_quiet; the CPU round-trip bench's drive then read on
# four sectors where one is allowed). No second idle check: mdec_quiet has just returned, and its
# last look is still in t0 (masked status) and t5 (the idle value). Idle there holds until the save
# is done: the guest executes nothing from the trap to the routine's exit, quiet: found no DMA
# channel running a few instructions earlier (only the CPU can start one), so nothing can give the
# MDEC a command or data, and a table command waiting for words shows as busy (status 29).
READ_OLD = '''\
        jal     mdec_quiet              # R1: no MDEC command, no MDEC data on its way (t2 = first half)
        nop
        bnez    $v0, retry
        nop
'''
READ_TAP = '''\
        # mdec-tap: the MDEC's quantisation and scale tables, which no register reads back,
        # through the save device's tap, into the staging page (page 0 of the state). Here,
        # before cd_quiet: nothing of this runs between the CD quiet point and CD_FREEZE.
        # Only with the MDEC idle on mdec_quiet's looks just now (t0 = its last look, t5 =
        # idle; from the second half of the retries a busy MDEC no longer stops a save): a
        # decode would read its tables at the tap's address. MT_FLAG tells save: the result.
        bne     $t0, $t5, 62f
        sb      $zero, MT_FLAG($k0)
        li      $t1, 128                # pass 128 is the settling pass: its bytes land in
        sb      $t1, SS_ADR($k0)        # MT_TABLES+128 (pass 0 writes it again) and MT_FLAG (set below)
        li      $t0, 2
        sb      $t0, SPU_HOLD($k0)      # tap on (bit 1); the SPU is not held yet (bit 0)
60:     addu    $t4, $k0, $t1
        lbu     $t0, MDEC_TAP($k0)
        lbu     $t5, MDEC_TAP+1($k0)
        addiu   $t1, $t1, -1
        sb      $t1, SS_ADR($k0)        # the next pass's address, three bus accesses ahead
        sb      $t0, MT_TABLES($t4)
        bgez    $t1, 60b
        sb      $t5, MT_TABLES+128($t4)
        sb      $zero, SPU_HOLD($k0)    # tap off
        li      $t0, 1
        sb      $t0, MT_FLAG($k0)
62:
'''
READ_NEW = READ_OLD + READ_TAP
SAVE_OLD = '''\
        sw      $t4, R1_BAUD($k0)
        jal     put_page
        move    $a0, $zero
'''
SAVE_TAP = '''\
        # mdec-tap: bit 19 of the stored MDEC status (always read as 0) = the tables are in
        # the state (MT_FLAG, from the table read in quiet:)
        lbu     $t0, MT_FLAG($k0)
        lw      $t2, R1_MDEC($k0)
        sll     $t0, $t0, 19
        or      $t2, $t2, $t0
        sw      $t2, R1_MDEC($k0)
65:
'''
SAVE_NEW = SAVE_OLD.replace('        jal     put_page\n', SAVE_TAP + '        jal     put_page\n')
LOAD_OLD = '''\
        lw      $t3, R1_MDEC($k0)
        lui     $t0, 0x8000
        sw      $t0, 0x1824($t9)
        srl     $t0, $t3, 23            # status 26:23 -> command 28:25
'''
LOAD_TAP = '''\
        # mdec-tap: the tables of the state (R1_MDEC bit 19), as a game sends them: command 2
        # (quantisation, luminance and chrominance, 32 words), then command 3 (scale, 32 words).
        # Both leave status 15:0 and 26:23 for the decode command below to set.
        sll     $t0, $t3, 12
        bgez    $t0, 63f
        lui     $t0, 0x4000
        ori     $t0, $t0, 1
        addiu   $t4, $k0, MT_TABLES
61:     sw      $t0, 0x1820($t9)
        addiu   $t5, $t4, 128
64:     lw      $t1, 0($t4)
        addiu   $t4, $t4, 4
        bne     $t4, $t5, 64b
        sw      $t1, 0x1820($t9)
        lui     $t1, 0x6000
        bne     $t0, $t1, 61b
        move    $t0, $t1
63:
'''
LOAD_NEW = LOAD_OLD.replace('        srl     $t0, $t3, 23', LOAD_TAP + '        srl     $t0, $t3, 23')

# Planted routine faults for the bench (never a fitted input): name -> (old, new) on the step's text
FAULTS = {
    # the table words are taken 64 bytes further on: chrominance lands in the luminance table, half the
    # scale table in the chrominance table, and command 3 gets what follows the tables
    'resend-swapped': ('        addiu   $t4, $k0, MT_TABLES\n61:', '        addiu   $t4, $k0, MT_TABLES+64\n61:'),
    # the scale table is not sent again (command 2 only)
    'resend-no-scale': ('        bne     $t0, $t1, 61b\n        move    $t0, $t1\n', '        nop\n        move    $t0, $t1\n'),
    # the save stores the scale bytes one place off
    'save-scale-offset': ('        sb      $t5, MT_TABLES+128($t4)\n', '        sb      $t5, MT_TABLES+129($t4)\n'),
    # the save never sets "tables in the state"
    'save-no-flag': ('        or      $t2, $t2, $t0\n        sw      $t2, R1_MDEC($k0)\n65:', '        nop\n        sw      $t2, R1_MDEC($k0)\n65:'),
    # the tap is read without being switched on
    'save-tap-off': ('        li      $t0, 2\n        sb      $t0, SPU_HOLD($k0)      # tap on', '        li      $t0, 0\n        sb      $t0, SPU_HOLD($k0)      # tap on'),
}


def firmware_text(text):
    """The big-build routine (R1 applied) with the table tap. Exact anchors; a miss raises."""
    if 'MDEC_TAP' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    for label in ('60', '61', '62', '63', '64', '65'):
        if re.search(rf'^{label}:|[ ,]{label}[fb]\b', text, flags=re.M):
            raise ValueError(f'{TAG}: numeric label {label} is in use')
    if 'SS_ADR, 0x1820' not in text or 'mdec_quiet:' not in text:
        raise ValueError(f'{TAG}: the routine needs the SPU scan registers and R1')
    text = after(text, EQUATES_ANCHOR, EQUATES)
    text = once(text, READ_OLD, READ_NEW)
    text = once(text, SAVE_OLD, SAVE_NEW)
    text = once(text, LOAD_OLD, LOAD_NEW)
    return text


def fault_text(text, name):
    old, new = FAULTS[name]
    return once(text, old, new)


def big_build_text():
    from prepare_save_big_build_routine import firmware_text as big
    from prepare_spu_exact_voices import pxs6_firmware
    return big(pxs6_firmware())


def build(output_dir):
    """The big-build routine with and without the step: sizes, and the ROM image."""
    from build_staged_save_fw import image_parts, rom1, rom2, rom40_hex, free_bytes
    from prepare_save_r1 import stub_bytes, packed_bytes
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, text in (('big-build', big_build_text()), ('mdec-tap', firmware_text(big_build_text()))):
        source = output_dir / f'staged-{name}.S'
        source.write_text(text)
        image, stage = image_parts(source=source)           # stops if the packed stage 2 does not fit
        stub = stub_bytes(source)
        rows[name] = dict(source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), stub_bytes=len(stub),
                          stage2_bytes=len(stage), packed_bytes=packed_bytes(image, len(stub)), free_bytes=free_bytes(image))
    for name in FAULTS:                                     # every planted fault applies and assembles
        faulty = output_dir / f'staged-mdec-tap-fault-{name}.S'
        faulty.write_text(fault_text(firmware_text(big_build_text()), name))
        image_parts(source=faulty)
    rom = output_dir / 'psx_save_rom40.hex'
    image, _ = image_parts(source=output_dir / 'staged-mdec-tap.S')
    rom.write_text(rom40_hex(rom1(image), rom2(image)))
    a, b = rows['big-build'], rows['mdec-tap']
    if a['stub_bytes'] != b['stub_bytes']:
        raise ValueError(f'{TAG}: the stub changed')
    report = dict(rows, rom=str(rom), rom_sha256=hashlib.sha256(rom.read_bytes()).hexdigest(),
                  stage2_bytes_added=b['stage2_bytes'] - a['stage2_bytes'], packed_bytes_added=b['packed_bytes'] - a['packed_bytes'],
                  free_bytes_before=a['free_bytes'], free_bytes=b['free_bytes'])
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"{TAG} routine: {b['source']}")
    print(f"stub:             {b['stub_bytes']} bytes, unchanged")
    print(f"unpacked stage 2: {a['stage2_bytes']} bytes before, {b['stage2_bytes']} after (+{report['stage2_bytes_added']})")
    print(f"packed stage 2:   {a['packed_bytes']} bytes before, {b['packed_bytes']} after (+{report['packed_bytes_added']})")
    print(f"free ROM bytes:   {a['free_bytes']} before, {b['free_bytes']} after")
    print(f"ROM sha256:       {report['rom_sha256']}")
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path, nargs='?')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--routine', type=Path, metavar='OUT')
    args = parser.parse_args()
    try:
        if args.routine:
            build(args.routine)
        else:
            if not args.source or not args.output:
                parser.error('SOURCE and --output required')
            report = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {report["seed"]})')
            for path, row in report['changed'].items():
                print(f'{TAG}: patched {path} (sha256 {row["before"][:16]} -> {row["after"][:16]})')
            print(f'{TAG}: 1 register, 13 address multiplexer bits, 8 read data bits (3 sources); no RAM, no SDC change')
    except ValueError as error:
        raise SystemExit(str(error))
