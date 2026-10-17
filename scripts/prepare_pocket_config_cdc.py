#!/usr/bin/env python3
"""Wire the existing coherent host/video bridges into a copied Pocket project.

Runs after the early builder has finished core_top.sv. Keeps that canonical
file assigned, so a later copied build cannot silently edit an unused top.
Only exact known early-builder clock exceptions are removed (family hold cuts
and the Native init-to-host-clock cut); unknown exceptions are not rewritten.
All related CPU clocks stay timed.
"""
import argparse
import json
from pathlib import Path
import re
import shutil

from prepare_host_config_cdc import prepare as prepare_host
from prepare_video_config_cdc import prepare as prepare_video
from prepare_resource_targets import digest
from tool_paths import ROOT


ASSIGNMENT = re.compile(
    r'^set_global_assignment -name (SYSTEMVERILOG_FILE|VERILOG_FILE|VHDL_FILE|SDC_FILE) '
    r'(?:"([^"]+)"|(\S+))[^\n]*$', re.M)
NATIVE_INIT_CLOCK_CUT='set_false_path -from [get_registers {*|native_init_count[*]}] -to [get_clocks {clk_74a}]'


def assignments(directory, text):
    result = []
    for m in ASSIGNMENT.finditer(text):
        p = Path(m[2] or m[3])
        result.append((m[1], (p if p.is_absolute() else directory/p).resolve()))
    return result


def scoped_hold_text(text):
    families = ['ic|cpu_pll|*', 'ic|memory_clocks|*', 'ic|video_pll|*',
                'ic|audio_pll|*', 'clk_74a', 'clk_74b', 'bridge_spiclk']
    known = {f'set_false_path -hold -from [get_clocks {{{a}}}] -to [get_clocks {{{b}}}]'
             for a in families for b in families if a != b}
    # Older Native preparation also added a setup/hold cut to the entire host
    # domain. Remove that known line; it does not prove safe diagnostic/ready CDC.
    known.add(NATIVE_INIT_CLOCK_CUT)
    kept, removed = [], []
    for line in text.splitlines(keepends=True):
        if line.rstrip('\r\n') in known:
            removed.append(line.rstrip('\r\n'))
        elif re.search(r'^\s*set_false_path\b.*\bget_clocks\b', line) or re.search(
                r'^\s*set_clock_groups\b', line):
            raise ValueError('Unknown clock-level exception; use a parent with reviewed scoped constraints')
        else:
            kept.append(line)
    return ''.join(kept), removed


def adapt_apf_blank(text):
    # psx_apf_video, or psx_apf_video_wide (--wide-lines), whose line buffer
    # takes the same raw blank on its write side.
    video_inst='psx_apf_video_wide #(' if 'psx_apf_video_wide #(' in text else 'psx_apf_video #('
    if text.count(video_inst)!=1 or text.count('.blank(cpu_boot_reset)')!=1:
        raise ValueError('Expected one APF video instance with raw CPU boot-reset blanking')
    if text.count('psx_boot_gate boot_gate (')!=1 or 'apf_blank_cdc' in text:
        raise ValueError('Expected one unadapted CPU boot gate for APF blanking')
    text=text.replace('.blank(cpu_boot_reset)', '.blank(apf_blank_video)', 1)
    at=text.rfind('\n',0,text.index(video_inst))+1
    return text[:at]+'''    wire apf_blank_video;
    psx_video_blank_cdc apf_blank_cdc (
        .video_clk(video_rgb_clock),.blank_async(cpu_boot_reset),
        .blank_video(apf_blank_video));
'''+text[at:]


