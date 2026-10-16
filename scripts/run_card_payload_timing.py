#!/usr/bin/env python3
"""Native card bundled-data timing with concurrent GPU traffic.

Only simulation copies receive wire delays/event assertions. No BIOS/game
assets are used. The inherited chip model is not electrical qualification.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_pocket_card_cdc import CARD_SHA, preserve_sync_flops
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool

SELECTED_CARD_SHA = 'e54c7d9264b10ea6afc172c3d2874a2f55d0a274ebf7f2dd17f13644e7593f49'
NATIVE_SHA = '3dfd5dbd926d69f930248b54129517905118b360d0cc799752b24930fd6ffe7d'


def instrument_formatter(text):
    original=text;edits=[]
    def replace(old,new):
     nonlocal text
     assert text.count(old)==1,old
     text=text.replace(old,new,1);edits.append((old,new))
    replace('entity pocket_memcard_format is\n',
            'entity pocket_memcard_format is\n   generic (TEST_FLIGHT_PS : natural := 13000);\n')
    decl='''   -- Simulation-only wiring and event observers.
       signal test_word_wire : std_logic_vector(14 downto 0);
       signal test_data_wire : std_logic_vector(31 downto 0);
       signal test_request_time, test_ack_time : time := 0 ns;
       signal test_min_request_age, test_min_capture_age : time := 1 sec;
       signal test_captured_ack : std_logic := '0';
       signal test_requests, test_acks, test_captures : natural := 0;
       signal test_reported : boolean := false;
    '''
    anchor='   -- Word w (0-2047) of a formatted card'
    replace(anchor,decl+'\n'+anchor)
    marker='\nbegin\n\n   ---------------------------------------------------------------- clk_74a'
    statements='''
       test_word_wire <= transport rd_word after TEST_FLIGHT_PS * 1 ps;
       test_data_wire <= transport rd_data after TEST_FLIGHT_PS * 1 ps;
       test_launch : process(rd_ack)
       begin
          if rd_ack'event and now > 0 ns then
             assert test_captured_ack = not rd_ack
                report "CARD_PAYLOAD_OVERWRITTEN_BEFORE_CAPTURE" severity failure;
             test_ack_time <= now;
             test_acks <= test_acks + 1;
          end if;
       end process;
       test_coverage : process(clk_74a)
       begin
          if rising_edge(clk_74a) and test_captures = 32768 and not test_reported then
             report "CARD_BUNDLE_COVERAGE requests=" & natural'image(test_requests)
                & " acks=" & natural'image(test_acks)
                & " captures=" & natural'image(test_captures)
                & " min_request_age_fs=" & time'image(test_min_request_age)
                & " min_capture_age_fs=" & time'image(test_min_capture_age);
             test_reported <= true;
          end if;
       end process;
    '''
    replace(marker,'\nbegin\n'+statements+'\n   ---------------------------------------------------------------- clk_74a')
    old='            rd_word <= bridge_addr(16 downto 2);'
    replace(old,'''            assert test_captured_ack = rd_tog
                   report "CARD_REQUEST_OVERRUN" severity failure;
                test_request_time <= now;
                test_requests <= test_requests + 1;
    '''+old)
    old='            bridge_rd_data <= rd_data;'
    replace(old,'''            assert now - test_ack_time >= 26936 ns / 1000
                   report "CARD_EARLY_CAPTURE" severity failure;
                assert test_data_wire = rd_data
                   report "CARD_PAYLOAD_NOT_SETTLED" severity failure;
                if now-test_ack_time < test_min_capture_age then
                   test_min_capture_age <= now-test_ack_time;
                end if;
                test_captured_ack <= rd_ack;
                test_captures <= test_captures + 1;
                bridge_rd_data <= test_data_wire;''')
    old='                  elsif (rd_tog_s(2) /= rd_ack) then'
    replace(old,old+'''
                         assert now-test_request_time >= 29525100 fs
                            report "CARD_REQUEST_CAPTURE_EARLY" severity failure;
                         assert test_word_wire = rd_word
                            report "CARD_REQUEST_NOT_SETTLED" severity failure;
                         if now-test_request_time < test_min_request_age then
                            test_min_request_age <= now-test_request_time;
                         end if;''')
    replace('unsigned(rd_word)                when READ_WAIT,',
            'unsigned(test_word_wire)         when READ_WAIT,')
    replace("pf_word = unsigned(rd_word)","pf_word = unsigned(test_word_wire)")
    restored=text
    for old,new in reversed(edits):
     assert restored.count(new)==1
     restored=restored.replace(new,old,1)
    assert restored==original
    return text


def run_checks(directory):
    directory = Path(directory).resolve()
    if directory.exists():
        raise ValueError('Fresh payload timing output required')
    source = ROOT / 'rtl/pocket/pocket_memcard_format.vhd'
    native = ROOT / 'references/pocket_sotn/pocket/src/fpga/core/vram_psram.vhd'
    asyncpart = native.with_name('psram_async.vhd')
    model = ROOT / 'references/pocket_sotn/pocket/sim/vram_psram/psram_model.vhd'
    bench = ROOT / 'sim/pocket/tb_card_payload_native.vhd'
    fifo = ROOT / 'sim/pocket/altera_mf_dcfifo_model.vhd'
    if digest(source) != CARD_SHA or digest(native) != NATIVE_SHA:
        raise ValueError('Unreviewed formatter/Native source')
    protected = {str(p): digest(p) for p in (source, native, asyncpart, model, bench, fifo, Path(__file__))}
    selected = preserve_sync_flops(source.read_text())
    import hashlib
    if hashlib.sha256(selected.encode()).hexdigest() != SELECTED_CARD_SHA:
        raise ValueError('Selected formatter attributes differ')
    body = instrument_formatter(selected)
    directory.mkdir(parents=True)
    prepared = directory / 'selected-formatter.vhd'
    prepared.write_text(selected)
    runs = []

    def compile_mode(mode, text):
        work = directory / mode; work.mkdir()
        formatter = work / 'formatter.vhd'; formatter.write_text(text)
        with (work / 'compile.log').open('w') as log:
            for lib, p in (('altera_mf', fifo), ('work', asyncpart), ('work', native),
                           ('work', model), ('work', formatter), ('work', bench)):
                subprocess.run([find_tool('ghdl'), '-a', '--std=08', '-frelaxed-rules',
                                '--work=' + lib, str(p)], cwd=work, stdout=log,
                               stderr=subprocess.STDOUT, check=True, timeout=60)
            subprocess.run([find_tool('ghdl'), '-e', '--std=08', '-frelaxed-rules',
                            'tb_memcard_format'], cwd=work, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=60)
        return work, formatter

    positive, pos_source = compile_mode('positive', body)
    original = 'if (rd_ack_s(2) /= rd_ack_s(1)) then'
    if body.count(original) != 1:
        raise ValueError('Capture fault anchor differs')
    early, bad_source = compile_mode('early-capture', body.replace(
        original, 'if (rd_ack_s(1) /= rd_ack_s(0)) then', 1))
    for name, work, formatter, flight, marker in (
            ('positive', positive, pos_source, 13000, None),
            ('early-capture', early, bad_source, 0, 'CARD_EARLY_CAPTURE'),
            ('late-payload-flight', positive, pos_source, 41000, 'CARD_PAYLOAD_NOT_SETTLED')):
        dump = work / (name + '.txt'); log = work / (name + '.log')
        command = [find_tool('ghdl'), '-r', '--std=08', '-frelaxed-rules',
                   'tb_memcard_format', '-gLOAD_KIND=2', '-gREAD_WORDS=32768',
                   '-gDUMP=' + str(dump), '-gFLIGHT_PS=' + str(flight),
                   '--ieee-asserts=disable', '--assert-level=failure']
        result = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=600)
        output = result.stdout + result.stderr; log.write_text(output)
        row = dict(name=name, exit_code=result.returncode, flight_ps=flight, command=command,
                   formatter=dict(path=str(formatter), sha256=digest(formatter)),
                   log=dict(path=str(log), sha256=digest(log)), expected_fault=marker)
        if marker:
            if result.returncode == 0 or marker not in output:
                raise ValueError('Actual payload fault escaped: ' + name + '\n' + output[-2000:])
            row['actual_fault_rejected'] = True
        else:
            if result.returncode or 'tb_memcard_format done' not in output:
                raise ValueError('Composed Native/card simulation failed:\n' + output[-2000:])
            expected = [0x434d] + [0x5a000000 | i for i in range(1, 32768)]
            read = Path(str(dump) + '.read'); random = Path(str(dump) + '.random')
            if ([int(v, 16) for v in read.read_text().split()] != expected or
                    [int(v, 16) for v in random.read_text().split()] !=
                    [expected[i] for i in (32767, 2, 1047, 9, 0)]):
                raise ValueError('Independent full card/readback word mismatch')
            m = re.search(r'CARD_NATIVE_COVERAGE GPU_bursts=(\d+) GPU_words=(\d+) AUX_max=(\d+)', output)
            c = re.search(r'CARD_BUNDLE_COVERAGE requests=(\d+) acks=(\d+) captures=(\d+) min_request_age_fs=(\d+) fs min_capture_age_fs=(\d+) fs', output)
            if not m or not c:
                raise ValueError('Missing Native/card timing coverage')
            row['Native'] = dict(zip(('GPU_bursts', 'GPU_words', 'max_AUX_cycles'), map(int, m.groups())))
            row['payload'] = dict(zip(('requests', 'acks', 'captures', 'min_request_age_fs',
                                       'min_capture_age_fs'), map(int, c.groups())))
            row['readback'] = dict(path=str(read), sha256=digest(read), words=32768)
            row['nonsequential_readback'] = dict(path=str(random), sha256=digest(random))
            if (row['Native']['max_AUX_cycles'] > 30 or row['payload']['captures'] != 32768 or
                    row['payload']['min_capture_age_fs'] < 26936000 or
                    row['payload']['min_request_age_fs'] < 29525100):
                raise ValueError('Payload interval/deadline contract failed')
        runs.append(row)
        print(name + ': expected result observed', flush=True)
    if any(digest(Path(p)) != sha for p, sha in protected.items()):
        raise ValueError('Source changed during card timing proof')
    report = dict(passed=True, protected=protected, runner_sha256=digest(Path(__file__)),
                  selected_formatter=dict(path=str(prepared), sha256=digest(prepared)),
                  Native_sha256=NATIVE_SHA, positive_body_reverses_exactly=True,
                  host_read_write_interval_cycles=88, payload_flight_bound_ns=13,
                  request_consumer_lower_bound_fs=29525100, ACK_capture_lower_bound_fs=26936000,
                  runs=runs, actual_Native_and_formatter_connected=True,
                  inherited_chip_model=True, independent_electrical_qualification=False,
                  actual_APF_phy_connected=False, hardware_OS_verified=False, hardware_verified=False)
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        directory = args.output
    else:
        parent = ROOT / 'build/sim/card-payload-timing'; parent.mkdir(parents=True, exist_ok=True)
        # The runner owns creation and refuses existing output.
        directory = Path(tempfile.mkdtemp(dir=parent)); directory.rmdir()
    report = run_checks(directory)
    print('Card payload timing proof:', directory / 'report.json')

