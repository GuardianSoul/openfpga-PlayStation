"""Audit full-system simulation's explicit compatibility and memory bounds."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_system_sim import prepare_memory_models, observed_system_source, compatible_async_video_source
from scripts.prepare_resource_targets import digest
from scripts.prepare_system_verilator import prepare as prepare_verilator


class SystemSimulationSourcesTests(unittest.TestCase):
    def test_memory_models_record_portable_preload_and_unchanged_protocol_sections(self):
        with tempfile.TemporaryDirectory() as temporary:
            sources=prepare_memory_models(Path(temporary))
            for source in sources:
                original=Path(source['original']).read_text(); adapted=Path(source['path']).read_text()
                self.assertEqual(source['original_sha256'],digest(Path(source['original'])))
                self.assertEqual(source['sha256'],digest(Path(source['path'])))
                if Path(source['path']).name=='sdram_model3x.vhd':
                    begin=original.index('      clk1xToggle3x   <= clk1xToggle;')
                    end=original.index("      if (SCRIPTLOADING = '1') then")
                    self.assertIn(original[begin:end],adapted)
                    self.assertIn('readline(boot_file,boot_line); hread(boot_line,boot_byte);',adapted)
                    self.assertIn('targetpos := INITADDR;',adapted)
                else:
                    self.assertEqual(original.replace('type t_data is array(0 to (2**28)-1) of integer;',
                        'type t_data is array(0 to (2**24)+511) of integer;'),adapted)

    def test_system_observations_are_simulation_only_and_logger_is_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=observed_system_source(Path(temporary))
            text=Path(source['path']).read_text()
            self.assertIn('gexport : if false generate',text)
            self.assertIn('sim_cpu_state <= cpu_export;',text)
            self.assertEqual(text.count('resize(joypad'),4)
            self.assertFalse(source['production_reference_modified'])
            source=compatible_async_video_source(Path(temporary))
            self.assertEqual(source['sha256'],digest(Path(source['path'])))
            self.assertFalse(source['production_reference_modified'])

    def test_transcode_sources_keep_pins_and_explicitly_separate_divider_directions(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=prepare_verilator(Path(temporary))
            self.assertFalse(result['production_reference_modified'])
            self.assertTrue(result['fast_reset'])
            sources={Path(source['path']).name:source for source in result['sources']}
            for source in result['sources']:
                self.assertEqual(source['sha256'],digest(Path(source['path'])))
                self.assertEqual(source['original_sha256'],digest(Path(source['original'])))
            for name in ('gpu_poly.vhd','gpu_line.vhd'):
                text=Path(sources[name]['path']).read_text()
                self.assertNotIn(': inout div_type',text)
                self.assertEqual(text.count('_in : in div_type'),6)
                self.assertEqual(text.count('_out : out div_type'),6)
                self.assertNotIn('abs(',text if name=='gpu_poly.vhd' else '')
            polygon=Path(sources['gpu_poly.vhd']['path']).read_text()
            self.assertEqual(polygon.count('signed\'(\"000000000\" & x\"100000000\")'),3)
            self.assertEqual(polygon.count('magnitude(vt('),9)
            self.assertEqual(sources['dpram.vhd']['library'],'ramip')

    def test_logo_skip_is_explicit_and_retains_complete_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=prepare_verilator(Path(temporary),fast_reset=False,fast_boot=True)
            self.assertTrue(result['fast_boot'])
            self.assertFalse(result['fast_reset'])
            wrapper=next(source for source in result['sources']
                         if Path(source['path']).stem=='psx_mister_psx_top_resource')
            text=Path(wrapper['path']).read_text()
            self.assertIn("fastboot => '1'",text)
            self.assertNotIn("is_simu=>'1'",text)
            self.assertFalse(result['production_reference_modified'])


if __name__=='__main__': unittest.main()