def prepare_project(directory, *, host=False, video=False, blank=False):
    if not host and not video and not blank:
        raise ValueError('Select host, video configuration and/or APF blank CDC')
    directory = Path(directory).resolve()
    qsf = directory/'apf_top.qsf'; core = directory/'core_top.sv'; sdc = directory/'pocket.sdc'
    q = qsf.read_text(); saved_sdc = sdc.read_text()
    scoped_sdc, removed = scoped_hold_text(saved_sdc)
    assigned = assignments(directory, q)
    tops = [p for kind,p in assigned if kind=='SYSTEMVERILOG_FILE' and p.name=='core_top.sv']
    if tops != [core]:
        raise ValueError('Expected the canonical copied core_top.sv to be assigned exactly once')
    original_core_sha = digest(core)
    work = directory/'config-cdc'
    if work.exists():
        raise ValueError('Configuration CDC output already exists; choose a fresh unadapted parent')
    # Reject duplicate channels before creating files or changing the project.
    if host and 'psx_config_cdc #(' in core.read_text():
        raise ValueError('Host configuration channel already present')
    selected_video = None
    if video:
        candidates = [p for kind,p in assigned if kind=='VHDL_FILE' and
                      re.search(r'^\s*entity\s+gpu_videoout_async\s+is\b', p.read_text(), re.M | re.I)]
        if len(candidates) != 1:
            raise ValueError('Expected one assigned asynchronous GPU video engine')
        selected_video = candidates[0]
        if 'component psx_video_config_cdc is' in selected_video.read_text():
            raise ValueError('Ordered video configuration channel already present')
    # Check this source transform before creating files or changing assignments.
    formatter=None
    if blank:
        adapt_apf_blank(core.read_text())
        formatters=[p for kind,p in assigned if kind=='SYSTEMVERILOG_FILE' and
                    re.search(r'^module\s+psx_apf_video\b',p.read_text(),re.M)]
        if len(formatters)!=1 or len(re.findall(r"rgb<=active && !blank (?:&& !\(BLANK_TOP!=0 && top_line\) )?\? rgb_in : 24'h0;",formatters[0].read_text()))!=1:
            raise ValueError('APF blank CDC requires the raw-blank formatter; do not stack internal and external synchronizers')
        formatter=formatters[0]
    work.mkdir()
    before = dict(qsf_sha256=digest(qsf), core_sha256=original_core_sha, sdc_sha256=digest(sdc))
    report = dict(host_enabled=host, video_enabled=video, apf_blank_enabled=blank, before=before,
                  removed_exact_family_hold_cuts=[s for s in removed if s!=NATIVE_INIT_CLOCK_CUT],
                  removed_exact_native_init_clock_cuts=[s for s in removed if s==NATIVE_INIT_CLOCK_CUT], added=[],
                  preparer_sha256=digest(Path(__file__)),
                  opt_in=True, fit_verified=False, timing_verified=False,
                  game_verified=False, hardware_verified=False,
                  related_cpu_clocks_still_timed=True,
                  limitations=['Forward video packet adds delivery/startup latency; no cycle-equivalence claim',
                               'Reverse video report, diagnostic readback and APF reset crossings are separate',
                               'Configuration current must not gate runtime guest reset',
                               'Require actual clock/Gray/reset geometry and all-corner audits before selection'])
    new_core = None

    def add(path, kind):
        nonlocal q
        path = Path(path)
        matches = [(k,p) for k,p in assignments(directory,q) if p.name==path.name]
        if matches:
            if len(matches)!=1 or matches[0][0]!=kind or digest(matches[0][1])!=digest(path):
                raise ValueError('Conflicting existing dependency: '+path.name)
            report['added'].append(dict(path=str(matches[0][1]),sha256=digest(path),reused=True))
        else:
            suffix = '' if kind=='SDC_FILE' else ' -library work'
            q += f'\nset_global_assignment -name {kind} "{path}"{suffix}\n'
            report['added'].append(dict(path=str(path),sha256=digest(path),reused=False))

    if host:
        h = prepare_host(core, work/'host', original_core_sha)
        report['host'] = h
        new_core = Path(h['top']['path']).read_text()
        module = next(Path(s['path']) for s in h['added'] if s['path'].endswith('.sv'))
        source = next(Path(s['path']) for s in h['added'] if s['path'].endswith('.tcl'))
        effective = work/'host.sdc';shutil.copyfile(source,effective)
        add(module,'SYSTEMVERILOG_FILE');add(effective,'SDC_FILE')
    if video:
        v = prepare_video(selected_video,work/'video',digest(selected_video))
        report['video'] = v
        token = '"'+str(selected_video)+'"'
        if q.count(token)!=1:
            raise ValueError('Expected one quoted selected video assignment')
        q = q.replace(token,'"'+v['path']+'"')
        module = next(Path(s['path']) for s in v['added'] if s['path'].endswith('.sv'))
        source = next(Path(s['path']) for s in v['added'] if s['path'].endswith('.tcl'))
        effective = work/'video.sdc';shutil.copyfile(source,effective)
        add(module,'SYSTEMVERILOG_FILE');add(effective,'SDC_FILE')
        for dependency in v['dependencies']:
            add(dependency['path'],'SYSTEMVERILOG_FILE')
    if blank:
        new_core=adapt_apf_blank(new_core if new_core is not None else core.read_text())
        copies=[]
        for original,kind,name in [
            (ROOT/'rtl/pocket/psx_video_blank_cdc.sv','SYSTEMVERILOG_FILE','psx_video_blank_cdc.sv'),
            (ROOT/'scripts/pocket_video_blank_scope.tcl','SDC_FILE','blank.sdc')]:
            copied=work/name;sha=digest(original);shutil.copyfile(original,copied)
            if digest(copied)!=sha:
                raise ValueError('APF blank input changed while copying')
            add(copied,kind)
            copies.append(dict(path=str(copied),sha256=sha,original=str(original)))
        report['apf_blank']=dict(added=copies,video_stages=3,initialized_blank=True,
            free_running=True,bridge_latency_video_periods=[2,3],apf_output_extra_periods=1,
            metadata_unchanged=True,formatter=dict(path=str(formatter),sha256=digest(formatter)),hardware_verified=False)
        report['limitations']=[s.replace('and APF reset crossings are separate',
            'and other host crossings are separate') for s in report['limitations']]
        report['limitations'].append('APF blank assertion/release is delayed by the video synchronizer; raw reset is not used by RGB')
    # The known family hold cuts must not suppress payload/Gray skew checks.
    # All source transforms and dependency guards have completed before these writes.
    if digest(core)!=original_core_sha or digest(sdc)!=before['sdc_sha256'] or digest(qsf)!=before['qsf_sha256']:
        raise ValueError('Copied project changed while preparing configuration CDC')
    if new_core is not None:
        core.write_text(new_core)
    sdc.write_text(scoped_sdc);qsf.write_text(q)
    report['after'] = dict(qsf_sha256=digest(qsf),core_sha256=digest(core),sdc_sha256=digest(sdc))
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project',type=Path);p.add_argument('--host',action='store_true');p.add_argument('--video',action='store_true');p.add_argument('--blank',action='store_true')
    a = p.parse_args();print(json.dumps(prepare_project(a.project,host=a.host,video=a.video,blank=a.blank),indent=2))
