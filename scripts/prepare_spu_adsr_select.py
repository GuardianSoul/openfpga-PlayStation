#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Source-pinned native SPU ADSR selector experiment; never a default selection.

Requires the measured reset-only controller/top and FIR-width SPU source.
Preserves registered settings, exact arithmetic, current targets and KEYON priority.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

EXPECTED = 'b75586c8b309299bd16e5f558760d69aa21db738a15de35b2d9af5f153eda256'
OUTPUT = '3f3c4e890fcb1e58d87550c8652d423a6c54c140e016b449b85fcb9661ae1a4f'
COMBINED_OUTPUT = '2202d796c5e22683566d51db44eb8ef1916dee496cbe83a4460d46dacdaeb58f'
CONTROLLER = 'd6ed2c12c4fa326a92eba2e3f8e9f77dc574e0d5623d9ef1b22b9f3730e03ef7'
TOP = '8f827d28e7c3349ace77e21204d3ca27acfcb4daeffd28066de41955d07ae6a9'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def adsr_select_text(text):
    require(hashlib.sha256(text.encode()).hexdigest() == EXPECTED, 'Exact FIR-width SPU required')
    t = text
    for a, b in [('   signal adsr_ticks    : tadsr_ticks;', '   signal adsr_ticks_selected : unsigned(23 downto 0);'), ('   signal adsr_step     : tadsr_steps;', '   signal adsr_step_selected : signed(15 downto 0);')]:
        assert t.count(a) == 1
        t = t.replace(a, b)
    a = '   signal adsr_rate_q   : tadsr_rate;'
    assert t.count(a) == 1
    t = t.replace(a, a + '\n   signal adsr_lookup_index : integer range 0 to 4;\n   signal adsr_selected_rate : unsigned(6 downto 0);\n   signal adsr_selected_dec : std_logic;\n')
    reads = ['adsr_ticks(to_integer(voice.adsrphase))'] + [f'adsr_ticks(ADSR_{x})' for x in ['OFF', 'ATTACK', 'DECAY', 'SUSTAIN', 'RELEASE']]
    n = 0
    for a in reads:
        n += t.count(a)
        t = t.replace(a, 'adsr_ticks_selected')
    assert n == 13, n
    a = 'adsr_step(adsr_index)'
    assert t.count(a) == 1
    t = t.replace(a, 'adsr_step_selected')
    start = t.index('   process (adsr_rate_q, adsr_dec_q, adsrSetting_q)')
    end = t.index('   end process;', start) + len('   end process;')
    block = t[start:end]
    loop = re.search('      for i in 0 to 4 loop\\n(.*?)      end loop;\\n', block, re.S)
    assert loop
    single = loop[1].replace('adsr_rate_q(i)', 'adsr_selected_rate').replace('adsr_dec_q(i)', 'adsr_selected_dec').replace('adsr_ticks(i)', 'adsr_ticks_selected').replace('adsr_step(i)', 'adsr_step_selected')
    block = block[:loop.start()] + single + block[loop.end():]
    block = block.replace('process (adsr_rate_q, adsr_dec_q, adsrSetting_q)', 'process (adsr_selected_rate, adsr_selected_dec, adsrSetting_q)')
    selector = "   -- Select the sole tick request before the wide decoder. The current\n   -- target stays separate: phase transition tests still use the old phase.\n   process (all)\n      variable phase : unsigned(2 downto 0);\n   begin\n      phase := voice.adsrphase;\n      if (state = VOICE_APPLYVOLUME) then\n         case (voice.adsrphase) is\n            when ADSRPHASE_ATTACK => phase := ADSRPHASE_DECAY;\n            when ADSRPHASE_DECAY => phase := ADSRPHASE_SUSTAIN;\n            when ADSRPHASE_RELEASE => phase := ADSRPHASE_OFF;\n            when others => null;\n         end case;\n      elsif (state = VOICE_CHECKKEY and index<24) then\n         if (KEYON(index)='1') then\n            phase := ADSRPHASE_ATTACK;\n         elsif (KEYOFF(index)='1') then\n            phase := ADSRPHASE_RELEASE;\n         end if;\n      end if;\n      case (phase) is\n         when ADSRPHASE_OFF => adsr_lookup_index <= ADSR_OFF;\n         when ADSRPHASE_ATTACK => adsr_lookup_index <= ADSR_ATTACK;\n         when ADSRPHASE_DECAY => adsr_lookup_index <= ADSR_DECAY;\n         when ADSRPHASE_SUSTAIN => adsr_lookup_index <= ADSR_SUSTAIN;\n         when ADSRPHASE_RELEASE => adsr_lookup_index <= ADSR_RELEASE;\n         when others => adsr_lookup_index <= ADSR_OFF;\n      end case;\n   end process;\n   adsr_selected_rate <= adsr_rate_q(adsr_lookup_index);\n   adsr_selected_dec <= adsr_dec_q(adsr_lookup_index);\n"
    t = t[:start] + selector + block + t[end:]
    assert not re.search('\\badsr_ticks\\b|\\badsr_step\\b', t)
    require(hashlib.sha256(t.encode()).hexdigest() == OUTPUT, 'Unexpected ADSR output')
    return t




