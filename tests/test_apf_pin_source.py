"""APF pin models reject changed wiring; retiming preserves the raw encoder."""
import math
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_apf_pin_timing import (ROOT,I2S_SHA,AUDIO_WRAPPER,
    framework_contract,core_contract,falling_audio,prepare_project)
from prepare_resource_targets import digest


CORE='''module core_top;
altera_pll #(.phase_shift0("0 ps")) cpu_pll (.refclk(clk_74a));
altera_pll #(.number_of_clocks(2),.output_clock_frequency0("53.693166 MHz"),
.phase_shift0("0 ps"),.duty_cycle0(50),.output_clock_frequency1("53.693166 MHz"),
.phase_shift1("4656 ps"),.duty_cycle1(50)) video_pll
(.refclk(clk_74a),.outclk({video_rgb_clock_90,video_rgb_clock}));
mf_audio_pll audio_pll (.outclk_0(audio_mclk));
psx_audio_bridge audio_stream (.audio_clk(audio_mclk));
endmodule
'''


class APFPinSource(unittest.TestCase):
    def test_known_wiring_and_independent_cpu_phase(self):
        core_contract(CORE)
        framework_contract((ROOT/'references/MIRLO64/platform/pocket/apf_top.v').read_text())
        for before,after in [('4656 ps','0 ps'),('53.693166 MHz','50 MHz'),
                ('.refclk(clk_74a),.outclk','.refclk(clk_74b),.outclk'),
                ('.audio_clk(audio_mclk)','.audio_clk(clk_74a)')]:
            with self.subTest(before=before),self.assertRaises(ValueError):core_contract(CORE.replace(before,after))
        apf=(ROOT/'references/MIRLO64/platform/pocket/apf_top.v').read_text()
        for before,after in [('video_rgb[23:12]','video_rgb[11:0]'),('video_rgb_clock_90','video_rgb_clock'),
                ('scal_ddio_ctrl[3]','scal_ddio_ctrl[2]'),('scal_audlrck','wrong_pin')]:
            with self.subTest(before=before),self.assertRaises(ValueError):framework_contract(apf.replace(before,after))

    def test_raw_encoder_restores_exactly(self):
        raw=(ROOT/'rtl/audio/psx_audio_i2s.sv').read_text();self.assertEqual(digest(ROOT/'rtl/audio/psx_audio_i2s.sv'),I2S_SHA)
        changed=falling_audio(raw);self.assertTrue(changed.endswith(AUDIO_WRAPPER))
        self.assertEqual(changed[:-len(AUDIO_WRAPPER)].replace('module psx_audio_i2s_raw (','module psx_audio_i2s ('),raw)
        with self.assertRaises(ValueError):falling_audio(changed)

    def test_preparation_and_failures_do_not_modify_guest_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'core_top.sv').write_text(CORE)
            for name,source in [('apf_top.v',ROOT/'references/MIRLO64/platform/pocket/apf_top.v'),
                    ('mf_ddio_bidir_12.v',ROOT/'references/MIRLO64/platform/pocket/mf_ddio_bidir_12.v'),
                    ('psx_audio_i2s.sv',ROOT/'rtl/audio/psx_audio_i2s.sv')]:
                (p/name).write_bytes(source.read_bytes())
            (p/'pocket.sdc').write_text('# no unreviewed clock cuts\n')
            pins=['scal_clk','scal_vs','scal_hs','scal_de','scal_skip','scal_audmclk','scal_auddac','scal_audlrck']+[f'scal_vid[{i}]' for i in range(12)]
            q='\n'.join(f'set_global_assignment -name {kind} "{p/name}"' for kind,name in [('VERILOG_FILE','apf_top.v'),('VERILOG_FILE','mf_ddio_bidir_12.v'),('SYSTEMVERILOG_FILE','core_top.sv'),('SYSTEMVERILOG_FILE','psx_audio_i2s.sv')])+'\n'
            q+='\n'.join(f'set_instance_assignment -name IO_STANDARD "1.8 V" -to {pin}' for pin in pins)+'\n'
            (p/'apf_top.qsf').write_text(q)
            def catalog():return {str(f.relative_to(p)):digest(f) for f in p.rglob('*') if f.is_file()}
            before=catalog();expected=(digest(p/'core_top.sv'),digest(p/'apf_top.v'))
            opts=dict(video_setup_ns=1,video_hold_ns=1,audio_setup_ns=1,audio_hold_ns=1)
            for key,value in [('audio_hold_ns',-1),('video_setup_ns',math.nan),('board_min_ns',1),('board_max_ns',math.inf)]:
                with self.subTest(key=key),self.assertRaises(ValueError):prepare_project(p,*expected,**(opts|{key:value}))
                self.assertEqual(catalog(),before)
            with self.assertRaises(ValueError):prepare_project(p,'0'*64,expected[1],**opts)
            self.assertEqual(catalog(),before)
            (p/'apf_top.qsf').write_text(q.replace('"1.8 V" -to scal_clk','"2.5 V" -to scal_clk'))
            with self.assertRaises(ValueError):prepare_project(p,*expected,**opts)
            self.assertFalse((p/'apf-pin-timing').exists())
            (p/'apf_top.qsf').write_text(q)
            self.assertEqual(catalog(),before)
            result=prepare_project(p,*expected,**opts,falling_audio_pads=True)
            for name,h in before.items():
                if name!='apf_top.qsf':self.assertEqual(digest(p/name),h)
            self.assertTrue(result['analysis_only']);self.assertFalse(result['external_electrical_requirements_qualified'])
            self.assertFalse(result['additional_exceptions']);self.assertTrue(result['half_MCLK_output_latency_added'])
            self.assertIn('apf-pin-timing/psx_audio_i2s.sv',(p/'apf_top.qsf').read_text())
            self.assertEqual((p/'apf-pin-timing/psx_audio_i2s.sv').read_text(),falling_audio((p/'psx_audio_i2s.sv').read_text()))
            after=catalog()
            with self.assertRaises(ValueError):prepare_project(p,*expected,**opts)
            self.assertEqual(catalog(),after)


if __name__=='__main__':unittest.main()
