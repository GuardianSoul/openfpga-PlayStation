"""Only the same-instance binary top bit can replace an audio Gray top bit."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tool_paths import ROOT, find_tool
from prepare_pocket_audio_stream import refresh_audio_scope
from prepare_pocket_early import OLD


def interpreter():
    if shutil.which('tclsh'):
        return [shutil.which('tclsh')]
    quartus = find_tool('quartus_sh')
    return [quartus, '-t'] if quartus else []


class AudioGrayScope(unittest.TestCase):
    def test_early_project_refreshes_inherited_scope_without_other_sdc_changes(self):
        legacy = (ROOT / 'tests/fixtures/audio_gray_scope_legacy.tcl').read_text()
        current = refresh_audio_scope(legacy)
        for procedure in (legacy, current, legacy.replace('gray[6]', 'gray[5]')):
            with self.subTest(procedure=procedure == legacy), tempfile.TemporaryDirectory() as tmp:
                base, output = Path(tmp)/'base', Path(tmp)/'output'
                base.mkdir()
                (base/'core_top.sv').write_text(OLD)
                (base/'apf_top.qsf').write_text('set_global_assignment -name NUM_PARALLEL_PROCESSORS 4\n')
                original = '# captured base\n' + procedure + '\nset_max_delay 20.0\nset_min_delay 0.0\n'
                (base/'pocket.sdc').write_text(original)
                run = subprocess.run([sys.executable, str(ROOT/'scripts/prepare_pocket_early.py'),
                                      str(base), str(output)], capture_output=True, text=True, timeout=30)
                self.assertEqual((base/'pocket.sdc').read_text(), original)
                if procedure not in (legacy, current):
                    self.assertNotEqual(run.returncode, 0)
                    self.assertIn('Unreviewed copied audio Gray scope procedure', run.stderr)
                else:
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                    self.assertEqual((output/'pocket.sdc').read_text(), refresh_audio_scope(original))

    def test_only_reviewed_inline_procedure_is_refreshed(self):
        legacy = (ROOT / 'tests/fixtures/audio_gray_scope_legacy.tcl').read_text()
        before = '# preserved prefix\n' + legacy + '\nset_max_delay 20.0\nset_min_delay 0.0\n'
        after = refresh_audio_scope(before)
        self.assertTrue(after.startswith('# preserved prefix\n'))
        self.assertTrue(after.endswith('\nset_max_delay 20.0\nset_min_delay 0.0\n'))
        self.assertEqual(refresh_audio_scope(after), after)
        self.assertIn('physical_copies_', after)
        for text in (legacy.replace('gray[6]', 'gray[5]'),
                     legacy + legacy, '# missing procedure\n'):
            with self.subTest(text=text[:30]), self.assertRaises(ValueError):
                refresh_audio_scope(text)

    @unittest.skipUnless(interpreter(), 'Tcl interpreter unavailable')
    def test_logical_coverage_alias_identity_and_all_replicas(self):
        prefix = 'core_top:ic|psx_audio_bridge:audio_stream|'
        program = '''
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {var collection body} {
 foreach item $collection {uplevel 1 [list set $var $item];uplevel 1 $body}
}
proc get_registers {query} {
 if {[llength $query]==2} {return [concat $::gray $::binary]}
 if {$query eq [format {%s%s_gray[*]} $::prefix $::pointer]} {return $::gray}
 if {$query eq [format {%s%s_gray[6]} $::prefix $::pointer]} {return $::high_gray}
 if {$query eq [format {%s%s_bin[6]} $::prefix $::pointer]} {return $::binary}
 error UNEXPECTED_QUERY
}
''' + (ROOT / 'scripts/pocket_audio_scope.tcl').read_text()
        cases = 0
        for pointer in ('wr', 'rd'):
            gray = [prefix + f'{pointer}_gray[{bit}]' for bit in range(7)]
            binary = prefix + f'{pointer}_bin[6]'
            scenarios = [(gray, [], [], gray),
                         (gray + [gray[6] + '~DUPLICATE'], [], [], gray + [gray[6] + '~DUPLICATE']),
                         (gray[:6], [binary], [], gray[:6] + [binary]),
                         (gray[:6], [binary, binary + '~DUPLICATE'], [], gray[:6] + [binary, binary + '~DUPLICATE']),
                         (gray[:6], [binary + '~DUPLICATE2'], [], gray[:6] + [binary + '~DUPLICATE2']),
                         ([gray[0] + '~DUPLICATE'] + gray[1:6], [binary, binary + '~DUPLICATE'], [],
                          [gray[0] + '~DUPLICATE'] + gray[1:6] + [binary, binary + '~DUPLICATE']),
                         (gray[:6], [], [], None),
                         (gray[:6], [binary.replace('[6]', '[5]')], [], None),
                         (gray[:6], ['other|' + binary], [], None),
                         (gray[:6], [binary + '~unknown'], [], None),
                         (gray[:6], [binary], [gray[6]], None),
                         (gray[:3] + gray[4:6], [binary], [], None),
                         (gray[:6] + ['other|' + gray[6]], [], [], None)]
            for g, b, high, expected in scenarios:
                def quoted(values):
                    return '[list ' + ' '.join('{' + v + '}' for v in values) + ']'
                program += f'''
set prefix {{{prefix}}};set pointer {pointer}
set gray {quoted(g)};set binary {quoted(b)};set high_gray {quoted(high)}
set code [catch {{pocket_audio_gray_bus $prefix $pointer}} result]
if {{($code==0)!={int(expected is not None)}}} {{error CASE_{cases}_WRONG_VERDICT}}
'''
                if expected is not None:
                    program += f'if {{$result ne {quoted(expected)}}} {{error CASE_{cases}_DROPPED_COPY}}\n'
                cases += 1
        program += 'puts PASS_AUDIO_GRAY_CASES_26\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'check.tcl'
            path.write_text(program)
            run = subprocess.run([*interpreter(), str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('PASS_AUDIO_GRAY_CASES_26', run.stdout)


if __name__ == '__main__':
    unittest.main()
