#!/usr/bin/env python3
"""Bound the video-out engine's synchroniser first stages instead of waiving them.

The asynchronous video-out engine (gpu_videoout_async) hands two records from
the APF video clock to the CPU clocks through plain register chains
(*_s1 -> *_s2 -> output). release_timing.py timed the first stage against the
edge-to-edge relationship of the two unrelated clocks and waived the miss with
a floor of -6.0 ns (support/release_timing_waivers.json, "*video*" -> clk1x /
clk2x). The floor is arbitrary: candidate D2 seed 12 had every such path
inside it except one at -6.037 ns. This step replaces the waiver, for exactly
the endpoints below, by a requirement that is checked.

Covered endpoints (all direct register -> register, no logic; coherency in
docs/save-states-finish/video-crossing-constraint.md):

  into clk1x  videoout_reports_s1.<field>   every field is one bit
              allowunpause_s1               one bit
  into clk2x  videoout_request_s1.fetch          1 bit
              videoout_request_s1.fetchsize[9:0] quasi-static (display width)
              videoout_request_s1.lineInNext[8:0] changes once per line; the
                  clk2x side (gpu_videoout WAITNEWLINE -> WAITREQUEST) takes
                  it four clk2x after it first sees a difference

Not covered, on purpose: videoout_request_s1.xpos and .lineDisp (xpos moves on
every dot; the clk2x side reads neither, so synthesis removes them; if a later
graph keeps them they stay under the old waiver and its floor).

What the SDC block does (Quartus 25.1 Standard/Lite has no set_data_delay and
no set_max_delay -datapath_only):

  set_max_delay  10 -to <covered>   setup analysis with a 10 ns requirement in
                                    place of the clock edge relationship
  set_min_delay -10 -to <covered>   hold analysis released to the same window

TimeQuest's max/min delay is relative to the clock network delays, so the
analysed quantity is  launch clock arrival + tco + data delay + tsu - latch
clock arrival <= 10 ns  (and >= -10 ns), not the bare data delay. The sign-off
(run_frozen_pocket_fit.py) therefore also reads every covered path's Data
Delay column at the four corners and fails one above the bound. Not enforced:
bus skew as its own number (the +-10 ns window bounds it to 20 ns, the clk2x
side waits 59 ns), and nothing about metastability beyond the two flops.

Usage:
  prepare_video_sync_constraints.py PROJECT --output COPY [--seed N]
      COPY = a snapshot of the frozen PROJECT with the block in its pocket.sdc and a
      fit-inputs.json that closes the new inputs (only pocket.sdc may differ)
  prepare_video_sync_constraints.py --print            the SDC block (for a sign-off "extra_sdc")
The compiled gpu_videoout_async.vhd / gpu_videoout.vhd / pGPU.vhd must still
have the chains, the four-cycle wait and one-bit report fields (guard()).
"""
import argparse
import json
from pathlib import Path
import re

BOUND_NS = 10.0
BEGIN, END = '# POCKET_VIDEO_SYNC_BEGIN', '# POCKET_VIDEO_SYNC_END'
SCOPE = '*|gpu_videoout_async:igpu_videoout_async|'
# trailing * : the bus bits and any ~DUPLICATE copy of a first stage
COVERED = ('videoout_reports_s1.*', 'allowunpause_s1*',
           'videoout_request_s1.fetch*', 'videoout_request_s1.lineInNext*')
NOT_COVERED = ('videoout_request_s1.xpos*', 'videoout_request_s1.lineDisp*')
# the old rule these endpoints leave: release_timing_waivers.json rows
OLD_WAIVER = dict(launch='*video*', latch=('clk1x', 'clk2x'), check='setup', floor_ns=-6.0)


def patterns():
    return [SCOPE + p for p in COVERED]


def collection():
    return '[get_registers {' + ' '.join(patterns()) + '}]'


def constraint_lines(bound=BOUND_NS):
    return [f'{BEGIN} bound_ns={bound:.1f}',
            '# scripts/prepare_video_sync_constraints.py: first stages of the video-out engine\'s register chains',
            '# from the APF video clock into clk1x / clk2x. One-bit levels, the quasi-static fetch size and the',
            '# line number the clk2x side takes four cycles after it changes: bounded instead of waived.',
            f'set_max_delay {bound:.1f} -to {collection()}',
            f'set_min_delay -{bound:.1f} -to {collection()}',
            END]


def marker_bound(sdc_text):
    """The bound of a project that carries the constraint, else None. A half block is an error."""
    begins = re.findall(r'^' + re.escape(BEGIN) + r' bound_ns=(\d+\.\d)$', sdc_text, re.M)
    ends = re.findall(r'^' + re.escape(END) + r'$', sdc_text, re.M)
    if not begins and not ends and BEGIN not in sdc_text and END not in sdc_text:
        return None
    if len(begins) != 1 or len(ends) != 1 or sdc_text.index(BEGIN) > sdc_text.index(END):
        raise ValueError('Malformed video sync constraint block')
    return float(begins[0])


def without_block(sdc_text):
    """The SDC text with the block removed (the old rule's analysis)."""
    if marker_bound(sdc_text) is None:
        return sdc_text
    return re.sub(r'\n?' + re.escape(BEGIN) + r'.*?' + re.escape(END) + r'\n?', '\n', sdc_text, flags=re.S)


