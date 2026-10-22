"""prepare --pad-lean: drop two 32-bit copies from the APF template's pad reader (docs/pocket-launch-timing.md).

references/MIRLO64/platform/pocket/io_pad_controller.v keeps, besides the
receive shift register, a copy of each received word (rx_word, loaded when
the 32nd bit is in and read by the button state machine on the next clock,
rx_word_done), and a transmit shift register (tx_word_shift) loaded from
tx_word at each request and shifted left once a bit.

- rx_word: the receive shift register can only change again 26 clocks
  after the next word's first falling edge (bit sample at BITLEN/2-4), so
  on the clock rx_word_done is up it still holds the word: the state
  machine reads it directly.
- tx_word_shift: the bit sent in bit time tr_bit (0..31) is
  tx_word[31 - tr_bit], and tx_word holds through a transmission (the next
  request waits for tx_word_done); synthesis already reduces tx_word to the
  bits where its two request constants differ.

Same clocks, same pad line waveform, same outputs
(sim/pocket/tb_pad_lean.sv runs both in lockstep).
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'references/MIRLO64/platform/pocket/io_pad_controller.v'

EDITS = [
    ("        rx_word <= rx_word_shift;\n", ''),
    ("            tx_word_shift <= tx_word;\n", ''),
    ("            pad_1wire <= tx_word_shift[31];\n",
     "            pad_1wire <= tx_word[5'd31 - tr_bit[4:0]]; // --pad-lean: the bit the shift register used to send\n"),
    ("            tx_word_shift <= {tx_word_shift[30:0], 1'b1};\n", ''),
    ("    reg [31:0]  rx_word;\n", "    wire [31:0] rx_word = rx_word_shift; // --pad-lean: held while rx_word_done is up\n"),
    ("    reg [31:0]  tx_word_shift;\n", ''),
]


def patch(text):
    for old, new in EDITS:
        if text.count(old) != 1: raise ValueError(f'--pad-lean: io_pad_controller changed: {old.strip()[:70]}')
        text = text.replace(old, new)
    if 'tx_word_shift' in text: raise ValueError('--pad-lean: tx_word_shift still used')
    return text


def lean_source(directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    out = directory/'io_pad_controller.v'
    out.write_text('// Modified by scripts/prepare_pad_lean.py (--pad-lean): see its docstring.\n' + patch(SOURCE.read_text()))
    return out


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/io_pad_controller\.v)"', q)
    if len(hits) != 1: raise ValueError('--pad-lean: expected one io_pad_controller.v in the project')
    out = lean_source(project/'pad-lean')
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))


if __name__ == '__main__':
    import sys
    prepare_project(sys.argv[1])
