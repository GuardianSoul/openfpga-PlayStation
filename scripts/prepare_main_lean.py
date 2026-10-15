#!/usr/bin/env python3
"""psx_mister_main_adapter without its duplicated read-response copies.

The asynchronous adapter (SDRAM layout) moves each read response through
three 128-bit registers: read_data (clk3x, assembled from the memory
packets), response_payload (clk3x, a copy taken on RETURN) and return_data
(clk1x, a copy taken when the response toggle arrives). The clk3x side takes
no new work until the response is acknowledged back from clk1x, so
read_data already holds still for as long as the copies do. This is the
change b01d26d made to psx_mister_main_sync: the clk1x side reads read_data
directly, response_payload keeps only its five control bits, and IDLE no
longer clears read_data.

clk1x and clk3x come from the same PLL, so the read_data -> clk1x paths stay
fully timed related-clock paths, as response_payload -> return_data were.
"""
import argparse
from pathlib import Path


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('Main adapter source changed near: ' + old.strip().splitlines()[0])
    return text.replace(old, new)


def main_lean_text(text):
    text = _replace(text, '    reg [131:0] response_payload; // dma,count,error,128-bit read data\n',
        '    // dma,count,error,zero. The read data is not copied: the 1x side reads\n'
        '    // read_data (3x) directly, which holds still until the response is\n'
        '    // acknowledged (3x waits for that ack before taking any new work).\n'
        '    reg [4:0] response_payload;\n')
    text = _replace(text, '    reg [127:0] return_data;\n', '    reg return_zero;\n    reg [127:0] read_data;\n')
    text = _replace(text, '    assign dma_data=return_data[return_index*32+:32];\n',
                    "    assign dma_data=return_zero ? 32'b0:read_data[return_index*32+:32];\n")
    text = _replace(text, 'return_index<=0;return_count<=0;return_data<=0;\n',
                    'return_index<=0;return_count<=0;return_zero<=0;\n', 2)
    text = _replace(text, '''                    return_data<=response_payload[127:0];
                    return_count<=response_payload[130:129];return_index<=0;
                    ram_dataRead32<=response_payload[31:0];
                    return_state<=response_payload[131] ? R_DMA:R_SCALAR;
                    if(response_payload[128]) source_fault<=1;
''', '''                    return_zero<=response_payload[0];
                    return_count<=response_payload[3:2];return_index<=0;
                    ram_dataRead32<=response_payload[0] ? 32'b0:read_data[31:0];
                    return_state<=response_payload[4] ? R_DMA:R_SCALAR;
                    if(response_payload[1]) source_fault<=1;
''')
    text = _replace(text, '    reg [3:0] write_mask,cache_mask;\n    reg [127:0] read_data;\n',
                    '    reg [3:0] write_mask,cache_mask;\n')
    # As in psx_mister_main_sync, IDLE no longer clears read_data: words past
    # a response's count are never read (dma_data stops at return_count, cache
    # fills request four words, scalar reads use word 0).
    text = _replace(text, '                    error<=0;read_data<=0;words_done<=0;\n',
                    '                    error<=0;words_done<=0;\n')
    return _replace(text, "                    response_payload<={dma,dma_count,error,write || error ? 128'b0:read_data};\n",
                    '                    response_payload<={dma,dma_count,error,write || error};\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(main_lean_text(args.source.read_text()))


if __name__ == '__main__':
    main()
