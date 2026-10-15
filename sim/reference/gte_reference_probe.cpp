// SPDX-License-Identifier: GPL-2.0-only
// Runs a GTE vector file (W reg value / C command / R reg) through the
// adapted Laxer GTE (gte_adapter_top) with the clock enable always on and
// prints "R reg value" for every read, for comparison with a model.
#include "Vgte_adapter_top.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    if (argc != 2) return 2;
    Vgte_adapter_top dut;
    unsigned phase = 0;
    auto tick = [&]() {
        dut.clk2x = 0; dut.reference_clock = 0; dut.clk2xIndex = phase; dut.ce = 1; dut.eval();
        const bool engine_tick = dut.reset || phase;
        dut.clk2x = 1; dut.eval();
        if (engine_tick) { dut.reference_addr = dut.write_addr; dut.eval(); dut.reference_clock = 1; dut.eval(); }
        dut.clk2x = 0; dut.reference_clock = 0; dut.eval();
        phase ^= 1;
        return !dut.reset && !phase;
    };
    auto write = [&](unsigned addr, uint32_t data) {
        dut.write_addr = addr; dut.write_data = data; dut.write_enable = 1;
        while (!tick()) {}
        dut.write_enable = 0;
    };
    auto read = [&](unsigned addr) {
        dut.read_addr = addr; dut.read_enable = 1;
        tick();
        dut.read_enable = 0;
        return uint32_t(dut.read_data);
    };
    try {
        dut.reset = 1; dut.command_enable = 0; dut.write_enable = 0; dut.read_enable = 0;
        for (unsigned i = 0; i < 8; ++i) tick();
        dut.reset = 0;
        for (unsigned i = 0; i < 8; ++i) tick();
        std::ifstream input(argv[1]);
        if (!input) throw std::runtime_error("missing vector file");
        char operation; unsigned address; uint32_t value;
        unsigned commands = 0;
        while (input >> operation >> std::hex >> address >> value) {
            if (dut.busy) throw std::runtime_error("busy before operation");
            if (operation == 'W') write(address, value);
            else if (operation == 'R') std::printf("R %02x %08x\n", address, read(address));
            else if (operation == 'C') {
                dut.command = value; dut.command_enable = 1;
                while (!tick()) {}
                dut.command_enable = 0;
                unsigned elapsed = 0;
                while (dut.busy) { tick(); if (++elapsed > 400) throw std::runtime_error("command did not finish"); }
                ++commands;
            } else throw std::runtime_error("unknown operation");
        }
        dut.final();
        std::printf("DONE commands=%u\n", commands);
    } catch (const std::exception& error) {
        std::fprintf(stderr, "FAIL %s\n", error.what());
        return 1;
    }
}
