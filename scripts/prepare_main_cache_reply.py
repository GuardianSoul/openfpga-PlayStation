#!/usr/bin/env python3
"""Reproduce the measured cache-reply experiment in a fresh directory.

Successful, unsplit four-word CPU cache fills publish word zero through the
existing reply mailbox while the full line uses the unchanged cache port.
DMA, scalar, error and split replies retain their original sequence. The
clock contract is the existing related 1x/3x pair. No synchronizer is removed.
This utility does not select the candidate in a physical-top generator.
"""
import argparse
import json
from pathlib import Path

from prepare_resource_targets import digest
from tool_paths import ROOT


def prepare(directory):
    original = ROOT/'rtl/pocket/psx_mister_main_adapter.sv'
    text = original.read_text()

    def replace(before, after):
        nonlocal text
        if text.count(before) != 1:
            raise ValueError('Main cache-reply adaptation anchor changed: '+before[:100])
        text = text.replace(before, after)

    replace('''                    else state<=cache && !write && !error && !s_rsp_error ? CACHE:RETURN;''', '''                    else if(early_reply) begin
                        // The memory data is complete. Overlap its unchanged
                        // 1x reply transport with 3x cache consumption.

                        response_toggle<=!response_toggle;
                        state<=CACHE;
                    end else state<=cache && !write && !error && !s_rsp_error ? CACHE:RETURN;''')
    replace('                CACHE_DRAIN: state<=RETURN;',
            '                CACHE_DRAIN: state<=full_cache_reply ? IDLE:RETURN;')
    # The asynchronous reset clears the bank. The existing reset3 fence
    # keeps the service state IDLE until synchronized reset release; its
    # redundant synchronous bank clear is unnecessary during that fence.
    if text.count('response_payload<=0;') != 2:
        raise ValueError('Main reply-bank reset anchors changed')
    text = text.replace('response_payload<=0;', '')
    replace("                    response_payload<={dma,dma_count,error,write || error ? 128'b0:read_data};", '')
    marker = '    always @(posedge clk3x or posedge reset_async) begin\n        if(reset_async) begin\n            request_sync<=0;'
    bank = '''    wire full_cache_reply=cache && !dma && !write && !error && !posted &&
        words_done==0 && packet_words==4 && words_left==4;
    wire early_reply=state==WAIT && s_rsp_valid && full_cache_reply && !s_rsp_error;
    always @(posedge clk3x or posedge reset_async) begin
        if(reset_async) response_payload<=0;
        else if(early_reply)
            response_payload<={dma,dma_count,error || s_rsp_error,
                write || error || s_rsp_error ? 128'b0:{96'b0,s_rsp_rdata[31:0]}};
        else if(state==RETURN)
            response_payload<={dma,dma_count,error,write || error ? 128'b0:read_data};
    end

'''
    replace(marker, bank+marker)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    path = directory/'psx_mister_main_adapter.sv'
    path.write_text(text)
    result = dict(path=str(path), sha256=digest(path), original=str(original),
        original_sha256=digest(original), required_clock_ratio='clk3x = 3 * clk1x, related clocks',
        full_cache_write_data_retained=True, full_DMA_reply_data_retained=True,
        synchronizers_unchanged=True, production_selected=False,
        board_timing_verified=False, game_verified=False, hardware_verified=False)
    (directory/'manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='Fresh output directory; existing paths are rejected')
    print(json.dumps(prepare(parser.parse_args().output), indent=2))
