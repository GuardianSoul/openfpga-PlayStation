#!/usr/bin/env python3
"""Optional same-edge memorymux context RAM; costly measured combination only.

Source-bound to the reviewed reset-only memorymux/CPU/FIFO composition.
No default, clock, constraint, transport or added-cycle change. Undefined
startup payloads can become zero; defined values and capture edges are held.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import UPSTREAM, digest
from tool_paths import ROOT

EXPECTED = '221d60d1e92fe0aaaff5a33b09049dc90114631777d00b53916af1c33a55ef7e'
SOURCES = {
    'memorymux.vhd': EXPECTED,
    'cpu.vhd': '79924b13b8407acc54b9a3c6cf1b62d718d02fee46c659b48e983ec86e94d67c',
    'psx_wfifo_m10k.vhd': '9b2cd2f643ebdf91e752bd307a978ea9b675063917c3e74da4d071dabeae919c',
}
TEMPLATE_SHA = 'eeb1768ee1a26667685a5a085fc331dbac4135c310161dcc4bdc508e422c9712'
OUTPUT_SHA = '9e3bcd6f14150d1f8b5035b5ab275aa57c9e0fbf01b27a17643cf7e16c9dd98d'


def require(ok, message):
    if not ok:
        raise ValueError(message)


GROUPS={
 'pending':[
 ('mem_save_rnw',1,'std_logic',"'1'"),('mem_save_isData',1,'std_logic','mem_in_isData'),('mem_save_isCache',1,'std_logic','mem_in_isCache'),('mem_save_oldtagvalids',4,'std_logic_vector','mem_in_oldtagvalids'),('mem_save_addressInstr',32,'unsigned','mem_in_addressInstr'),('mem_save_addressData',32,'unsigned','mem_in_addressData'),('mem_save_reqsize',2,'unsigned','mem_in_reqsize'),('mem_save_writeMask',4,'std_logic_vector','mem_in_writeMask'),('mem_save_dataWrite',32,'std_logic_vector','mem_in_dataWrite')],
 'active':[
 ('addressData_buf',32,'unsigned','mem_addressData'),('dataWrite_buf',32,'std_logic_vector','mem_dataWrite'),('reqsize_buf',2,'unsigned','mem_reqsize'),('writeMask_buf',4,'std_logic_vector','mem_writeMask')],
 'external':[
 ('ext_dataWrite_buf',32,'std_logic_vector','dataWrite_buf'),('ext_writeMask_buf',4,'std_logic_vector','writeMask_buf')],
 'bios':[('addressBIOS_buf',19,'unsigned','ctx_bios_value')]
}
def memorymux_text(s):
 require(hashlib.sha256(s.encode()).hexdigest() == EXPECTED, 'Unreviewed memorymux context source')
 decl='''   component psx_word_hold is generic(width:positive:=32);
      port(clk,we:in std_logic;d:in std_logic_vector(width-1 downto 0);q:out std_logic_vector(width-1 downto 0));
   end component;
   signal ctx_bios_value:unsigned(18 downto 0);
''';body=''
 for group,fields in GROUPS.items():
  width=sum(x[1]for x in fields);decl+=f'   signal ctx_{group}_d,ctx_{group}_q:std_logic_vector({width-1} downto 0);\n   signal ctx_{group}_we:std_logic;\n'
  for n,w,kind,value in fields:
   # Remove initializers from newly RAM-driven aliases, avoiding a second
   # GHDL driver. Initial hardware contents remain zero where specified.
   pattern=r'(?m)^(\s*signal '+n+r'\s*:[^;\n]*?)(?:\s*:=\s*[^;\n]+)?;'
   matches=list(re.finditer(pattern,s));require(len(matches) == 1, 'Changed declaration: ' + n)
   old=matches[0][0];new=re.sub(r'\s*:=\s*[^;]+','',old);s=s[:matches[0].start()]+new+s[matches[0].end():]
   pat=r'(?m)^\s*'+n+r'\s*<=\s*[^;]+;'
   found=re.findall(pat,s);require(len(found) == (2 if group == 'bios' else 1), 'Changed capture: ' + n)
   s=re.sub(pat,'',s)
  if group=='bios':
   cond="reset='0' and ce='1' and state=IDLE and pauseNext='0' and ((readram='0' and writeram='0')or ram_done='1') and ((mem_request='1' and writeFifo_busy='0')or writeFifo_Empty='0') and loadExe_latched='0'"
   # Preserve the nested data/instruction distinction, and the read-only
   # condition for data BIOS. All original address-region predicates retained.
   body+="   ctx_bios_value<=mem_addressInstr(18 downto 0) when mem_isData='0' else mem_addressData(18 downto 0);\n"
   cond+=" and ((mem_isData='0' and mem_addressInstr(28 downto 0)>=16#1FC00000# and mem_addressInstr(28 downto 0)<16#1FC80000#)or(mem_isData='1' and mem_rnw='1' and mem_addressData(28 downto 0)>=16#1FC00000# and mem_addressData(28 downto 0)<16#1FC80000#))"
  else:cond={'pending':"reset='0' and ce='1' and mem_in_request='1' and mem_in_rnw='1'",'active':"reset='0' and ce='1' and state=IDLE",'external':"reset='0' and ce='1' and ext_state=EXT_IDLE"}[group]
  body+=f"   ctx_{group}_we<='1' when {cond} else '0';\n"
  pack=' & '.join('std_logic_vector('+val+')'if kind=='unsigned'else val for n,w,kind,val in fields)
  # A one-field BIOS packet is already an unsigned word, converted above.
  body+=f'   ctx_{group}_d<='+pack+';\n'
  body+=f'   context_{group}:psx_word_hold generic map(width=>{width}) port map(clk=>clk1x,we=>ctx_{group}_we,d=>ctx_{group}_d,q=>ctx_{group}_q);\n'
  hi=width-1
  for n,w,kind,_ in fields:
   part=f'ctx_{group}_q({hi})'if w==1 else f'ctx_{group}_q({hi} downto {hi-w+1})';expr='unsigned('+part+')'if kind=='unsigned'else part;body+=f'   {n}<='+expr+';\n';hi-=w
  require(hi == -1, 'Context packing width changed')
 a=list(re.finditer(r'(?m)^begin[ \t]*\n',s));require(len(a) == 1, 'Changed architecture boundary');i=a[0].start();return s[:i]+decl+s[i:a[0].end()]+body+s[a[0].end():]


def prepare(directory):
    directory = Path(directory).resolve()
    require(directory.is_relative_to(ROOT / 'build') and not subprocess.run(
        ['git', 'check-ignore', '--quiet', str(directory / 'memorymux-context-ram/manifest.json')],
        cwd=ROOT).returncode, 'Ignored build project required')
    require(not any((directory / n).exists() for n in
                    ('db', 'incremental_db', 'output_files', 'memorymux-context-ram')),
            'Prepare contexts once, before synthesis')
    require(json.loads((directory / 'manifest.json').read_text()).get('reset_only'),
            'Reviewed contexts require reset-only configuration')
    hold = directory / 'cpu-hold-margin/pocket_cpu_hold.sdc'
    require(hold.is_file() and 'set pocket_cpu_hold_ns 0.15' in hold.read_text(),
            'Measured contexts require the retained 150 ps CPU hold target')
    qsf = directory / 'apf_top.qsf'
    before_qsf = digest(qsf)
    qsf_text = qsf.read_text()
    assigned = assignments(directory, qsf_text)
    selected = {}
    for name, sha in SOURCES.items():
        paths = {p for kind, p in assigned if kind == 'VHDL_FILE' and p.name == name}
        require(len(paths) == 1, 'Missing or duplicate context dependency: ' + name)
        path = paths.pop()
        require(digest(path) == sha, 'Unreviewed context dependency: ' + name)
        selected[name] = path
    template = ROOT / 'rtl/pocket/psx_word_hold.sv'
    require(not any(p.name == template.name for _, p in assigned), 'Context RAM already assigned')
    require(digest(template) == TEMPLATE_SHA, 'Unreviewed context RAM template')
    protected = {str(p): digest(p) for p in (*selected.values(), template)}
    original = inputs(directory)
    work = directory / 'memorymux-context-ram'
    body = memorymux_text(selected['memorymux.vhd'].read_text())
    require(hashlib.sha256(body.encode()).hexdigest() == OUTPUT_SHA,
            'Context output differs from measured implementation')
    token = '"' + str(selected['memorymux.vhd']) + '"'
    require(qsf_text.count(token) == 1, 'Expected one quoted memorymux pointer')
    replacement = qsf_text.replace(token, '"' + str(work / 'memorymux.vhd') + '"', 1)
    require(digest(qsf) == before_qsf and all(digest(Path(p)) == h for p, h in protected.items())
            and all(digest(Path(r['path'])) == r['sha256'] for r in original),
            'Project changed while preparing contexts')
    work.mkdir()
    (work / 'memorymux.vhd').write_text(body)
    shutil.copyfile(template, work / template.name)
    shutil.copyfile(UPSTREAM / 'LICENSE', work / 'COPYING.GPL2')
    replacement += f'\nset_global_assignment -name SYSTEMVERILOG_FILE "{work / template.name}" -library work\n'
    qsf.write_text(replacement)
    report = dict(original_inputs=original, sources=protected,
                  selected_sources={n: digest(work / n) for n in ('memorymux.vhd', template.name)},
                  preparer_sha256=digest(Path(__file__)), context_widths=[109, 70, 36, 19],
                  added_clocks_or_cycles=0, clock_and_constraint_changes=False,
                  retained_through_reset=True, undefined_startup_payload_may_differ=True,
                  generic_full_port_equivalence=False, production_selected=False,
                  fit_verified=False, timing_verified=False, hardware_verified=False)
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    print(json.dumps(prepare(parser.parse_args().project), indent=2))
