"""Hold preparation must stay source-bound, keep local timing and150ps."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_native_hold_routes import (CARD_MIN, held_card_scope_text,
                                        prepare_project, verify_payload_proof)
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


class NativeHoldRoutes(unittest.TestCase):
    def test_card_changes_only_actual_crossing_minimum_and_rejects_unknown_scope(self):
        before = (ROOT / 'scripts/pocket_card_scope.tcl').read_text()
        after = held_card_scope_text(before)
        self.assertEqual(after.replace(CARD_MIN.replace(' 0\n', ' -13\n'), CARD_MIN), before)
        self.assertEqual(before.count(CARD_MIN), 1)
        for bad in (before + '\n', after, before.replace('set_max_skew', 'set_false_path')):
            with self.assertRaises(ValueError): held_card_scope_text(bad)

    def test_missing_failed_or_changed_proof_cannot_approve_card_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'proof.json'; source = Path(tmp) / 'formatter.vhd'; source.write_text('drift')
            for value in ({}, {'passed': False}, {'passed': True, 'positive_body_reverses_exactly': True,
                         'runner_sha256': '0' * 64}):
                p.write_text(json.dumps(value))
                with self.assertRaises(ValueError): verify_payload_proof(p, source)

    def test_failed_project_checks_leave_inputs_and_output_untouched(self):
        parent = ROOT / 'build/sim/native-hold-unit'; parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            d = Path(tmp); core = d / 'core_top.sv'; core.write_text('module core_top; endmodule\n')
            hold = d / 'pocket_cpu_hold.sdc'; hold.write_text('set pocket_cpu_hold_ns 0.15\n')
            dma = d / 'dma.vhd'; dma.write_text('-- unreviewed DMA\n')
            qsf = d / 'apf_top.qsf'; pocket = d / 'pocket.sdc'; pocket.write_text('# untouched\n')
            qsf.write_text(''.join(f'set_global_assignment -name {k} "{p}"\n' for k, p in (
                ('SYSTEMVERILOG_FILE', core), ('VHDL_FILE', dma), ('SDC_FILE', hold))))
            files = [core, hold, dma, qsf, pocket]; before = {p: digest(p) for p in files}
            for kwargs, sha in (({}, digest(core)), ({'dma': True}, '0' * 64),
                                ({'dma': True}, digest(core)), ({'card': True}, digest(core))):
                with self.subTest(kwargs=kwargs, sha=sha), self.assertRaises(ValueError):
                    prepare_project(d, sha, **kwargs)
                self.assertEqual({p: digest(p) for p in files}, before)
                self.assertFalse((d / 'hold-contracts').exists())
            hold.write_text('set pocket_cpu_hold_ns 0.1\n')
            with self.assertRaisesRegex(ValueError, '150ps'):
                prepare_project(d, digest(core), dma=True)
            (d / 'db').mkdir()
            with self.assertRaisesRegex(ValueError, 'before map'):
                prepare_project(d, digest(core), dma=True)

    def test_dma_scope_cannot_add_timing_cuts_or_replace_uncertainty(self):
        text = (ROOT / 'scripts/pocket_dma_address_route.tcl').read_text()
        for command in ('set_false_path', 'set_multicycle_path', 'set_min_delay',
                        'set_max_delay', 'set_clock_groups', 'set_clock_uncertainty'):
            self.assertNotIn(command, text)
        self.assertIn('set_net_delay -min 0.5 -from $dma_route_sources -to $dma_route_targets', text)
        self.assertIn('$pocket_cpu_hold_ns!=0.15', text)

    def test_full_address_bank_and_replicas_not_one_failing_bit(self):
        text = (ROOT / 'scripts/pocket_dma_address_route.tcl').read_text()
        procedures = text[:text.index('set dma_route_clock')]
        command = [shutil.which('tclsh')] if shutil.which('tclsh') else [find_tool('quartus_sh'), '-t']
        owner = 'core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|dma:idma|'
        base = [owner + f'nextAddr[{i}]' for i in range(24)]
        cases = [(base, True), (base + [base[13] + '~DUPLICATE2'], True),
                 (base[:13] + base[14:], False), ([base[13]], False),
                 (base + [owner + 'nextAddr[24]'], False),
                 ([v.replace('dma:idma', 'dma:other') for v in base], False),
                 (base + [base[13] + '~UNKNOWN'], False)]
        stubs = '''proc get_registers {args} {return $::nodes}
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {v c b} {foreach x $c {uplevel 1 [list set $v $x];uplevel 1 $b}}
'''
        with tempfile.TemporaryDirectory() as tmp:
            for nodes, good in cases:
                p = Path(tmp) / 'bank.tcl'
                p.write_text(stubs + procedures + '\nset nodes [list ' +
                             ' '.join('{' + n + '}' for n in nodes) + ']\ndma_route_regs nextAddr\n')
                result = subprocess.run([*command, str(p)], capture_output=True, text=True, timeout=15)
                with self.subTest(nodes=nodes):
                    self.assertEqual(result.returncode == 0, good, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