def adsr_noise_text(text):
    t = adsr_select_text(text)
    add = '   signal noiseFraction : unsigned(8 downto 0);\n   signal noisePhase : unsigned(15 downto 0);\n   signal noiseShift_q : unsigned(3 downto 0);\n   signal noiseDelta : unsigned(8 downto 0);\n   signal noisePhaseStep : unsigned(1 downto 0);\n   signal noiseKeepMask : unsigned(15 downto 0);\n'
    a = '   signal noiseCheck             : unsigned(31 downto 0);'
    assert t.count(a) == 1
    t = t.replace(a, a + '\n' + add)
    a = "               noiseCnt             <= (others => '0');"
    assert t.count(a) == 1
    t = t.replace(a, "               noiseFraction <= (others => '0');\n               noisePhase <= (others => '0');")
    a = '            if (to_integer(SS_Adr) =  6) then noiseCnt             <= unsigned(SS_DataWrite(31 downto 0)); end if;'
    assert t.count(a) == 1
    t = t.replace(a, '            if (to_integer(SS_Adr) =  6) then noisePhase <= unsigned(SS_DataWrite(31 downto 16)); noiseFraction <= unsigned(SS_DataWrite(8 downto 0)); end if;')
    a = '            noiseCheck <= shift_right(to_unsigned(16#8000#, 16), to_integer(unsigned(cnt(13 downto 10)))) & x"0000";'
    assert t.count(a) == 1
    t = t.replace(a, '            noiseShift_q <= unsigned(cnt(13 downto 10));')
    a = '            case (state) is'
    assert t.count(a) == 1
    t = t.replace(a, '            if (state = CAPTURE1 or state = CAPTURE2) then\n               noiseFraction <= noiseFraction + noiseDelta;\n            end if;\n' + a)
    a = '                  case (CNT(9 downto 8)) is\n                     when "01" => noiseCnt <= noiseCnt + 84;\n                     when "10" => noiseCnt <= noiseCnt + 140;\n                     when "11" => noiseCnt <= noiseCnt + 180;\n                     when others => null;\n                  end case;'
    assert t.count(a) == 1
    t = t.replace(a, '                  -- noiseFraction uses the common small adder above the state case.')
    a = '                  if (noiseCnt(15 downto 0) >= 210) then\n                     noiseCnt <= noiseCnt + 16#20000# - 210;\n                  else\n                     noiseCnt <= noiseCnt + 16#10000#;\n                  end if;'
    assert t.count(a) == 1
    t = t.replace(a, '                  noisePhase <= noisePhase + resize(noisePhaseStep, 16);')
    a = '                  if (noiseCnt >= noiseCheck) then\n                     noiseCnt   <= noiseCnt and (noiseCheck - 1);'
    assert t.count(a) == 1
    t = t.replace(a, '                  noisePhase <= noisePhase and noiseKeepMask;\n                  if ((noisePhase and not noiseKeepMask) /= 0) then')
    block = '   -- Reset-only invariant: fraction <210 before CAPTURE1, <=389 afterwards.\n   noiseCnt <= noisePhase & "0000000" & noiseFraction;\n   noiseCheck <= shift_right(to_unsigned(16#8000#,16),to_integer(noiseShift_q)) & x"0000";\n   noiseKeepMask <= shift_right(to_unsigned(16#7FFF#,16),to_integer(noiseShift_q));\n   noisePhaseStep <= "10" when noiseFraction>=210 else "01";\n   noiseDelta <= to_unsigned(84,9) when state=CAPTURE1 and CNT(9 downto 8)="01" else\n                 to_unsigned(140,9) when state=CAPTURE1 and CNT(9 downto 8)="10" else\n                 to_unsigned(180,9) when state=CAPTURE1 and CNT(9 downto 8)="11" else\n                 to_unsigned(302,9) when state=CAPTURE2 and noiseFraction>=210 else\n                 to_unsigned(0,9);\n'
    assert t.count('\nbegin\n') == 1
    t = t.replace('\nbegin\n', '\nbegin\n' + block, 1)
    assert len(re.findall('(?m)^ +noiseCnt\\s*<=', t)) == 1
    assert len(re.findall('(?m)^ +noiseCheck\\s*<=', t)) == 1
    require(hashlib.sha256(t.encode()).hexdigest() == COMBINED_OUTPUT, 'Unexpected combined SPU output')
    return t

