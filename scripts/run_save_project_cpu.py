#!/usr/bin/env python3
"""Run a save-handler CPU bench with a fitted project's selected CPU path.

The fixture checkout supplies its handler, bench and upstream GTE/device
models. The project supplies the exact CPU, memorymux, cache, divider and
memory-control sources. Vendor RAMs use the existing portable clocked models.
This is a CPU-path check, not a full physical-top or hardware save test.
"""
import argparse
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import re
import resource
import shutil
import sys
from prepare_save_states import exp1_byte_bus_text


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def replayed_guest(reference, restored):
    """CPU restore stores can increment log_stores; only guest SPU writes prove replay."""
    return (restored.get('loads') == '1' and restored.get('loads_failed') == '0'
            and int(restored.get('spu_writes', '0')) > int(reference['spu_writes']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--fixture-root', type=Path, required=True)
    parser.add_argument('--scenario', action='append', help="scenario(s) to run after reference, save and save warm (default: 'save+load'), e.g. 'save+load fresh drive'")
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--staged-full-context', action='store_true', help='use own full-SPU PXS4 fixture and COP0 replay witness')
    parser.add_argument('--staged-scalar-ring', action='store_true', help='require paired PXS6 project ROM/SPU and cyclic-port model')
    parser.add_argument('--lean-spu-base', type=Path, help='exact PXS4 SPU input used to regenerate PXS7')
    parser.add_argument('--staged-exact-voices', action='store_true',
                        help='require the paired PXS9 project ROM (prepare_spu_exact_voices.py) and use the 8-word ring port model')
    parser.add_argument('--staged-lean-restore', action='store_true', help='require paired PXS7 project ROM and shared sample-write SPU')
    parser.add_argument('--staged-playback-restore', action='store_true', help='require paired PXS8 ROM and speaker-history rebuild SPU')
    parser.add_argument('--staged-context-fault', choices=('debug-restore', 'spu-restore', 'sample-restore'), help='deliberate staged qualification fault; never a fitted input')
    parser.add_argument('--reject-old-staged-format', action='store_true', help='require safe rejection of the preceding incomplete state format')
    parser.add_argument('--iterations', type=int, default=32)
    parser.add_argument('--routine', choices=('pxs6', 'r1'), default='pxs6',
                        help='r1: run prepare_save_r1.firmware_text of the paired PXS6 text on the R1 bench '
                             '(save_r1_bench.py); the project ROM stays the checked PXS6 pair, it is not the ROM simulated')
    parser.add_argument('--r1-remount', action='append', choices=('other disc', 'wake', 'wrong disc', 'timeout'),
                        help='R1 remount case(s) to run after the reference instead of the save/load scenarios')
    parser.add_argument('--r1-bench-disc-polls', action='store_true',
                        help='BENCH-ONLY: DISC_POLLS equate 0x00010000 instead of 0x00800000 (the timeout case only)')
    parser.add_argument('--r1-mutation', help='planted R1 routine fault (save_r1_bench.ROUTINE_MUTATIONS); never a fitted input')
    parser.add_argument('--r1-generic', action='append', default=[], metavar='NAME=VALUE',
                        help='extra bench generic for the R1 runs with a load (e.g. DISC_EARLY=true, a planted model fault)')
    parser.add_argument('--warm-only', action='store_true', help='check one save with stage 2 already installed')
    parser.add_argument('--warm-round-trip', action='store_true', help='check save/load after measured handler return with stage 2 installed')
    parser.add_argument('--entry-probe', action='store_true', help='check an error-only NOOP entry/return probe')
    parser.add_argument('--entry-finish-early', action='store_true',
                        help='report the entry-probe error before staging/EPC inspection')
    parser.add_argument('--exp1-byte-bus', action='store_true',
                        help='apply candidate byte-bus normalization to the selected memorymux (not a fitted input)')
    parser.add_argument('--exp1-startup-matrix', action='store_true',
                        help='entry probe with guest 8/16-bit and address-increment configurations')
    parser.add_argument('--check-fetch-fault', action='store_true', help='require the old EXP1 fetch-error decode to fail')
    parser.add_argument('--entry-contention-matrix', action='store_true',
                        help='check entry with bounded DMA stall input and simultaneous IRQ input')
    parser.add_argument('--entry-kernel-gprs', action='store_true',
                        help='observe k0/k1 preservation with normal IRQ masks clear')
    parser.add_argument('--preserve-kernel-gprs', action='store_true',
                        help='apply experimental CPU/private-handler preservation (not a fitted input)')
    parser.add_argument('--entry-kernel-guard', action='store_true',
                        help='check ordinary memory/JR or COP0 behavior outside hidden context; no save requested')
    parser.add_argument('--kernel-gpr-fault', choices=['memory-context', 'context-exit', 'return-delay', 'restore-k1'],
                        help='deliberate qualification fault, never a fitted input')
    parser.add_argument('--kernel-guard-kind', choices=['nop', 'unusable'], default='nop',
                        help='nop: ordinary zero-base SW/LW/JR; unusable: COP0 with CU0 disabled')
    # stage2-mark (scripts/prepare_save_stage2_mark.py, run_save_stage2_mark.py); without these
    # options nothing below changes the routine, the bench or the scenarios
    parser.add_argument('--routine-step', action='append', choices=('stage2-mark', 'load-check', 'spu-quiet', 'mdec-tap', 'early-load', 'disc-id', 'size-mib', 'rom-ext', 'waits', 'compress', 'stall-probe', 'readout-hold', 'drive-state-msf', 'switch-check', 'rom-room', 'readout-end', 'checksum', 'drive-state-seek', 'ready-remount', 'audit-quiet', 'diag-refusal', 'mount-wait', 'mount-wait-wake', 'mount-id'),
                        help='apply routine step(s), in the order given, to the paired routine text (after --routine r1); not a fitted input')
    parser.add_argument('--early-load-bench', action='store_true',
                        help='run the --scenario names of save_early_load_bench.py (with --stage2-bench and the routine steps)')
    parser.add_argument('--routine-fault', help='deliberate fault in a routine step (FAULTS of prepare_save_stage2_mark / prepare_save_load_check); never a fitted input')
    parser.add_argument('--stage2-bench', action='store_true',
                        help="run the --scenario names of run_save_stage2_mark.py (e.g. 'save damaged stage 2') instead of the fixture's list")
    parser.add_argument('--readout', choices=('late', 'none', 'endless', 'paused', 'cue'), default='late',
                        help='with --routine-step readout-hold: what the bench\'s Pocket reads after a save (save_readout_hold_bench.py)')
    parser.add_argument('--rom-ext-pad', type=int, metavar='BYTES',
                        help='with --routine-step rom-ext: bytes that do not pack at the start of stage 2, so that its packed stream reaches into the extension ROM; never a fitted input')
    parser.add_argument('--waits-left-out', action='append', default=[], metavar='PART',
                        help='with --routine-step waits: a part of the step the routine does not have (prepare_save_final_routine.LEFT_OUT)')
    parser.add_argument('--bench-wait-bound', action='store_true',
                        help='BENCH-ONLY: the waits step polls 4,096 times instead of WAIT_LOOPS (dead-device scenarios)')
    parser.add_argument('--max-cycles', type=int, help='stage2-bench: upper bound of a run (a run expected to hang)')
    args = parser.parse_args()
    if (args.routine_step or args.stage2_bench) and not (args.staged_scalar_ring or args.staged_exact_voices):
        parser.error('routine step / stage2 bench require --staged-full-context --staged-scalar-ring')
    if args.routine_fault and not args.routine_step:
        parser.error('routine fault requires --routine-step')
    if args.rom_ext_pad and 'rom-ext' not in (args.routine_step or ()):
        parser.error('--rom-ext-pad requires --routine-step rom-ext')
    if args.max_cycles and not args.stage2_bench:
        parser.error('--max-cycles requires --stage2-bench')
    if args.stage2_bench and not args.scenario:
        parser.error('--stage2-bench requires --scenario names')
    formats = (args.staged_scalar_ring, args.staged_lean_restore, args.staged_playback_restore, args.staged_exact_voices)
    if any(formats) and not args.staged_full_context:
        parser.error('paired save formats require --staged-full-context')
    if sum(formats) > 1:
        parser.error('choose one staged save format')
    r1_options = args.r1_remount or args.r1_bench_disc_polls or args.r1_mutation or args.r1_generic
    if args.routine == 'r1' and not (args.staged_scalar_ring or args.staged_exact_voices):
        parser.error('--routine r1 is written on the PXS6 text: use --staged-full-context --staged-scalar-ring')
    if args.routine != 'r1' and r1_options:
        parser.error('R1 options require --routine r1')
    if args.routine == 'r1' and (args.staged_context_fault or args.warm_only or args.reject_old_staged_format):
        parser.error('--routine r1 runs the scenarios, --warm-round-trip or --r1-remount')
    if args.r1_bench_disc_polls != (args.r1_remount == ['timeout']):
        parser.error('the DISC_POLLS bench override is for --r1-remount timeout alone, and that case needs it')
    if args.staged_context_fault and not args.staged_full_context:
        parser.error('staged context fault requires --staged-full-context')
    if args.reject_old_staged_format and (not args.staged_full_context or not args.warm_round_trip):
        parser.error('old-format rejection requires --staged-full-context --warm-round-trip')
    if args.staged_full_context and (args.entry_probe or args.preserve_kernel_gprs):
        parser.error('staged full context requires its already paired full firmware/CPU')
    if args.entry_finish_early and not args.entry_probe:
        parser.error('early FINISH requires --entry-probe')
    if args.exp1_startup_matrix and not args.entry_probe:
        parser.error('EXP1 startup matrix requires --entry-probe')
    if args.entry_contention_matrix and (not args.entry_probe or args.exp1_startup_matrix):
        parser.error('entry contention requires --entry-probe and a separate startup-matrix run')
    if args.entry_kernel_gprs and (not args.entry_probe or args.entry_contention_matrix or args.exp1_startup_matrix):
        parser.error('kernel-GPR check requires --entry-probe and a separate matrix run')
    if args.preserve_kernel_gprs and not args.entry_probe:
        parser.error('kernel-GPR preservation requires --entry-probe')
    if args.entry_kernel_guard and (not args.entry_probe or args.entry_kernel_gprs or
                                   args.entry_contention_matrix or args.exp1_startup_matrix):
        parser.error('kernel context guard requires --entry-probe and its own run')
    if args.kernel_gpr_fault and (not args.preserve_kernel_gprs or
            (args.kernel_gpr_fault != 'memory-context' and not args.entry_kernel_gprs) or
            (args.kernel_gpr_fault == 'memory-context' and not args.entry_kernel_guard)):
        parser.error('kernel fault requires preservation and the matching witness/guard')
    if args.kernel_guard_kind != 'nop' and not args.entry_kernel_guard:
        parser.error('kernel guard kind requires --entry-kernel-guard')
    project, fixture, out = (p.resolve() for p in (args.project, args.fixture_root, args.output))
    if out.exists() or args.iterations < 16:
        parser.error('use a fresh output directory and at least 16 guest iterations')
    qsf = project / 'apf_top.qsf'
    frozen = json.loads((project / 'fit-inputs.json').read_text())
    expected = {str(Path(row['path']).resolve()): row['sha256'] for row in frozen['quartus_sources']}
    paths = {Path(p) if Path(p).is_absolute() else project / p
             for p in re.findall(r'-name VHDL_FILE "([^"]+)"', qsf.read_text())}

    def selected(*names):
        found = {p.resolve() for p in paths if p.name in names}
        if len(found) != 1:
            raise ValueError(f'expected one selected {names}, found {found}')
        path = found.pop()
        if expected.get(str(path)) != sha(path):
            raise ValueError(f'fit-inputs.json does not bind the current {path}')
        return path

    sources = {name: selected(name) for name in ('cpu.vhd', 'memorymux.vhd', 'datacache.vhd', 'divider.vhd')}
    sources['memctrl.vhd'] = selected('memctrl.vhd', 'memctrl_lean.vhd')
    if args.staged_full_context:
        from prepare_save_kernel_gprs import QUALIFIED_CPU
        from prepare_cpu_store_shift import CPU_CODE_SHA, code_only
        # The qualified private-context CPU, or the same code with only comment changes,
        # alone or after prepare_cpu_store_shift (docs/ss-pxs6-recipe.sh).
        STORE_SHIFT_CODE_SHA = 'bf322c960b2e4dd351e9750d686fe61d1da776726f2a4a345be3d68152c7688a'
        code = hashlib.sha256(code_only(sources['cpu.vhd'].read_text()).encode()).hexdigest()
        # The same two with the clean-room MULT/MULTU product (cpu_shared_product.py, PR #95): 15 changed
        # code lines, all in the multiplier (checked when these hashes were added, 2026-11-11).
        from prepare_cpu_store_shift import CPU_CODE_SHA_SHARED_PRODUCT
        STORE_SHIFT_PRODUCT_CODE_SHA = '24e6b2300d19b9599db9456faa9039e41641c5fcec3c6e0f19923efea314e561'
        # Candidate E: the store-shift + shared-product CPU after prepare_cpu_tag_split.py (branch
        # timing-paths branch: the I-cache tag RAM read at both next-fetch candidates). Checked
        # 2026-11-11: build/ss/e's cpu.vhd is byte-for-byte that script's transform() of build/ss/d2's.
        # The step's own equivalence evidence is that step's; this bench runs the result as it is.
        TAG_SPLIT_CODE_SHA = 'ae99c6bf5018deb0a529a29c206221e04a1732f7a99797deb05dd4dd9c3031e3'
        if sha(sources['cpu.vhd']) != QUALIFIED_CPU and code not in (CPU_CODE_SHA, STORE_SHIFT_CODE_SHA,
                                                                CPU_CODE_SHA_SHARED_PRODUCT, STORE_SHIFT_PRODUCT_CODE_SHA,
                                                                TAG_SPLIT_CODE_SHA):
            raise ValueError('staged full context requires the qualified private-context CPU')
    fifo = 'entity mem.psx_wfifo_m10k' in sources['memorymux.vhd'].read_text()
    if fifo:
        sources['psx_wfifo_m10k.vhd'] = selected('psx_wfifo_m10k.vhd')
    out.mkdir(parents=True)
    owned = out / 'inputs'
    owned.mkdir()
    ledger = {str(path.resolve()): sha(path) for path in
              (Path(__file__), Path(inspect.getfile(exp1_byte_bus_text)))}
    for name, path in sources.items():
        ledger[str(path)] = sha(path)
        (owned / name).write_bytes(path.read_bytes())
    for path in (qsf, project / 'fit-inputs.json'):
        ledger[str(path)] = sha(path)

    # Import the matching fixture's runner rather than mixing its stage-2
    # firmware with another revision's bench or firmware linker.
    sys.path.insert(0, str(fixture / 'scripts'))
    m = importlib.import_module('run_staged_save_cpu' if args.staged_full_context else 'run_save_trap_cpu')
    from run_current_cpu_regfile import ADDRESS_MODEL
    m.BUILD = out / 'runs'
    m.ITERATIONS = args.iterations
    if any(formats):
        from prepare_spu_ring_paired import cpu_bench, firmware_text, require_paired_spu
        from build_staged_save_fw import image_parts, rom40_hex, rom1, rom2, SRC

        native = selected('spu.vhd')
        ring_fw = firmware_text(SRC.read_text())
        if args.staged_lean_restore or args.staged_playback_restore:
            if args.staged_playback_restore:
                import prepare_spu_playback_restore as restore_profile
                label = 'playback'
            else:
                import prepare_spu_lean_restore as restore_profile
                label = 'lean'
            from run_spu_pending_feed import transform as pending_spu
            from run_spu_full_context import SPU_SHA as PXS4_SPU_SHA

            # Rebuild the exact SPU from the retained PXS4 input. The frozen
            # manifest already binds the actual selected output and its ROM.
            base = args.lean_spu_base.resolve() if args.lean_spu_base else None
            if base is None or sha(base) != PXS4_SPU_SHA:
                raise ValueError('Lean profiles require the exact PXS4 SPU via --lean-spu-base')
            if native.read_text() != restore_profile.transform(pending_spu(base.read_text(), 0)):
                raise ValueError('Lean restore SPU differs')
            ledger[str(base)] = sha(base)
            for module in (restore_profile, pending_spu, require_paired_spu):
                path = Path(inspect.getfile(module))
                ledger[str(path)] = sha(path)
            fw = project / f'staged-{label}.S'
            expected_fw = restore_profile.firmware_text(ring_fw)
            cpu_bench = restore_profile.cpu_bench
        elif args.staged_exact_voices:
            # PXS9: the PXS6 text with the exact-voices port table and magic; the project ROM must be
            # exactly that routine. The port is the authored 8-word ring model (save_exact_voices_bench.py).
            import prepare_spu_exact_voices, save_exact_voices_bench
            if sha(native) not in save_exact_voices_bench.SPU_SHAS:
                raise ValueError('SPU is not a known exact-voices (PXS9) SPU')
            fw = out / 'staged-exact-voices.S'
            expected_fw = prepare_spu_exact_voices.firmware_text(ring_fw)
            fw.write_text(expected_fw)
            ring_bench = cpu_bench
            cpu_bench = lambda text: save_exact_voices_bench.cpu_bench(ring_bench(text))
            for module in (prepare_spu_exact_voices, save_exact_voices_bench):
                ledger[str(Path(inspect.getfile(module)))] = sha(inspect.getfile(module))
        else:
            fw = project / 'staged-ring.S'
            expected_fw = ring_fw
            if (project / 'save-ring-paired.json').exists():
                report = json.loads((project / 'save-ring-paired.json').read_text())
                require_paired_spu(native, report['spu_sha256'])
            else:
                # docs/ss-pxs6-recipe.sh: the same PXS6 port ABI after the pending feed,
                # the Gaussian DSP and base trims A (each step qualified on the native SPU).
                pxs6_chain = {'77170eda3749b24b9ec5a28afd9183c854bb5905bc1e2a2a0f748a4708d6803b',   # + pending feed
                              '415e40514c9d3aa153cb199e151e341c36e158ff780bc30204826773b0808e3a',   # + Gaussian DSP
                              '049066bccaf54d7dbfc0ef792014c046b32c38eb25f3c9c0d1f8287201dd2aba',   # + base trims A
                              'f23d4ccea1e08203e64882d251b74a2b752590c530a9dd326078d571901ad986'}   # + env_step (bound-Gaussian lockstep, 2026-11-11)
                if sha(native) not in pxs6_chain:
                    raise ValueError('SPU is not the PXS6 recipe chain')
                fw = out / 'staged-ring.S'
                fw.write_text(ring_fw)
        if fw.read_text() != expected_fw:
            raise ValueError('Paired save firmware source differs')
        for path in (Path(inspect.getfile(require_paired_spu)), fw, SRC):
            ledger[str(path)] = sha(path)
        rom = project / 'save-states/psx_save_rom40.hex'
        image, _ = image_parts(source=fw)
        if expected.get(str(rom)) != sha(rom) or rom.read_text() != rom40_hex(rom1(image), rom2(image)):
            raise ValueError('Paired save ROM differs')
        ledger[str(native)] = sha(native)
        ledger[str(rom)] = sha(rom)
        m.FW = fw
        original_bench = m.bench_text
        m.bench_text = lambda: cpu_bench(original_bench())
    r1 = None
    r1_sources = []
    rom_ext_info = {}
    if args.routine == 'r1':
        import save_r1_bench as r1
        from run_memorymux_fifo_ram import ports as vhdl_ports, zero as vhdl_zero
        pxs6_text = m.FW.read_text()       # the paired PXS6 text checked against the project ROM above
        for name in ('pJoypad.vhd', 'joypad_pad_table.vhd', 'joypad_mem.vhd', 'joypad_card1.vhd'):
            path = selected(name)
            ledger[str(path)] = sha(path)
            (owned / name).write_bytes(path.read_bytes())
            r1_sources.append(owned / name)
        r1_fw = out / 'staged-r1.S'
        r1_fw.write_text(r1.routine_text(pxs6_text, disc_polls=r1.DISC_POLLS_BENCH if args.r1_bench_disc_polls else None,
                                         mutation=args.r1_mutation))
        ledger[str(Path(inspect.getfile(r1)))] = sha(inspect.getfile(r1))
        m.FW = r1_fw
        paired_bench = m.bench_text
        m.bench_text = lambda: r1.bench_text(paired_bench(), owned / 'joypad_card1.vhd', vhdl_ports, vhdl_zero,
                                             r1.magic_r1(pxs6_text))
        paired_game = m.game_program

        def r1_game_program(seed, directory):
            paired_game(seed, directory)
            r1.guest(directory)

        m.game_program = r1_game_program
    r1_extra = dict(item.split('=', 1) for item in args.r1_generic)
    r1_rows = []
    m.cpu_trap_text = lambda _: (owned / 'cpu.vhd').read_text()
    if args.preserve_kernel_gprs:
        from prepare_save_kernel_gprs import cpu_text, firmware_text
        ledger[str(Path(inspect.getfile(cpu_text)))] = sha(inspect.getfile(cpu_text))
        candidate = owned / 'cpu-kernel-gprs.vhd'
        candidate_text = cpu_text((owned / 'cpu.vhd').read_text())
        if args.kernel_gpr_fault == 'memory-context':
            anchor = "if exceptionTrap = '1' and decodeSource1 = 0 then"
            if candidate_text.count(anchor) != 1: raise ValueError('memory alias fault anchor changed')
            candidate_text = candidate_text.replace(anchor, 'if decodeSource1 = 0 then')
        if args.kernel_gpr_fault == 'context-exit':
            anchor = '''               elsif opcode1 = x"42000010" then -- RFE ends hidden context
                  exceptionTrap <= '0';
'''
            if candidate_text.count(anchor) != 1: raise ValueError('context exit fault anchor changed')
            candidate_text = candidate_text.replace(anchor, '')
        candidate.write_text(candidate_text)
        ledger[str(candidate)] = sha(candidate)
        m.cpu_trap_text = lambda _: candidate.read_text()
    m.memorymux_exp1_text = lambda _: (owned / 'memorymux.vhd').read_text()
    m.SAVE_MODE_PORT = '-- save-mode-clk1x' in (owned / 'memorymux.vhd').read_text()
    if args.exp1_byte_bus:
        candidate = exp1_byte_bus_text((owned / 'memorymux.vhd').read_text())
        (owned / 'memorymux-byte-bus.vhd').write_text(candidate)
        ledger[str(owned / 'memorymux-byte-bus.vhd')] = sha(owned / 'memorymux-byte-bus.vhd')
        m.memorymux_exp1_text = lambda _: candidate
    if args.entry_probe:
        if args.warm_only or args.warm_round_trip:
            parser.error('entry probe and stage-2 checks are separate modes')
        from build_save_abort_probe import probe_source
        path = owned / 'save_abort.S'
        path.write_text(probe_source(m.FW.read_text(), early_finish=args.entry_finish_early))
        if args.preserve_kernel_gprs:
            path.write_text(firmware_text(path.read_text()))
            if args.kernel_gpr_fault == 'return-delay':
                anchor = '        jr      $k0\n'
                text = path.read_text()
                if text.count(anchor) != 1: raise ValueError('load delay fault anchor changed')
                path.write_text(text.replace(anchor, '        nop                     # deliberately lose old EPC\n' + anchor))
            if args.kernel_gpr_fault == 'restore-k1':
                anchor = '        lw      $k1, PAGE+4*27($k0)\n'
                text = path.read_text()
                if text.count(anchor) != 1: raise ValueError('kernel reload fault anchor changed')
                path.write_text(text.replace(anchor, '        nop                     # deliberate missing k1 restore\n'))
        m.FW = path
    if args.staged_context_fault:
        text = m.FW.read_text()
        mutations = {
            'debug-restore': ('        mtc0    $t0, $\\n\n', '        nop                     # deliberately omit debug context restore\n'),
            'spu-restore': ('        li      $a0, 2\n\n# a0 = 1: savestate port words', '        li      $a0, 1           # deliberately read instead of restoring\n\n# a0 = 1: savestate port words'),
            'sample-restore': ('        jal     spu_samples\n        li      $a0, 2\n', '        jal     spu_samples\n        li      $a0, 1           # deliberately omit decoded-sample restoration\n'),
        }
        before, after = mutations[args.staged_context_fault]
        if text.count(before) != 1:
            raise ValueError('Staged-context fault anchor changed: ' + args.staged_context_fault)
        mutant = owned / 'staged-fault.S'
        mutant.write_text(text.replace(before, after))
        ledger[str(m.FW)] = sha(m.FW)
        m.FW = mutant
    if args.routine_step:
        import prepare_save_stage2_mark, prepare_save_load_check, prepare_save_spu_quiet, prepare_save_waits
        modules = {'stage2-mark': prepare_save_stage2_mark, 'load-check': prepare_save_load_check,
                   'spu-quiet': prepare_save_spu_quiet, 'waits': prepare_save_waits}
        if 'mdec-tap' in args.routine_step:
            import save_mdec_tap_bench
            modules['mdec-tap'] = save_mdec_tap_bench
            ledger[str(Path(inspect.getfile(save_mdec_tap_bench.step)))] = sha(inspect.getfile(save_mdec_tap_bench.step))
        if 'early-load' in args.routine_step:
            import prepare_save_early_load
            modules['early-load'] = prepare_save_early_load
        if 'size-mib' in args.routine_step:
            import prepare_save_size_mib
            modules['size-mib'] = prepare_save_size_mib
        if 'disc-id' in args.routine_step:
            import prepare_save_disc_id
            modules['disc-id'] = prepare_save_disc_id
        if 'stall-probe' in args.routine_step:
            import save_stall_probe_bench
            modules['stall-probe'] = save_stall_probe_bench
            ledger[str(Path(inspect.getfile(save_stall_probe_bench.step)))] = sha(inspect.getfile(save_stall_probe_bench.step))
        if 'rom-ext' in args.routine_step:
            import prepare_save_rom_ext
            modules['rom-ext'] = prepare_save_rom_ext.RoutineStep
        if 'compress' in args.routine_step:
            import save_compress_bench
            modules['compress'] = save_compress_bench
            ledger[str(Path(inspect.getfile(save_compress_bench.step)))] = sha(inspect.getfile(save_compress_bench.step))
        if 'switch-check' in args.routine_step:
            import prepare_save_switch_check_routine
            modules['switch-check'] = prepare_save_switch_check_routine
        if 'drive-state-seek' in args.routine_step:
            import save_drive_state_seek_step
            modules['drive-state-seek'] = save_drive_state_seek_step
        if 'audit-quiet' in args.routine_step:
            import prepare_save_audit_quiet
            modules['audit-quiet'] = prepare_save_audit_quiet
        if 'ready-remount' in args.routine_step:
            import prepare_save_ready_remount_routine
            modules['ready-remount'] = prepare_save_ready_remount_routine
        if 'mount-id' in args.routine_step:
            import prepare_save_mount_id_routine
            modules['mount-id'] = prepare_save_mount_id_routine
        if 'mount-wait-wake' in args.routine_step:
            import prepare_save_mount_wait_wake_routine
            modules['mount-wait-wake'] = prepare_save_mount_wait_wake_routine
        if 'mount-wait' in args.routine_step:
            import prepare_save_mount_wait_routine
            modules['mount-wait'] = prepare_save_mount_wait_routine
        if 'diag-refusal' in args.routine_step:
            import prepare_save_diag_refusal_routine
            modules['diag-refusal'] = prepare_save_diag_refusal_routine
        if 'drive-state-msf' in args.routine_step:
            import save_drive_state_msf_step
            modules['drive-state-msf'] = save_drive_state_msf_step
        if 'checksum' in args.routine_step:
            import save_checksum_bench
            modules['checksum'] = save_checksum_bench
            ledger[str(Path(inspect.getfile(save_checksum_bench.step)))] = sha(inspect.getfile(save_checksum_bench.step))
        if 'readout-hold' in args.routine_step:
            import save_readout_hold_bench
            save_readout_hold_bench.mode['reads'] = args.readout
            modules['readout-hold'] = save_readout_hold_bench
            ledger[str(Path(inspect.getfile(save_readout_hold_bench.step)))] = sha(inspect.getfile(save_readout_hold_bench.step))
        if 'readout-end' in args.routine_step:
            import save_readout_end_bench
            save_readout_end_bench.mode['reads'] = args.readout
            modules['readout-end'] = save_readout_end_bench
            ledger[str(Path(inspect.getfile(save_readout_end_bench.step)))] = sha(inspect.getfile(save_readout_end_bench.step))
        if 'rom-room' in args.routine_step:
            import prepare_save_rom_room
            modules['rom-room'] = prepare_save_rom_room
            if 'rom-ext' not in args.routine_step:
                raise ValueError('rom-room: its lz2 stream is built by the rom-ext builder (add --routine-step rom-ext)')
        if 'audit-quiet' in args.routine_step:
            import prepare_save_audit_quiet
            modules['audit-quiet'] = prepare_save_audit_quiet
        text = m.FW.read_text()
        waits_parts = tuple(p for p in prepare_save_waits.PARTS if p not in args.waits_left_out)
        if set(args.waits_left_out) - set(prepare_save_waits.PARTS):
            raise ValueError('unknown waits part: ' + ', '.join(args.waits_left_out))
        for name in args.routine_step:
            text = modules[name].firmware_text(text, waits_parts) if name == 'waits' else modules[name].firmware_text(text)
            ledger[str(Path(inspect.getfile(modules[name])))] = sha(inspect.getfile(modules[name]))
        if args.routine_fault:
            owners = [modules[name] for name in args.routine_step if args.routine_fault in modules[name].FAULTS]
            if len(owners) != 1:
                raise ValueError('routine fault is not one of the applied steps\' faults: ' + args.routine_fault)
            text = owners[0].fault_text(text, args.routine_fault)
        if args.rom_ext_pad:
            text = prepare_save_rom_ext.pad_text(text, args.rom_ext_pad)
        if args.bench_wait_bound:
            text = prepare_save_waits.bench_bound(text)
        stepped = owned / ('staged-step-fault.S' if args.routine_fault else 'staged-step.S')
        stepped.write_text(text)
        ledger[str(m.FW)] = sha(m.FW)
        m.FW = stepped
    if args.routine_step or args.stage2_bench:
        import run_save_stage2_mark as stage2_bench
        ledger[str(Path(inspect.getfile(stage2_bench)))] = sha(inspect.getfile(stage2_bench))
        paired_bench_text = m.bench_text
        m.bench_text = lambda: stage2_bench.bench_text(paired_bench_text())
        if 'mdec-tap' in (args.routine_step or ()):
            staged_bench_text = m.bench_text
            m.bench_text = lambda: save_mdec_tap_bench.bench_text(staged_bench_text())
            m.simulate = save_mdec_tap_bench.wrap_simulate(m.simulate)
        if args.early_load_bench:
            import save_early_load_bench
            ledger[str(Path(inspect.getfile(save_early_load_bench)))] = sha(inspect.getfile(save_early_load_bench))
            el_bench_text = m.bench_text
            m.bench_text = lambda: save_early_load_bench.bench_text(el_bench_text())
        if 'size-mib' in (args.routine_step or ()):     # the bench's size assertion: whole MiB
            exact_bench_text = m.bench_text
            m.bench_text = lambda: prepare_save_size_mib.bench_text(exact_bench_text())
        if 'stall-probe' in (args.routine_step or ()):  # EXPERIMENT: several saves in a run, busy times
            unprobed_bench_text = m.bench_text
            m.bench_text = lambda: save_stall_probe_bench.bench_text(unprobed_bench_text())
            m.simulate = save_stall_probe_bench.wrap_simulate(m.simulate)
        if 'rom-ext' in (args.routine_step or ()):
            # the extension ROM in the bench's device model, and the stream built into it by this
            # checkout's builder (the fixture's has no extension)
            ext_builder = prepare_save_rom_ext.builder()
            ledger[str(Path(inspect.getfile(ext_builder)))] = sha(inspect.getfile(ext_builder))
            ext_bench_text = m.bench_text
            m.bench_text = lambda: prepare_save_rom_ext.cpu_bench_text(ext_bench_text())

            def assemble(source, directory, ram_pages):
                directory.mkdir(parents=True, exist_ok=True)
                rom, stage2, ext = ext_builder.image_parts_ext(source, ram_pages, m.VRAM_ROWS, m.SPU_PAGES)
                (directory / 'rom_exp1.hex').write_text(ext_builder.word_hex(rom))
                (directory / 'rom_ext.hex').write_text(ext_builder.word_hex(ext))
                (directory / 'stage2.hex').write_text(ext_builder.word_hex(stage2 + bytes(-len(stage2) % 4)))
                rom_ext_info.update(stage2_bytes=len(stage2), extension_bytes_used=len(ext.rstrip(b'\0')),
                                    free_bytes=ext_builder.free_bytes(rom, ext), pad_bytes=args.rom_ext_pad or 0)
                print('rom-ext:', rom_ext_info, flush=True)
                if args.rom_ext_pad and not rom_ext_info['extension_bytes_used']:
                    raise ValueError('rom-ext: the pad does not reach the extension ROM')

            m.assemble = assemble
        if 'compress' in (args.routine_step or ()):
            stepped_bench_text = m.bench_text
            m.bench_text = lambda: save_compress_bench.bench_text(stepped_bench_text())
            m.simulate = save_compress_bench.wrap_simulate(m.simulate)
        if 'checksum' in (args.routine_step or ()):   # CSUM in the device model, STATE_FAULT 4 and 5
            summed_bench_text = m.bench_text
            m.bench_text = lambda: save_checksum_bench.bench_text(summed_bench_text())
        if 'readout-hold' in (args.routine_step or ()):   # last: its bench anchors are the other steps' final text
            held_bench = save_readout_end_bench if 'readout-end' in args.routine_step else save_readout_hold_bench
            held_bench_text = m.bench_text
            m.bench_text = lambda: held_bench.bench_text(held_bench_text())
            m.simulate = held_bench.wrap_simulate(m.simulate)
    original_run, original_simulate = m.run, m.simulate
    contention_fault = False
    if args.entry_contention_matrix:
        from prepare_save_entry_stress import stress_bench, irq_enabled_boot
        ledger[str(Path(inspect.getfile(stress_bench)))] = sha(inspect.getfile(stress_bench))
        original_game = m.game_program

        def game_program(seed, directory):
            original_game(seed, directory)
            rom = directory / 'rom.hex'
            rom.write_text(irq_enabled_boot(rom.read_text()))

        m.game_program = game_program
    if args.entry_kernel_gprs:
        from prepare_save_entry_stress import kernel_guest, kernel_bench
        ledger[str(Path(inspect.getfile(kernel_guest)))] = sha(inspect.getfile(kernel_guest))
        original_game = m.game_program

        def game_program(seed, directory):
            original_game(seed, directory)
            kernel_guest(directory)

        m.game_program = game_program
    if args.entry_kernel_guard:
        from prepare_save_entry_stress import kernel_guard_guest, kernel_guard_bench
        ledger[str(Path(inspect.getfile(kernel_guard_guest)))] = sha(inspect.getfile(kernel_guard_guest))
        original_game = m.game_program

        def game_program(seed, directory):
            original_game(seed, directory)
            kernel_guard_guest(directory, unusable=args.kernel_guard_kind == 'unusable')

        m.game_program = game_program
    initialized = set()

    def run(command, directory, log, timeout=int(__import__("os").environ.get("SAVE_SIM_TIMEOUT", "3600"))):
        if '-r' in command and '--max-stack-alloc=0' not in command:
            command = [*command, '--max-stack-alloc=0']
        if '-a' in command:
            source = Path(command[-1])
            if args.entry_contention_matrix and source.name == 'tb_save_trap_cpu.vhd':
                source.write_text(stress_bench(source.read_text(), disconnect_dma=contention_fault))
            if args.entry_kernel_gprs and source.name == 'tb_save_trap_cpu.vhd':
                source.write_text(kernel_bench(source.read_text()))
            if args.entry_kernel_guard and source.name == 'tb_save_trap_cpu.vhd':
                source.write_text(kernel_guard_bench(source.read_text()))
            if directory not in initialized:
                initialized.add(directory)
                for name in ('rom_exp1.hex', 'stage2.hex', 'save_handler.S', 'ram.hex', 'rom.hex'):
                    artifact = directory / name
                    if artifact.exists():
                        ledger[str(artifact)] = sha(artifact)
                ram = (m.UPSTREAM / 'sim/system/src/mem/dpram.vhd').read_text()
                ram = ram[:ram.index('end rtl;') + len('end rtl;')]
                ram = re.sub(r'\bdpram\b', 'pocket_dpram_sdp', ram, flags=re.I)
                ram = ram[:ram.index('architecture rtl of')] + ADDRESS_MODEL
                reverse = ram.replace('pocket_dpram_sdp', 'pocket_dpram_sdp_reverse')
                reverse = re.sub(r'\b(clock|clken|address|data|wren|q)_([ab])\b',
                                 lambda match: match[1] + '_' + ('b' if match[2] == 'a' else 'a'), reverse)
                extra = []
                for name, text in (('pocket_dpram_sdp.vhd', ram), ('pocket_dpram_sdp_reverse.vhd', reverse)):
                    path = directory / name
                    path.write_text(text)
                    extra.append(('work', path))
                if fifo:
                    extra += [('mem', fixture / 'sim/memory/psx_m10k_sdp_model.vhd'),
                              ('mem', owned / 'psx_wfifo_m10k.vhd')]
                for library, path in extra:
                    result = original_run([command[0], '-a', *m.GHDL_FLAGS, '--work=' + library, str(path)], directory, log)
                    if result.returncode:
                        raise RuntimeError(result.stdout + result.stderr)
            if r1 and source.name == 'tb_save_trap_cpu.vhd':
                # the project's joypad (real RTL) for the R1 bench, after the memories it instantiates
                for path in r1_sources:
                    ledger[str(path)] = sha(path)
                    result = original_run([command[0], '-a', *m.GHDL_FLAGS, '--work=work', str(path)], directory, log)
                    if result.returncode:
                        raise RuntimeError(result.stdout + result.stderr)
            if source.name in ('divider.vhd', 'datacache.vhd', 'memctrl.vhd'):
                command = [*command[:-1], str(owned / source.name)]
            ledger[str(Path(command[-1]))] = sha(command[-1])
        return original_run(command, directory, log, timeout)

    def simulate(directory, generics, tag):
        # The entry-only probe cannot enter the cold stage-2 unpacker.
        if generics:
            limit = (generics.get('MAX_CYCLES', 100000) if args.entry_probe else
                     max(generics.get('MAX_CYCLES', 0), 3000000))
            if args.max_cycles:
                limit = min(limit, args.max_cycles)
            generics = dict(generics, MAX_CYCLES=limit)
        if r1 and ('LOAD_AT' in generics or 'LOAD_DELAY' in generics):
            # a load of a real state: the devices R1 restores are reset at the load request
            if 'SAVE_AT' in generics and not any(k in generics for k in ('DISC_WRONG', 'DISC_NEVER', 'LOAD_PLANT')):
                generics = dict(generics, R1_SCRUB='true')
            generics = dict(generics, **r1_extra)
        return original_simulate(directory, generics, tag)

    m.run, m.simulate = run, simulate
    assembler = m.__dict__.get('image_parts', m.__dict__.get('image'))
    for path in (m.FW, Path(inspect.getfile(m)), Path(inspect.getfile(assembler)),
                 fixture / 'rtl/pocket/save_fw/save_handler.S', fixture / 'sim/reference/tb_save_trap_cpu.vhd',
                 *(fixture / 'scripts').glob('*.py')):
        ledger[str(path)] = sha(path)
    # LLVM allocates the large portable memory model on the process stack.
    # Match our native-SPU runner without requiring a shell-specific ulimit.
    _, hard_stack = resource.getrlimit(resource.RLIMIT_STACK)
    resource.setrlimit(resource.RLIMIT_STACK, (hard_stack, hard_stack))
    directory = m.build('selected-project', seed=1)

    kernel_results = []

    def check(directory):
        if args.entry_kernel_guard:
            ok, stats, image, why = m.simulate(directory, dict(MAX_CYCLES=5000), 'outside-context')
            kernel_results.append(dict(outside_context=True, statistics=stats))
            if not ok: return ['outside-context guard failed: ' + why]
            expected = dict(kernel_k0='A5A55A5A' if args.kernel_guard_kind == 'unusable' else '13579BDF', kernel_k1='2468ACE0',
                            normal_exceptions='2' if args.kernel_guard_kind == 'unusable' else '1', saves='0')
            if args.kernel_guard_kind == 'nop':
                expected['kernel_alias'] = '13579BDF'
            if any(stats.get(k) != v for k, v in expected.items()):
                return ['ordinary memory/COP0/JR behavior changed: ' + str(stats)]
            print('outside-context guard:', stats, flush=True)
            return []
        if args.r1_remount:
            ok, ref, image, why = m.simulate(directory, {}, 'reference')
            if not ok:
                return ['reference run failed: ' + why]
            print('reference:', ref, flush=True)
            rows, errors = r1.remount_scenarios(m, directory, ref, image, args.r1_remount)
            r1_rows.extend(rows)
            return errors
        if args.early_load_bench:
            return save_early_load_bench.scenarios(m, directory, args.scenario, disc_id='disc-id' in (args.routine_step or ()))
        if args.stage2_bench:
            errors = stage2_bench.scenarios(m, directory, args.scenario, marker='stage2-mark' in (args.routine_step or ()),
                                            check='load-check' in (args.routine_step or ()),
                                            quiet='spu-quiet' in (args.routine_step or ()),
                                            waits='waits' in (args.routine_step or ()))
            if 'rom-ext' in (args.routine_step or ()):
                rows, more = prepare_save_rom_ext.cpu_log_check((directory / 'checks.log').read_text(),
                                                                rom_ext_info.get('extension_bytes_used', 0),
                                                                cold=any(s in ('save', 'save+load', 'load first cold') for s in args.scenario))
                rom_ext_info['runs'] = rows
                print('rom-ext runs:', rows, flush=True)
                errors = errors + more
            return errors
        if not (args.warm_only or args.warm_round_trip or args.entry_probe):
            options = {'only': tuple(args.scenario or ('save+load',))} if 'only' in inspect.signature(m.scenarios).parameters else {}
            return m.scenarios(directory, stop_on_failure=True, **options)
        ok, ref, image, why = m.simulate(directory, {}, 'reference')
        if not ok:
            return ['reference run failed: ' + why]
        print('reference:', ref, flush=True)
        generics = dict(SAVE_AT=int(ref['cycles']) // 3)
        if args.entry_probe:
            generics['MAX_CYCLES'] = int(ref['cycles']) * 4
        if not args.entry_probe:
            generics['STAGE2_WARM'] = 'true'
        if args.warm_round_trip:
            # FINISH precedes register/cache restoration. LOAD_DELAY measured
            # from FINISH can request Load before a single guest instruction
            # has run. First measure the entire Save/return overhead instead.
            ok, save_only, save_image, why = m.simulate(directory, generics, 'save-only-warm')
            print('warm save-only:', save_only, flush=True)
            if (not ok or save_image != image or save_only.get('saves') != '1'
                    or save_only.get('saves_failed') != '0'):
                return ['warm save-only baseline failed: ' + why]
            overhead = int(save_only['cycles']) - int(ref['cycles'])
            generics['LOAD_AT'] = generics['SAVE_AT'] + overhead + int(ref['cycles']) // 3
            if args.reject_old_staged_format:
                generics['LOAD_OLD_FORMAT'] = 'true'
        ok, saved, saved_image, why = m.simulate(directory, generics, 'save-load-warm' if args.warm_round_trip else 'save-warm')
        print('warm save:', saved, flush=True)
        if not ok:
            return ['warm save failed: ' + why]
        if args.entry_kernel_gprs:
            expected = dict(kernel_k0='13579BDF', kernel_k1='2468ACE0')
            kernel_results.append(dict(reference=ref, observed=saved, image_identical=saved_image == image))
            if any(ref.get(k) != v for k, v in expected.items()):
                return ['kernel-GPR reference did not retain the authored values']
            if any(saved.get(k) != v for k, v in expected.items()):
                return ['kernel-GPR preservation failed: k0=' + saved.get('kernel_k0', '?') +
                        ' k1=' + saved.get('kernel_k1', '?')]
        if args.entry_probe:
            if (saved_image != image or saved.get('saves') != '0' or saved.get('saves_failed') != '1'
                    or saved.get('log_stores') != ref['log_stores']):
                return ['entry probe did not report an error and return to the unchanged guest']
            return []
        if saved_image != image or saved.get('saves') != '1' or saved.get('saves_failed') != '0':
            return ['warm save changed the guest or did not complete successfully']
        if args.reject_old_staged_format:
            if (saved.get('loads') != '0' or saved.get('loads_failed') != '1'
                    or saved.get('spu_writes') != ref.get('spu_writes')):
                return ['old staged format was not rejected before guest replay']
        elif args.warm_round_trip and not replayed_guest(ref, saved):
            return ['warm load did not complete and replay guest device writes']
        return []

    contention_matrix = []

    def check_contention(directory, record=True):
        ok, ref, image, why = m.simulate(directory, {}, 'contention-reference')
        if not ok:
            return ['contention reference failed: ' + why]
        at = int(ref['cycles']) // 3
        cases = [('control', {}), ('dma-before', dict(DMA_AT=at-1, DMA_FOR=64)),
                 ('dma-at-entry', dict(DMA_AT=at, DMA_FOR=1)),
                 ('dma-in-handler', dict(DMA_AT=at+100, DMA_FOR=64)),
                 ('irq-at-entry', dict(IRQ_AT=at, IRQ_FOR=8)),
                 ('dma-and-irq', dict(DMA_AT=at-1, DMA_FOR=64, IRQ_AT=at, IRQ_FOR=8))]
        for name, pins in cases:
            ok, stats, saved, why = m.simulate(directory,
                dict(SAVE_AT=at, MAX_CYCLES=int(ref['cycles'])*4, **pins), 'contention-' + name)
            errors = []
            if not ok:
                errors.append(name + ': ' + why)
            elif (saved != image or stats.get('saves') != '0' or stats.get('saves_failed') != '1'
                  or stats.get('log_stores') != ref['log_stores']
                  or stats.get('stress_dma') != str(pins.get('DMA_FOR', 0))
                  or stats.get('stress_irq') != str(pins.get('IRQ_FOR', 0))):
                errors.append(name + ': result, guest image or applied input-window counts differ')
            if record:
                contention_matrix.append(dict(case=name, pins=pins, statistics=stats, failures=errors))
            print('contention', name, stats, errors, flush=True)
            if errors:
                return errors
        return []

    matrix = []
    if args.entry_contention_matrix:
        failures = check_contention(directory)
    elif args.exp1_startup_matrix:
        rom = directory / 'rom.hex'
        original = rom.read_text()
        try:
            for config in (0x0013243f, 0x0013343f, 0x0013043f, 0x0013143f):
                # Write the real memctrl before enabling guest interrupts.
                # Existing bootstrap then initializes every guest register.
                bootstrap = (0x3c081f80, 0x3c090000 | config >> 16,
                             0x35290000 | config & 0xffff, 0xad091008, 0)
                rom.write_text(''.join(f'{word:08x}\n' for word in bootstrap) + original)
                errors = check(directory)
                matrix.append(dict(config=f'{config:08x}', failures=errors))
                # Preserve each case's captures instead of overwriting them.
                for path in directory.glob('dump-*.hex'):
                    shutil.copy2(path, directory / f'{config:08x}-{path.name}')
                if errors:
                    break
        finally:
            rom.write_text(original)
        failures = [f"EXP1 {row['config']}: {error}" for row in matrix for error in row['failures']]
    else:
        failures = check(directory)
    mutation = None
    contention_mutation = None
    if args.entry_contention_matrix and not failures:
        contention_fault = True
        bad = m.build('fault-disconnected-dma', seed=1)
        errors = check_contention(bad, record=False)
        caught = any('CPU retired while DMA stall input held' in error for error in errors)
        contention_mutation = dict(caught=caught, errors=errors)
        contention_fault = False
        if not caught:
            failures.append('disconnected DMA stall input was not caught')
    if args.check_fetch_fault and not failures:
        new = 'FetchAddr(28 downto 23) /= "111110" and FetchAddr(28 downto 23) /= "000000"'
        old = 'FetchAddr(31 downto 30) /= "11" and FetchAddr(28 downto 23) /= "000000"'
        bad = m.build('fault-exp1-fetch', cpu_fault=(new, old), seed=1)
        errors = check(bad)
        caught = any('handler opcode at BF000000' in error for error in errors)
        mutation = dict(caught=caught, errors=errors)
        if not caught:
            failures.append('old EXP1 fetch-error decode was not caught at handler entry')
    r1_readback = None
    if r1:
        # the guest's own readback of the R1 registers at the end of the reference run
        dump = directory / 'dump-reference.hex'
        if dump.exists():
            r1_readback = {k: f'{v:08x}' for k, v in r1.readback(dump.read_text()).items()}
            print('R1 readback (reference):', r1_readback, flush=True)
            if list(r1.readback(dump.read_text()).values()) != r1.READ_EXPECT:
                failures.append('R1 witness: the reference run does not read back the planted register values')
    changed = [path for path, digest in ledger.items() if sha(path) != digest]
    report = dict(project=str(project), fixture=str(fixture), guest_iterations=args.iterations,
                  failures=failures, mutation=mutation, warm_only=args.warm_only, warm_round_trip=args.warm_round_trip,
                  entry_probe=args.entry_probe, staged_full_context=args.staged_full_context,
                  staged_context_fault=args.staged_context_fault, staged_scalar_ring=args.staged_scalar_ring,
                  **(dict(staged_exact_voices=True) if args.staged_exact_voices else {}),
                  **(dict(routine_step=args.routine_step, routine_fault=args.routine_fault, stage2_bench=args.stage2_bench,
                          stage2_scenarios=args.scenario, max_cycles=args.max_cycles,
                          bench_wait_bound=args.bench_wait_bound, waits_left_out=args.waits_left_out)
                     if args.routine_step or args.stage2_bench else {}),
                  staged_lean_restore=args.staged_lean_restore,
                  staged_playback_restore=args.staged_playback_restore,
                  reject_old_staged_format=args.reject_old_staged_format,
                  entry_contention_matrix=contention_matrix, contention_mutation=contention_mutation,
                  entry_kernel_gprs=kernel_results,
                  preserve_kernel_gprs_candidate=args.preserve_kernel_gprs,
                  entry_kernel_guard=args.entry_kernel_guard, kernel_gpr_fault=args.kernel_gpr_fault,
                  kernel_guard_kind=args.kernel_guard_kind,
                  exp1_byte_bus_candidate=args.exp1_byte_bus, exp1_startup_matrix=matrix,
                  **(dict(rom_ext=rom_ext_info) if rom_ext_info else {}),
                  routine=args.routine, r1_remount=r1_rows, r1_readback=r1_readback, r1_mutation=args.r1_mutation,
                  r1_bench_disc_polls=args.r1_bench_disc_polls, r1_generics=r1_extra,
                  inputs_changed=changed, sha256=ledger,
                  scope='selected CPU path; portable RAM and upstream GTE/device models; no physical-top or hardware proof')
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('failures', 'inputs_changed', 'scope')}), flush=True)
    return bool(failures or changed)


if __name__ == '__main__':
    sys.exit(main())
