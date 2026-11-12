"""prepare_save_rom_ext.py: the device, core_top and routine transforms; the builder with and without the extension."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_rom_ext as step
import build_staged_save_fw as fw
from tool_paths import find_tool

# the lines of the E3m device the step anchors on
DEVICE = '''module psx_save_exp1 #(
    parameter ROM_HEX = "",     // 1024 x 40 bits: {second ROM byte i, first ROM word i}
    parameter [31:0] SAVE_BASE = 32'h3000_0000
) (
    input  wire        core_reset,
    input  wire [12:0] bus_addr
);
    wire [7:0] rom2_q = rom_q[39:32];
    wire [7:0] rom1_q = rom_q[8*bus_addr[1:0] +: 8];
    always @(*) begin
        else if (bus_addr[12] && bus_addr[11] && bus_addr[10]) bus_dataRead = rom2_q;
        else bus_dataRead = bus_addr[12] ? page_q : rom1_q;
    end
    always @(posedge clk2x) begin
        if (reset_2x) begin
            trap <= 1'b0; save_mode <= 1'b0; cd_freeze <= 1'b0;
        end else begin
            if (wr_edge && reg_area && !bus_addr[5] && !bus_addr[4] && bus_addr[3:0] == 4'd14) save_mode <= bus_dataWrite[0];
            if (core_edge) begin
                save_mode <= 1'b0; cd_freeze <= 1'b0; disc_silent <= 1'b0;
            end
        end
    end
endmodule
`default_nettype wire
'''
CORE_TOP = ('    psx_save_exp1 #(.ROM_HEX("/p/save-states/psx_save_rom40.hex"), .SAVE_BASE(32\'h3000_0000)) save_exp1 (\n')
STAGED = (Path(__file__).resolve().parents[1] / 'rtl/pocket/save_fw/staged_handler.S').read_text()
TOOLS = all(find_tool(t) for t in ('llvm-mc', 'ld.lld', 'llvm-objcopy', 'llvm-nm'))


class Device(unittest.TestCase):
    def test_transform_and_its_inverse(self):
        new = step.device_text(DEVICE)
        self.assertEqual(step.restore(new), DEVICE)
        self.assertIn('psx_save_rom_ext #(.HEX(EXT_HEX)) rom_ext (.clk(clk2x), .addr(bus_addr[9:0]), .q(ext_q));', new)
        self.assertIn('bus_dataRead = rom_bank ? ext_q : rom2_q;', new)
        self.assertIn("begin save_mode <= bus_dataWrite[0]; rom_bank <= bus_dataWrite[1]; end", new)
        self.assertEqual(new.count("save_mode <= 1'b0; rom_bank <= 1'b0;"), 2)      # reset_2x and the core reset edge
        self.assertEqual(new.count('endmodule'), 2)
        self.assertTrue(new.endswith('endmodule\n`default_nettype wire\n'))
        self.assertIn('reg [7:0] rom [0:1023];', new)
        # the first ROM's byte and every other read source are untouched
        for kept in ('wire [7:0] rom1_q = rom_q[8*bus_addr[1:0] +: 8];', 'else bus_dataRead = bus_addr[12] ? page_q : rom1_q;'):
            self.assertIn(kept, new)

    def test_refuses_a_second_run_and_changed_anchors(self):
        with self.assertRaises(ValueError):
            step.device_text(step.device_text(DEVICE))
        with self.assertRaises(ValueError):          # before save-core-reset
            step.device_text(DEVICE.replace('core_reset', 'other').replace('core_edge', 'edge'))
        with self.assertRaises(ValueError):          # a third place that clears SAVE_MODE
            step.device_text(DEVICE.replace("trap <= 1'b0; save_mode", "save_mode <= 1'b0; cd_freeze <= 1'b0; trap <= 1'b0; save_mode"))
        with self.assertRaises(ValueError):          # the second ROM's select changed
            step.device_text(DEVICE.replace('bus_addr[10]) bus_dataRead = rom2_q', 'bus_addr[9]) bus_dataRead = rom2_q'))

    def test_every_fault_changes_the_text(self):
        new = step.device_text(DEVICE)
        for name in step.FAULTS:
            self.assertNotEqual(step.fault_text(new, name), new, name)

    def test_core_top_names_the_extension_next_to_the_rom(self):
        new = step.core_top_text(CORE_TOP)
        self.assertIn('.ROM_HEX("/p/save-states/psx_save_rom40.hex"), .EXT_HEX("/p/save-states/psx_save_rom_ext.hex"), .SAVE_BASE', new)
        with self.assertRaises(ValueError):
            step.core_top_text(new)
        with self.assertRaises(ValueError):
            step.core_top_text(CORE_TOP + CORE_TOP)

    def test_extension_file(self):
        self.assertEqual(step.ext_hex(), '00\n' * 1024)
        self.assertEqual(step.ext_hex(bytes(range(256)) * 4).splitlines()[255:258], ['ff', '00', '01'])
        with self.assertRaises(ValueError):
            step.ext_hex(bytes(1023))


class Routine(unittest.TestCase):
    def test_loader_text(self):
        new = step.firmware_text(STAGED)
        self.assertEqual(new.count('rom_ext:'), 1)
        added = [l for l in new.splitlines() if l not in STAGED.splitlines()]
        self.assertEqual(len([l for l in added if not l.lstrip().startswith('#') and ':' not in l.split('#')[0]]), 6)   # 5 new (the sixth, a nop, is a line the file has), 1 comment changed
        with self.assertRaises(ValueError):
            step.firmware_text(new)
        self.assertNotEqual(step.routine_fault_text(new, 'no-bank'), new)

    def test_pad_is_seeded_and_whole_words(self):
        a, b = step.pad_text(STAGED, 64), step.pad_text(STAGED, 64)
        self.assertEqual(a, b)
        self.assertEqual(sum(l.count('0x') for l in a.splitlines() if l.startswith('        .byte   0x')) -
                         sum(l.count('0x') for l in STAGED.splitlines() if l.startswith('        .byte   0x')), 64)
        with self.assertRaises(ValueError):
            step.pad_text(STAGED, 6)

    @unittest.skipUnless(TOOLS, 'LLVM tools needed')
    def test_unused_extension_leaves_the_four_blocks(self):
        image, stage = fw.image_parts()
        image_x, stage_x, ext = fw.image_parts_ext()
        self.assertEqual((image, stage), (image_x, stage_x))
        self.assertEqual(ext, bytes(1024))
        self.assertEqual(fw.free_bytes(image), fw.free_bytes(image_x))

    @unittest.skipUnless(TOOLS, 'LLVM tools needed')
    def test_stream_goes_on_in_the_extension(self):
        import tempfile
        plain, plain_stage = fw.image_parts()
        room = fw.free_bytes(plain)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'routine.S'
            # the loader alone: 24 bytes more stub, the stream still ends in the four blocks
            source.write_text(step.firmware_text(STAGED))
            image, stage, ext = fw.image_parts_ext(source=source)
            self.assertEqual(ext, bytes(1024))
            stub = lambda data, s: next(i for i in range(0, 0xFF4, 4) if data[i:i + 4] == s[0:4])   # the stamp follows the stub
            self.assertEqual(stub(image, stage), stub(plain, plain_stage) + 24)
            # a pad that does not fit the four blocks: refused without the loader, in the extension with it
            pad = (room + 300) // 4 * 4
            source.write_text(step.pad_text(STAGED, pad))
            with self.assertRaises(SystemExit):
                fw.image_parts_ext(source=source)
            source.write_text(step.pad_text(step.firmware_text(STAGED), pad))
            with self.assertRaises(SystemExit):
                fw.image_parts(source=source)                      # the old entry point refuses a used extension
            image, stage, ext = fw.image_parts_ext(source=source)
            used = len(ext.rstrip(b'\0'))
            self.assertTrue(200 < used < 700, used)
            # the stub's three copies, in order, are the packed stream: it unpacks to stage 2
            first = stub(image, stage)
            blob = image[first:0xFF4] + image[0x1C00:0x2000] + ext
            self.assertEqual(int.from_bytes(blob[4:8], 'little'), len(stage))
            self.assertEqual(fw.lz_unpack(blob[8:], len(stage)), stage)
            self.assertEqual(fw.free_bytes(image, ext), 1024 - used)
            # too much even for the extension
            source.write_text(step.pad_text(step.firmware_text(STAGED), pad + 1024))
            with self.assertRaises(SystemExit):
                fw.image_parts_ext(source=source)


if __name__ == '__main__':
    unittest.main()
