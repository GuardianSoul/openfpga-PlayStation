#include "Vpsx_laxer_gte_resource.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>

// The existing generated GTE oracle is independent of both implementations.
// This checks a bounded command/register subset, not CPU integration or latency.
int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    if (argc != 2) return 2;
    Vpsx_laxer_gte_resource dut;
    auto tick = [&]() {
        dut.clk1x = 0; dut.eval();
        dut.clk1x = 1; dut.eval();
        dut.clk1x = 0; dut.eval();
    };
    dut.reset_n = 0; dut.reg_write = 0; dut.command_enable = 0;
    dut.reg_addr = 0; dut.reg_data = 0; dut.command = 0;
    for (int i = 0; i < 8; ++i) tick();
    dut.reset_n = 1;
    for (int i = 0; i < 8; ++i) tick();
    std::ifstream input(argv[1]);
    if (!input) return 2;
    char operation;
    unsigned address;
    uint32_t value;
    unsigned operations = 0, commands = 0, checks = 0, mismatches = 0;
    while (input >> operation >> std::hex >> address >> value) {
        if (dut.busy) { std::cerr << "Unexpected busy before probe\n"; return 1; }
        if (operation == 'W') {
            dut.reg_addr = address; dut.reg_data = value; dut.reg_write = 1;
            tick(); dut.reg_write = 0; tick();
        } else if (operation == 'R') {
            dut.reg_addr = address; dut.eval();
            if (dut.reg_read_data != value) {
                if (mismatches < 10) {
                    std::cerr << "GTE mismatch probe=" << std::dec << operations
                              << " reg=" << std::hex << address << " got=" << dut.reg_read_data
                              << " expected=" << value << "\n";
                }
                ++mismatches;
            }
            ++checks; tick();
        } else if (operation == 'C') {
            dut.command = value & 0x1ffffff; dut.command_enable = 1;
            tick(); dut.command_enable = 0;
            if (!dut.busy) { std::cerr << "GTE command did not start\n"; return 1; }
            unsigned cycles = 0;
            while (dut.busy) {
                tick();
                if (++cycles > 100) { std::cerr << "GTE command timeout\n"; return 1; }
            }
            ++commands; tick();
        } else { std::cerr << "Unknown probe\n"; return 2; }
        ++operations;
    }
    if (!input.eof()) return 2;
    dut.final();
    std::cout << (mismatches ? "FAIL" : "PASS") << " Laxer GTE operations=" << std::dec
              << operations << " commands=" << commands << " checks=" << checks
              << " mismatches=" << mismatches << "\n";
    return mismatches ? 1 : 0;
}