def prepare(directory, *, with_noise=False):
    from tool_paths import ROOT

    directory = Path(directory).resolve()
    require(directory.is_relative_to(ROOT / 'build') and not subprocess.run(
        ['git', 'check-ignore', '--quiet', str(directory / 'adsr-select/manifest.json')],
        cwd=ROOT).returncode, 'Ignored private build project required')
    require(not any((directory / n).exists() for n in
                    ['db', 'incremental_db', 'output_files', 'adsr-select']),
            'Prepare ADSR selector once, before compilation')
    require(json.loads((directory / 'manifest.json').read_text()).get('reset_only'),
            'Reset-only composition required')
    from measure_pocket_seeds import inputs
    from prepare_pocket_config_cdc import assignments
    from prepare_resource_targets import digest
    from prepare_spu_fir_width import HOLD, HOLD_SCOPE

    qsf = directory / 'apf_top.qsf'
    text = qsf.read_text()
    assigned = assignments(directory, text)
    def source(name):
        found = {p for kind, p in assigned if kind == 'VHDL_FILE' and p.name == name}
        require(len(found) == 1, 'One assigned ' + name + ' required')
        return found.pop()
    original, controller, top = source('spu.vhd'), source('savestates.vhd'), source('psx_top.vhd')
    require(digest(controller) == CONTROLLER and digest(top) == TOP,
            'Reviewed reset-only controller/top required: no SPU restoration')
    hold = directory / 'cpu-hold-margin/pocket_cpu_hold.sdc'
    require(('SDC_FILE', hold) in assigned and hold.is_file(), 'Assigned 150 ps hold SDC required')
    require(hashlib.sha256(hold.read_text().replace(str(directory), '<PROJECT>').encode()).hexdigest() == HOLD
            and digest(hold.with_name('scope.tcl')) in HOLD_SCOPE,
            'Exact retained 150 ps hold constraint/scope required')
    before = inputs(directory)
    protected = {str(p): digest(p) for p in [original, controller, top, hold, hold.with_name('scope.tcl')]}
    previous_qsf_sha256 = digest(qsf)
    output = (adsr_noise_text if with_noise else adsr_select_text)(original.read_text())
    token = '"' + str(original) + '"'
    require(text.count(token) == 1, 'One quoted absolute SPU assignment required')
    work = directory / 'adsr-select'
    changed = text.replace(token, '"' + str(work / 'spu.vhd') + '"', 1)
    require(before == inputs(directory) and digest(qsf) == previous_qsf_sha256
            and all(digest(Path(p)) == h for p, h in protected.items()),
            'Project changed during preparation')
    work.mkdir()
    (work / 'spu.vhd').write_text(output)
    qsf.write_text(changed)
    require(all(digest(Path(p)) == h for p, h in protected.items()), 'Original source/constraints changed')
    report = dict(original_inputs=before, protected_original_sha256=protected,
                  previous_qsf_sha256=previous_qsf_sha256, output_qsf_sha256=digest(qsf),
                  input_spu_sha256=EXPECTED, output_spu_sha256=COMBINED_OUTPUT if with_noise else OUTPUT,
                  compact_noise=with_noise,
                  preparer_sha256=digest(Path(__file__)), extra_cpu_hold_ns=0.15,
                  reset_only=True, arbitrary_emulator_restore_supported=False,
                  added_cycles=0, added_stages=0, extra_DSP_requested=0, extra_M10K_requested=0,
                  selected=False, fit_verified=False, strict_timing_verified=False, hardware_verified=False)
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--with-noise', action='store_true',
                        help='Reproduce separately qualified bounded noise combination; reset-only')
    args = parser.parse_args()
    print(json.dumps(prepare(args.project, with_noise=args.with_noise), indent=2))
