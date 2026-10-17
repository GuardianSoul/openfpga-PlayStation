"""Whole-bank GPU routing must not become a per-bit pad or timing waiver."""
import copy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_pocket_gpu_stage_route import prepare_project
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


class GPUStageRoute(unittest.TestCase):
    def test_rejected_project_leaves_sources_constraints_and_output_untouched(self):
        parent = ROOT / 'build/sim/gpu-stage-route-unit'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            d = Path(tmp)
            core = d / 'core_top.sv'; core.write_text('module core_top; endmodule\n')
            pixel = d / 'gpu_pixelpipeline.vhd'; pixel.write_text('-- unreviewed body\n')
            hold = d / 'pocket_cpu_hold.sdc'; hold.write_text('set pocket_cpu_hold_ns 0.15\n')
            qsf = d / 'apf_top.qsf'
            qsf.write_text(''.join(f'set_global_assignment -name {kind} "{p}"\n' for kind, p in (
                ('SYSTEMVERILOG_FILE', core), ('VHDL_FILE', pixel), ('SDC_FILE', hold))))
            paths = (core, pixel, hold, qsf); before = {p: digest(p) for p in paths}
            for sha, message in (('0' * 64, 'top identity'), (digest(core), 'pipeline source')):
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    prepare_project(d, sha)
                self.assertEqual(before, {p: digest(p) for p in paths})
                self.assertFalse((d / 'gpu-stage-route').exists())
            hold.write_text('set pocket_cpu_hold_ns 0.1\n')
            with self.assertRaisesRegex(ValueError, '150ps'):
                prepare_project(d, digest(core))
            self.assertFalse((d / 'gpu-stage-route').exists())
            (d / 'db').mkdir()
            with self.assertRaisesRegex(ValueError, 'before map'):
                prepare_project(d, digest(core))

    def test_outside_build_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'ignored build'):
                prepare_project(tmp, '0' * 64)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_no_path_exception_clock_change_or_rtl_padding(self):
        text = (ROOT / 'scripts/pocket_gpu_stage_route.tcl').read_text()
        for command in ('set_false_path', 'set_multicycle_path', 'set_min_delay',
                        'set_max_delay', 'set_clock_groups', 'set_clock_uncertainty'):
            self.assertNotIn(command, text)
        self.assertIn('set_net_delay -min 0.5 -from $gpu_stage_sources -to $gpu_stage_targets', text)
        self.assertIn('$pocket_cpu_hold_ns!=0.15', text)

    def test_complete_bank_replicas_faults_and_unrelated_address_bank(self):
        command = [shutil.which('tclsh')] if shutil.which('tclsh') else [find_tool('quartus_sh'), '-t']
        scope = (ROOT / 'scripts/pocket_gpu_stage_route.tcl').read_text()
        owner = 'core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|gpu:igpu|gpu_pixelpipeline:igpu_pixelpipeline|'
        flags = ('valid', 'texture', 'transparent', 'rawTexture', 'dithering')
        lanes = [(flag, None) for flag in flags]
        for field, width in (('x', 10), ('y', 9), ('cr', 8), ('cg', 8), ('cb', 8),
                             ('u', 8), ('v', 8), ('oldPixel', 15)):
            lanes += [(field, i) for i in range(width)]
        def node(stage, field, bit=None):
            return owner + stage + '_' + field + (f'[{bit}]' if bit is not None else '')
        source = [node('stageS', f, i) for f, i in lanes]
        target = [node('stage0', f, i) for f, i in lanes]
        base_edges = {a: [b] for a, b in zip(source, target)}
        cases = []
        def case(name, nodes=None, edges=None, error=None, period=14.76255, margin=0.15, clock=True):
            cases.append((name, list(nodes if nodes is not None else source + target),
                          copy.deepcopy(edges if edges is not None else base_edges), error,
                          period, margin, clock))
        case('complete')
        replicas = [node('stage0', f, i) + '~DUPLICATE2' for f, i in (('u', 2), ('v', 0), ('v', 2))]
        src_replica = node('stageS', 'u', 2) + '~DUPLICATE'
        edges = copy.deepcopy(base_edges)
        for f, i in (('u', 2), ('v', 0), ('v', 2)):
            edges[node('stageS', f, i)].append(node('stage0', f, i) + '~DUPLICATE2')
        edges[src_replica] = list(edges[node('stageS', 'u', 2)])
        case('physical-replicas', source + target + replicas + [src_replica], edges)
        addresses = [owner + f'stage0_textaddr_1[0][{i}]' for i in range(1, 20)]
        case('later-addresses-excluded', source + target + addresses)
        case('missing-source-bit', [n for n in source + target if n != node('stageS', 'u', 3)],
             error='Incomplete GPU handoff bank')
        case('missing-target-bit', [n for n in source + target if n != node('stage0', 'v', 7)],
             error='Incomplete GPU handoff bank')
        case('only-failing-bit', [node('stageS', 'transparent'), node('stage0', 'transparent')],
             error='Incomplete GPU handoff bank')
        case('unknown-field', source + target + [owner + 'stageS_filter'],
             error='Unknown GPU handoff register')
        case('unknown-replica', source + target + [node('stageS', 'u', 3) + '~UNKNOWN'],
             error='Unknown GPU handoff register')
        case('wrong-owner', [n.replace('|gpu:igpu|', '|gpu:other|') if n == node('stageS', 'u', 3)
                            else n for n in source + target], error='Incomplete GPU handoff bank')
        edges = copy.deepcopy(base_edges); edges[node('stageS', 'u', 0)] = [node('stage0', 'u', 1)]
        case('wrong-bit-lane', edges=edges, error='crossed field/bit lanes')
        edges = copy.deepcopy(base_edges); edges[node('stageS', 'u', 0)] = []
        case('missing-source-consumer', edges=edges, error='Missing GPU handoff consumer')
        case('orphan-destination-replica', source + target + [node('stage0', 'valid') + '~DUPLICATE'],
             error='destination has no source')
        case('unexpected-address-bit', source + target + [owner + 'stage0_textaddr_1[0][0]'],
             error='Unknown GPU handoff register')
        case('wrong-clock', period=29.5251, error='handoff2x clock differs')
        case('missing-clock', clock=False, error='handoff2x clock differs')
        case('reduced-hold-reserve', margin=0.1, error='unchanged150ps reserve')
        case('legacy-caller-global-coupling')
        # Recreate the actual bug: caller-local assignments plus procedure
        # globals happen to work at top level but fail inside read_sdc's
        # private evaluation namespace.
        legacy = scope.replace('    lassign [gpu_stage_profile] gpu_stage_owner gpu_stage_fields',
                               '    global gpu_stage_owner gpu_stage_fields')
        self.assertEqual(scope.count('    lassign [gpu_stage_profile] gpu_stage_owner gpu_stage_fields'), 2)
        legacy = legacy.replace('proc gpu_stage_key {name stage} {',
                    'lassign [gpu_stage_profile] gpu_stage_owner gpu_stage_fields\nproc gpu_stage_key {name stage} {', 1)
        stubs = '''proc get_registers {args} {
 set result {};foreach pattern [lindex $args end] {foreach n $::nodes {
  if {$pattern eq $n || [string match $pattern $n]} {lappend result $n}
 }};return [lsort -unique $result]
}
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {v c body} {foreach x $c {uplevel 1 [list set $v $x];uplevel 1 $body}}
proc get_clocks {args} {if {$::clock_present} {return [list cpu2x]};return {}}
proc get_clock_info {args} {return $::clock_period}
proc get_fanouts {collection} {return [dict get $::edges [lindex $collection 0]]}
proc set_net_delay {args} {
 if {[llength $args]!=6 || [lrange $args 0 2] ne {-min 0.5 -from} || [lindex $args 4] ne "-to"} {error "Unexpected routing command"}
 set ::route_called 1
 set ::route_sources [lindex $args 3];set ::route_targets [lindex $args 5]
 foreach n $::route_targets {if {[string match *textaddr* $n]} {error "Later address bank was constrained"}}
}
'''
        def quoted_list(names):
            return '[list ' + ' '.join('{' + n + '}' for n in names) + ']'
        with tempfile.TemporaryDirectory() as tmp:
            for context in ('global', 'namespace'):
                for name, nodes, edges, error, period, margin, clock in cases:
                    fixture = (f'\nset nodes {quoted_list(nodes)}\nset edges [dict create]\n'
                               f'set clock_period {period}\nset clock_present {int(clock)}\n'
                               f'set pocket_cpu_hold_ns {margin}\nset route_called 0\n')
                    for a, b in edges.items():
                        fixture += 'dict set edges {' + a + '} ' + quoted_list(b) + '\n'
                    body = legacy if name == 'legacy-caller-global-coupling' else scope
                    if context == 'namespace':
                        body = f'namespace eval sdc_reader {{\nset pocket_cpu_hold_ns {margin}\n' + body + '\n}\n'
                    expected_error = ('gpu_stage_owner' if context == 'namespace' else None
                                      ) if name == 'legacy-caller-global-coupling' else error
                    p = Path(tmp) / (context + '-' + name + '.tcl')
                    p.write_text(stubs + fixture + body + '\nif {!$route_called} {error "No routing guide"}\n'
                                 'puts "BANK_COUNTS [llength $route_sources] [llength $route_targets]"\n')
                    result = subprocess.run([*command, str(p)], text=True, capture_output=True, timeout=15)
                    with self.subTest(case=name, context=context):
                        output = result.stdout + result.stderr
                        if expected_error:
                            self.assertNotEqual(result.returncode, 0, output)
                            self.assertIn(expected_error, output)
                        else:
                            self.assertEqual(result.returncode, 0, output)
                            self.assertIn('BANK_COUNTS ' + ('80 82' if name == 'physical-replicas' else '79 79'),
                                          output)


if __name__ == '__main__':
    unittest.main()
