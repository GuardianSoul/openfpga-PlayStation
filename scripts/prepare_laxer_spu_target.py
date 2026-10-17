"""Frozen capacity probe of the pinned Laxer SPU; never a core replacement.

Expose every public port. External SPU RAM and the simulation SPU_IF wrapper
are excluded; the voice, ADSR, interpolation, reverb and register logic stay.
The source omits sweeps and assumes fixed memory latency. A small fit would
not establish feature parity, output accuracy or a usable Pocket interface.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess

from prepare_resource_targets import digest, write_project
from tool_paths import ROOT

UPSTREAM = ROOT / 'references/Laxer3a_PSX'
PIN = 'b46c685064f6c3d156ff7a5a2cb5a5163c914c91'
TOP = 'psx_laxer_spu_resource'
PORTS = (
    ('input', 1, 'i_clk'), ('input', 1, 'n_rst'),
    ('input', 1, 'SPUCS'), ('input', 1, 'SRD'), ('input', 1, 'SWRO'),
    ('input', 10, 'addr'), ('input', 16, 'dataIn'),
    ('output', 16, 'dataOut'), ('output', 1, 'dataOutValid'),
    ('output', 1, 'SPUINT'), ('output', 1, 'SPUDREQ'), ('input', 1, 'SPUDACK'),
    ('output', 18, 'o_adrRAM'), ('output', 1, 'o_dataReadRAM'),
    ('output', 1, 'o_dataWriteRAM'), ('input', 16, 'i_dataInRAM'),
    ('output', 16, 'o_dataOutRAM'), ('input', 16, 'CDRomInL'),
    ('input', 16, 'CDRomInR'), ('input', 1, 'inputL'), ('input', 1, 'inputR'),
    ('output', 16, 'AOUTL'), ('output', 16, 'AOUTR'), ('output', 1, 'VALIDOUT'),
)
EXCLUDED = {'SPU_RAM.v', 'SPU_IF.v'}


def wrapper(text):
    clean = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
    header = clean[clean.index('module SPU('):].split(');', 1)[0]
    found = []
    for direction, width, name in re.findall(
            r'\b(input|output)\s+(?:signed\s+)?(\[\s*\d+\s*:\s*\d+\s*\]\s+)?(\w+)', header):
        bits = re.findall(r'\d+', width)
        found.append((direction, int(bits[0]) - int(bits[1]) + 1 if bits else 1, name))
    if tuple(found) != PORTS:
        raise ValueError('Pinned SPU public port contract differs')
    ports = ',\n'.join('    ' + direction + ' wire ' +
                       (f'[{bits-1}:0] ' if bits > 1 else '') + name
                       for direction, bits, name in PORTS)
    bindings = ',\n'.join(f'        .{name}({name})' for _, _, name in PORTS)
    return ('// GPL-2.0 component probe. All upstream ports remain live.\n'
            '// No fixed controls, RAM controller, Pocket integration or audio proof.\n'
            f'module {TOP}(\n{ports}\n);\n'
            f'    SPU component_core(\n{bindings}\n    );\nendmodule\n')


def prepare(directory):
    directory = Path(directory).resolve()
    if directory.exists() or (directory.parent / 'manifest.json').exists():
        raise ValueError('Fresh SPU measurement directory required')
    if (not directory.is_relative_to(ROOT / 'build') or
            subprocess.run(['git', 'check-ignore', '--quiet',
                            str(directory / 'manifest.json')], cwd=ROOT).returncode):
        raise ValueError('Ignored build directory required')
    actual = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != PIN or subprocess.run(['git', '-C', str(UPSTREAM), 'diff', '--quiet', 'HEAD']).returncode:
        raise ValueError('Laxer SPU reference differs from recorded pin')
    rtl = UPSTREAM / 'hdlSPU/rtl'
    files = sorted(p for p in rtl.iterdir() if p.suffix in ('.v', '.sv') and p.name not in EXCLUDED)
    text = wrapper((rtl / 'SPU.sv').read_text())
    # Read the exact Git object too: untracked HDL must never enter the probe.
    raw = {}
    for p in files:
        committed = subprocess.check_output(['git', '-C', str(UPSTREAM), 'show',
                                            PIN + ':' + p.relative_to(UPSTREAM).as_posix()])
        if p.read_bytes() != committed:
            raise ValueError('SPU source differs from pinned Git object: ' + p.name)
        raw[p] = committed
    directory.mkdir(parents=True)
    copied = directory / 'sources'; copied.mkdir()
    sources = []
    for p, content in raw.items():
        out = copied / p.name; out.write_bytes(content)
        sources.append(dict(path=str(out), sha256=digest(out), library='work',
                            assignment='MISC_FILE' if p.name == 'spu_def.sv' else 'SYSTEMVERILOG_FILE',
                            original=str(p), original_sha256=digest(p)))
    generated = directory / (TOP + '.sv'); generated.write_text(text)
    sources.append(dict(path=str(generated), sha256=digest(generated), library='work',
                        assignment='SYSTEMVERILOG_FILE'))
    external = [(name, 'in' if direction == 'input' else 'out',
                 f'std_logic_vector({bits-1} downto 0)' if bits > 1 else 'std_logic')
                for direction, bits, name in PORTS]
    project = write_project(directory, TOP, external, ('i_clk',), sources,
                            ['# Standalone internal clock only; no Pocket I/O timing proof.',
                             'create_clock -name spu_clk1x -period 29.5251 [get_ports i_clk]',
                             'derive_clock_uncertainty'])
    qsf = directory / (TOP + '.qsf')
    with qsf.open('a') as f:
        f.write(f'set_global_assignment -name SEARCH_PATH "{copied}"\n')
    project['.qsf'] = digest(qsf)
    license_path = UPSTREAM / 'LICENSE'
    if license_path.is_file():
        shutil.copyfile(license_path, directory / 'COPYING.upstream')
    target = dict(top=TOP, directory=str(directory), quartus_sources=sources,
                  project_sha256=project, fixed_ports={}, physical_clock_ports=['i_clk'],
                  included_components=['Pinned Laxer voice/ADSR/interpolator/reverb/register engine'],
                  components_connected=False, public_ports=[list(p) for p in PORTS],
                  limitations=['Capacity only; no functional execution or replacement selected',
                               'Upstream explicitly omits voice/main volume sweeps and some register behavior',
                               'Fixed-latency external RAM; no memory ready or clock-enable input',
                               'No SPU_RAM/SPU_IF simulation wrapper, physical memory or Pocket platform',
                               'All non-clock ports virtual; no interface or board timing qualification'])
    manifest = dict(device='5CEBA4F23C8', upstream_pin=PIN, full_core=False,
                    hardware_deployable=False, sources_byte_identical_to_pin=True,
                    excluded_files=sorted(EXCLUDED), targets={'laxer_spu': target})
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
