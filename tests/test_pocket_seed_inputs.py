"""The seed study must isolate compiler inputs from concurrent checkout edits."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import measure_pocket_seeds as study


class PocketSeedInputsTests(unittest.TestCase):
    def test_clone_cannot_inherit_a_completed_parents_success_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            (source/'result.json').write_text('{"fit_verified":true,"labs":1844}\n')
            (source/'timing-inputs.json').write_text('{"old_target":"parent"}\n')
            (source/'manifest.json').write_text('{"input_profile":"preserve"}\n')
            nested=source/'resources';nested.mkdir()
            (nested/'result.json').write_text('{"resource":"input, not fit evidence"}\n')
            output=root/'new-job';study.clone(source,output)
            self.assertFalse((output/'result.json').exists())
            self.assertFalse((output/'timing-inputs.json').exists())
            self.assertEqual((output/'manifest.json').read_text(),(source/'manifest.json').read_text())
            self.assertEqual((output/'resources/result.json').read_text(),(nested/'result.json').read_text())
            self.assertIn('true',(source/'result.json').read_text())

    def fixture(self,root):
        captured=root/'captured';captured.mkdir()
        external=root/'external.sv';external.write_text('`include \"defs.vh\"\nmodule external; // ORIGINAL\nendmodule\n')
        includes=root/'headers';includes.mkdir();(includes/'defs.vh').write_text('`define ORIGINAL 1\n')
        adapter=captured/'adapter.sv';adapter.write_text('module adapter; endmodule\n')
        rom=captured/'coeff.hex';rom.write_text('00\n01\n02\n03\n')
        core=captured/'core_top.sv';core.write_text('module core_top; reg [7:0] mem[0:3]; initial $readmemh("'+str(rom)+'",mem); endmodule\n')
        (captured/'pocket.sdc').write_text('derive_clock_uncertainty\n')
        (captured/'apf_top.qpf').write_text('PROJECT_REVISION = "apf_top"\n')
        (captured/'apf_top.qsf').write_text('set_global_assignment -name SEED 3\n'+''.join(
            'set_global_assignment -name SYSTEMVERILOG_FILE "'+str(p)+'" -library work\n' for p in (adapter,core,external))+
            'set_global_assignment -name SDC_FILE pocket.sdc\n'+
            'set_global_assignment -name SEARCH_PATH \"'+str(includes)+'\"\n')
        scripts=root/'scripts';scripts.mkdir()
        for name in ('measure_spu_scalar_full_top.py','run_resource_fit.py','resource_report.py',
                     'prepare_resource_targets.py','tool_paths.py'):
            (scripts/name).write_text('# fixture tool identity\n')
        replacement=root/'replacement.sv';replacement.write_text(adapter.read_text()+'// replacement\n')
        args=['measure_pocket_seeds.py',str(captured),'--source','adapter.sv',
            '--expected-original-sha256',study.digest(adapter),'--replacement',str(replacement),'--label','test only']
        return captured,external,args

    def test_external_edits_cannot_leak_into_later_seeds_or_ROM_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);captured,external,args=self.fixture(root);seen=[]
            def compiler(binary,target,device,timeout):
                case=Path(target['directory']);sources=target['quartus_sources']
                self.assertTrue(all(Path(s['path']).is_relative_to(case) for s in sources))
                self.assertTrue(all(study.digest(Path(s['path']))==s['sha256'] for s in sources))
                ext=next(Path(s['path']) for s in sources if Path(s['path']).name=='external.sv')
                self.assertIn('ORIGINAL',ext.read_text());self.assertNotIn('MUTATED',ext.read_text())
                header=next(Path(s['path']) for s in sources if Path(s['path']).name=='defs.vh')
                self.assertEqual(header.read_text(),'`define ORIGINAL 1\n')
                self.assertEqual(study.search_directories(case),[header.parent])
                rom=next(Path(s['path']) for s in sources if s['assignment']=='ROM')
                self.assertEqual(rom.read_text(),'00\n01\n02\n03\n')
                adapter=next(Path(s['path']) for s in sources if Path(s['path']).name=='adapter.sv')
                self.assertEqual('// replacement' in adapter.read_text(),case.name.startswith('candidate'))
                seen.append(case.name)
                if len(seen)==1:
                    external.write_text('MUTATED during active fit\n')
                    (root/'headers/defs.vh').write_text('MUTATED header\n')
                output=case/'output_files';output.mkdir()
                (output/'apf_top.fit.rpt').write_text('; Total LABs:  partially or completely used ; 100 / 1,848 ;\n')
                return dict(directory=str(case),steps=[dict(tool=t,exit_code=0) for t in ('quartus_map','quartus_fit','quartus_sta')],
                    source_identity_verified=True,project_identity_verified=True,fit_verified=True,tool_error_messages=[])
            with patch.object(study,'ROOT',root),patch.object(study,'find_tool',return_value='/fixture/quartus_sh'),\
                    patch.object(study,'fit_target',side_effect=compiler),patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):
                study.main()
            self.assertEqual(seen,['control-s1','candidate-s1','control-s3','candidate-s3'])
            report=json.loads(next((root/'build/resources/pocket-seeds/runs').glob('*/report.json')).read_text())
            self.assertTrue(report['passed']);self.assertTrue(report['source_identity_verified'])
            self.assertNotEqual(study.digest(external),report['original_input_sha256'][str(external)])

    def test_wrong_expected_source_is_rejected_before_any_compiler(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);captured,external,args=self.fixture(root)
            args[args.index('--expected-original-sha256')+1]='0'*64
            with patch.object(study,'ROOT',root),patch.object(study,'fit_target') as compiler,\
                    patch.object(sys,'argv',args),self.assertRaisesRegex(ValueError,'expected baseline'):
                study.main()
            compiler.assert_not_called()

    def test_project_dependent_replacement_requires_preparation_before_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);captured,external,args=self.fixture(root)
            args[args.index('--source')+1]='core_top.sv'
            args[args.index('--expected-original-sha256')+1]=study.digest(captured/'core_top.sv')
            with patch.object(study,'ROOT',root),patch.object(study,'fit_target') as compiler,\
                    patch.object(sys,'argv',args),self.assertRaisesRegex(ValueError,'embeds project/input paths'):
                study.main()
            compiler.assert_not_called()
