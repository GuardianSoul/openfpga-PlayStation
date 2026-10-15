"""Source-bound CPU tag experiment for a private, reset-only Pocket build."""
import json
from pathlib import Path
import shutil

from build_pocket import source
from prepare_resource_targets import digest
from run_cpu_cache_contract import observed_cpu, tag_trial
from tool_paths import ROOT


def validate_contract(path):
    path=Path(path).resolve()
    report=json.loads(path.read_text())
    keys=('passed','fault_coverage_complete','falling_edge_cycle_equivalence_verified',
          'registered_address_model_verified','all_sources_unchanged')
    if not all(report.get(key) for key in keys):
        raise ValueError('complete CPU cache/phase contract proof required')
    if any(digest(Path(p))!=expected for p,expected in report['source_sha256'].items()):
        raise ValueError('CPU contract input changed since testing')
    for group in ('runs','falling_trial','address_model_trial'):
        if len(report[group])<4 or not all(row['passed'] for row in report[group]):
            raise ValueError('incomplete cached CPU contract profiles')
    if len(report['fault_rejections'])!=4 or not all(row['rejected'] for row in report['fault_rejections']):
        raise ValueError('CPU contract fault coverage missing')
    return path,report


def apply_cpu_tag_trial(manifest,directory,contract_path):
    directory=Path(directory).resolve()
    if (manifest.get('kind')!='physical-psx-budget' or not manifest.get('reset_only')
        or not manifest.get('variant_flags',{}).get('no-turbo-cache') or 'cpu_tag_trial' in manifest):
        raise ValueError('private CPU tag trial requires fresh reset-only/no-turbo Pocket build')
    fixed=manifest['system_manifest']['targets']['psx_top']['fixed_ports']
    if any(fixed.get(name)!="'0'" for name in ('TURBO_COMP','TURBO_MEM','TURBO_CACHE','TURBO_CACHE50')):
        raise ValueError('CPU trial needs all turbo controls fixed off')
    if any(digest(Path(s['path']))!=s['sha256'] for s in manifest['sources']):
        raise ValueError('CPU preparation source identity changed')
    if any(digest(directory/name)!=sha for name,sha in manifest['project_hashes'].items()):
        raise ValueError('CPU preparation project identity changed')
    contract_path,contract=validate_contract(contract_path)
    selected=[s for s in manifest['sources'] if Path(s['path']).name=='cpu.vhd']
    if len(selected)!=1: raise ValueError('expected one selected CPU source')
    previous=selected[0]; text=Path(previous['path']).read_text()
    tested=Path(contract['directory'])
    if observed_cpu(text)!=(tested/'candidate-original.vhd').read_text():
        raise ValueError('prepared CPU differs from the tested baseline')
    transformed=tag_trial(text,'falling')
    if observed_cpu(transformed)!=(tested/'candidate-falling.vhd').read_text():
        raise ValueError('private CPU transform differs from the tested trial')
    output=directory/'cpu-tag-trial'; output.mkdir(exist_ok=False)
    path=output/'cpu.vhd'; path.write_text(transformed)
    shutil.copy2(ROOT/'references/PSX_MiSTer/LICENSE',output/'COPYING.GPL2')
    replacement=source(path); replacement['assignment']='VHDL_FILE'
    qsf=directory/'apf_top.qsf'; qtext=qsf.read_text()
    if qtext.count(previous['path'])!=1: raise ValueError('expected one CPU QSF assignment')
    qsf.write_text(qtext.replace(previous['path'],str(path)))
    manifest['sources']=[replacement if s is previous else s for s in manifest['sources']]
    manifest['project_hashes']['apf_top.qsf']=digest(qsf)
    manifest['cpu_tag_trial']={'original':previous,'replacement':replacement,
        'contract_report':str(contract_path),'contract_report_sha256':digest(contract_path),
        'baseline_observer_expansion_exact':True,'trial_observer_expansion_exact':True,
        'preparer_sha256':digest(Path(__file__)),
        'transform_source_sha256':digest(ROOT/'scripts/run_cpu_cache_contract.py'),
        'added_CPU_cycles':0,'production_variant_selected':False,
        'full_top_fit_verified':False,'full_top_timing_verified':False,'game_verified':False,'hardware_verified':False}
    manifest['limitations'].append('Private CPU tag RAM trial: cached contract passes; full-top half-cycle timing and game coverage pending')
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
