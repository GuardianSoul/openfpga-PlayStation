#!/usr/bin/env python3
"""Keep MiSTer's existing reverse-video sampling stages in flip-flops.

This is an inference change only: no RTL assignment, stage, clock, reset or
initial value changes. It does not make a sampled numeric bus coherent, repair
dot-clock pulse loss, or select any timing exception. Physical geometry and
full-top fit must be checked before selecting this option for hardware.
"""
import argparse
import json
from pathlib import Path
import re
import shutil

from prepare_pocket_config_cdc import assignments, scoped_hold_text, NATIVE_INIT_CLOCK_CUT
from prepare_resource_targets import digest, UPSTREAM


ENTITY_START='   -- POCKET_RETURN_FF_ENTITY_BEGIN\n'
ENTITY_END='   -- POCKET_RETURN_FF_ENTITY_END\n'
ARCH_START='   -- POCKET_RETURN_FF_ARCH_BEGIN\n'
ARCH_END='   -- POCKET_RETURN_FF_ARCH_END\n'
DECL='   attribute altera_attribute : string;\n'
RESTORE_DECL='   -- POCKET_RETURN_FF_RESTORE_ALTERA_DECL\n'
STAGES=['videoout_reports_s1','videoout_reports_s2','allowunpause_s1','allowunpause_s2',
        'videoout_request_s1','videoout_request_s2']
PORTS=['videoout_reports_1x','allowunpause1x','videoout_request_2x']


def strip_attributes(text):
    for start,end in [(ENTITY_START,ENTITY_END),(ARCH_START,ARCH_END)]:
        if text.count(start)!=1 or text.count(end)!=1:
            raise ValueError('Missing/duplicate reverse-video attribute block')
        a=text.index(start);b=text.index(end,a)+len(end)
        text=text[:a]+text[b:]
    if text.count(RESTORE_DECL)>1:
        raise ValueError('Duplicate relocated attribute declaration')
    return text.replace(RESTORE_DECL,DECL)


def adapt_videoout(text):
    if 'POCKET_RETURN_FF_' in text:
        raise ValueError('Reverse-video flip-flop mapping already present')
    for chain in [
        ['videoout_reports','videoout_reports_s1','videoout_reports_s2','videoout_reports_1x'],
        ['allowunpause','allowunpause_s1','allowunpause_s2','allowunpause1x'],
        ['videoout_request','videoout_request_s1','videoout_request_s2','videoout_request_2x']]:
        for source,target in zip(chain,chain[1:]):
            if len(re.findall(r'\b'+target+r'\s*<=\s*'+source+r'\s*;',text))!=1:
                raise ValueError('Unsupported reverse-video stage: '+source+' -> '+target)
    if text.count('end entity;')!=1 or text.count('architecture arch of gpu_videoout_async is')!=1:
        raise ValueError('Expected the assigned asynchronous video entity')
    # Output registers belong to the entity declarative scope in VHDL. The
    # intermediate record attributes belong to the architecture scope.
    # A forward-adapted architecture already declares altera_attribute. VHDL
    # does not permit redeclaring it across entity/architecture scopes. Move
    # only that declaration and retain an exact restoration marker.
    if text.count(DECL)>1:
        raise ValueError('Ambiguous altera_attribute declaration')
    result=text.replace(DECL,RESTORE_DECL)
    entity=ENTITY_START+DECL
    for name in PORTS:
        entity+=f'   attribute altera_attribute of {name} : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";\n'
    entity+=ENTITY_END
    result=result.replace('end entity;',entity+'end entity;',1)
    arch=ARCH_START
    # Both raw and forward-adapted stages now use the entity's declaration.
    for name in STAGES:
        arch+=f'   attribute altera_attribute of {name} : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";\n'
    arch+=ARCH_END
    anchor='begin \n\n   -- clk1x -> clkvid'
    if result.count(anchor)==1:
        result=result.replace(anchor,arch+anchor,1)
    else:
        # The ordered forward bridge inserts declarations immediately before
        # this architecture's begin. Identify its exact guarded marker.
        anchor='begin \n\n   -- Continuous ordered CPU-state snapshots; no settings/control coalescing.'
        if result.count(anchor)!=1:
            raise ValueError('Unsupported video architecture begin')
        result=result.replace(anchor,arch+anchor,1)
    if strip_attributes(result)!=text:
        raise ValueError('Reverse-video preparation changed functional RTL')
    return result


def prepare_project(directory):
    directory=Path(directory).resolve();qsf=directory/'apf_top.qsf';sdc=directory/'pocket.sdc'
    q=qsf.read_text();s=sdc.read_text();scoped,removed=scoped_hold_text(s)
    assigned=assignments(directory,q)
    core=directory/'core_top.sv'
    if [p for kind,p in assigned if kind=='SYSTEMVERILOG_FILE' and p.name=='core_top.sv']!=[core]:
        raise ValueError('Expected the canonical copied core_top.sv exactly once')
    choices=[p for kind,p in assigned if kind=='VHDL_FILE' and
             re.search(r'^\s*entity\s+gpu_videoout_async\s+is\b',p.read_text(),re.M|re.I)]
    if len(choices)!=1:raise ValueError('Expected one assigned asynchronous GPU video engine')
    original=choices[0];sha=digest(original);before=original.read_text();after=adapt_videoout(before)
    work=directory/'video-return-ff'
    if work.exists():raise ValueError('Reverse-video mapping directory already exists; select a fresh parent')
    token='"'+str(original)+'"'
    if q.count(token)!=1:raise ValueError('Expected one quoted selected video assignment')
    work.mkdir();output=work/'gpu_videoout_async.vhd';output.write_text(after)
    shutil.copyfile(UPSTREAM/'LICENSE',work/'COPYING.GPL2')
    if digest(original)!=sha:raise ValueError('Selected video source changed while preparing')
    qsf.write_text(q.replace(token,'"'+str(output)+'"',1));sdc.write_text(scoped)
    report=dict(original=str(original),original_sha256=sha,path=str(output),sha256=digest(output),
        preparer_sha256=digest(Path(__file__)),attribute_only=True,
        functional_source_identity_verified=strip_attributes(output.read_text())==before,
        intermediate_stages=STAGES,output_stages=PORTS,
        removed_exact_family_hold_cuts=[x for x in removed if x!=NATIVE_INIT_CLOCK_CUT],
        removed_exact_native_init_clock_cuts=[x for x in removed if x==NATIVE_INIT_CLOCK_CUT],
        constraints_added=False,stage_latency_changed=False,
        fit_verified=False,timing_verified=False,hardware_verified=False,
        limitations=['Numeric bus coherence and dotclock pulse delivery are not established by flip-flop mapping',
                     'No reverse first-stage timing exception is added; those paths remain visible',
                     'Quartus must actually map all live stages to FFs; attribute acceptance is not proof',
                     'Measure full-top LAB/BRAM cost and all-corner timing before hardware selection'])
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path)
    print(json.dumps(prepare_project(p.parse_args().project),indent=2))