def guard(async_text, top_text, package_text):
    """Raise unless the sources still have what the coherency argument rests on."""
    def need(condition, what):
        if not condition: raise ValueError('video sync guard: ' + what)
    flat = lambda t: re.sub(r'\s+', ' ', re.sub(r'--[^\n]*', '', t))
    a, t, p = flat(async_text), flat(top_text), flat(package_text)
    need(re.search(r'process \(clk1x\) begin if rising_edge\(clk1x\) then videoout_reports_s1 <= videoout_reports; '
                   r'videoout_reports_s2 <= videoout_reports_s1; videoout_reports_1x <= videoout_reports_s2; '
                   r'allowunpause_s1 <= allowunpause; allowunpause_s2 <= allowunpause_s1; '
                   r'allowunpause1x <= allowunpause_s2; end if; end process;', a), 'clk1x report chain changed')
    need(re.search(r'process \(clk2x\) begin if rising_edge\(clk2x\) then videoout_request_s1 <= videoout_request; '
                   r'videoout_request_s2 <= videoout_request_s1; videoout_request_2x <= videoout_request_s2; '
                   r'end if; end process;', a), 'clk2x request chain changed')
    need(a.count('_s1 <=') == 4 and 'reset_s1 <= reset_s2;' in a, 'another *_s1 first stage appeared')
    need('videoout_request.fetchsize <= to_unsigned(xmax, 10);' in a and a.count('xmax <=') == 1,
         'fetch size is no longer the measured width alone')
    reports = re.search(r'type tvideoout_reports is record (.*?) end record;', p)
    need(reports and all(re.fullmatch(r'\w+ : std_logic', f.strip())
                         for f in reports[1].rstrip('; ').split(';')), 'a report field is wider than one bit')
    used = set(re.findall(r'videoout_request_clk2x\.(\w+)', t))
    need(used == {'fetch', 'fetchsize', 'lineInNext'}, f'clk2x side reads other request fields: {sorted(used)}')
    wait = re.search(r'when WAITNEWLINE => (.*?) when WAITREQUEST => (.*?) when REQUEST =>', t)
    need(wait and 'waitcnt <= 3; state <= WAITREQUEST;' in wait[1], 'no four-cycle wait after a new line is seen')
    need(wait and re.match(r'if \(waitcnt > 0\) then waitcnt <= waitcnt - 1; else state <= REQUEST; '
                           r'lineAct <= videoout_request_clk2x\.lineInNext;', wait[2]) and
         'videoout_request_clk2x' not in t.split('when REQUEST =>', 1)[1].split('end process', 1)[0] and
         'fetchsize' not in wait[1], 'line number or fetch size is taken outside the waited state')
    return True


def sources(project, qsf_text):
    """(async, top, package) texts of the compiled files. A capture's QSF may still name the
    fit's old directory: the file is then the one under the project with the same tail."""
    project, found = Path(project), []
    assigned = [Path(q or b) for q, b in re.findall(
        r'^set_global_assignment -name VHDL_FILE (?:"([^"]+)"|(\S+))', qsf_text, re.M)]
    for name in ('gpu_videoout_async.vhd', 'gpu_videoout.vhd', 'pGPU.vhd'):
        wanted = [a for a in assigned if a.name == name]
        if len(wanted) != 1:
            raise ValueError(f'Expected one compiled {name}, found {len(wanted)}')
        local = [wanted[0]] if wanted[0].is_file() else [
            f for f in project.rglob(name) if str(wanted[0]).endswith('/' + str(f.relative_to(project)))]
        if len(local) != 1:
            raise ValueError(f'Compiled {name} not found under the project')
        found.append(local[0].read_text(errors='replace'))
    return found


def apply(project):
    project = Path(project).resolve()
    qsf = (project / 'apf_top.qsf').read_text()
    guard(*sources(project, qsf))
    sdc = project / 'pocket.sdc'
    text = sdc.read_text()
    if marker_bound(text) is not None:
        raise ValueError('Video sync constraint already applied')
    if not re.search(r'^set_global_assignment -name SDC_FILE ("?)(pocket\.sdc|' + re.escape(str(sdc)) + r')\1\s*$', qsf, re.M):
        raise ValueError(f'The QSF does not read {sdc}')
    sdc.write_text(text.rstrip('\n') + '\n' + '\n'.join(constraint_lines()) + '\n')
    return dict(sdc=str(sdc), bound_ns=BOUND_NS, covered=patterns(), not_covered=[SCOPE + p for p in NOT_COVERED],
                replaces=OLD_WAIVER, fit_verified=False, timing_verified=False)


def prepare(source, output, seed=None):
    """A fresh frozen project OUTPUT = SOURCE + the block, relocated and re-manifested the way the
    other steps do it (prepare_gpu_req_hoist.py): snapshot, edit pocket.sdc inside the snapshot,
    require that pocket.sdc is the only input that changed, write the new input list, validate."""
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    load_source(source, source / 'fit-inputs.json')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    report = apply(output)
    after = inputs(output)
    a, b = ({r['path']: r for r in rows} for rows in (after, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != {str(output / 'pocket.sdc')}:
        raise ValueError('Expected only pocket.sdc to change among the inputs')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    return dict(report, source=str(source), seed=seed, inputs=len(after), manifest=str(output / 'fit-inputs.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project', type=Path, nargs='?', help='frozen project with fit-inputs.json')
    parser.add_argument('--output', type=Path, help='fresh directory for the frozen project with the block')
    parser.add_argument('--seed', type=int, help='fitter seed of the output (default: the source\'s)')
    parser.add_argument('--print', action='store_true', help='print the SDC block and stop')
    args = parser.parse_args()
    if args.print:
        print('\n'.join(constraint_lines()))
    else:
        if not args.project or not args.output: parser.error('PROJECT and --output are required')
        print(json.dumps(prepare(args.project, args.output, args.seed), indent=2))
