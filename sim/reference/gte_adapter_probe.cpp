#include "Vgte_adapter_top.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>

// Compare enabled ticks with the unchanged original clocked only on those ticks.
// Separately check mathematical vectors; neither test executes a PSX CPU/game.
int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    if (argc != 3) return 2;
    Vgte_adapter_top dut;
    const unsigned seed = std::stoul(argv[2]);
    std::mt19937 rng(seed);
    unsigned ticks=0, disabled=0, paused_busy=0, samples=0, overlaps=0;
    unsigned operations=0, commands=0, checks=0, stress_commands=0, resets=0;
    unsigned phase=0;
    auto require = [&](bool condition, const std::string& reason) {
        if (!condition) throw std::runtime_error(reason+" tick="+std::to_string(ticks));
    };
    auto tick = [&](bool enable) {
        dut.clk2x=0; dut.reference_clock=0; dut.clk2xIndex=phase; dut.ce=enable;
        dut.reference_addr=dut.read_addr; dut.eval();
        const uint32_t expected_read=dut.reference_data;
        const uint32_t held_read=dut.read_data;
        const bool held_busy=dut.busy;
        const bool read_sample=!dut.reset && enable && dut.read_enable;
#ifdef STRICT_ENABLE
        const bool engine_tick=phase && (dut.reset || enable);
#else
        const bool engine_tick=dut.reset || (enable && phase);
#endif
        if (!enable) { ++disabled; if (dut.busy) ++paused_busy; }
        if (read_sample) { ++samples; if (engine_tick && dut.write_enable) ++overlaps; }
        dut.clk2x=1; dut.eval();
        if (engine_tick) {
            dut.reference_addr=dut.write_addr; dut.eval();
            dut.reference_clock=1; dut.eval();
        }
#ifdef STRICT_ENABLE
        // Reset clears busy at once; the engine itself resets on the next index=1 edge.
        require(!dut.reference_busy || dut.busy || dut.reset,"Adapter released busy before engine completion");
#else
        require(!dut.reference_busy || dut.busy,"Adapter released busy before engine completion");
#endif
        if (!enable && !dut.reset) require(dut.busy==held_busy,"Busy changed while paused");
        if (enable && !phase) require(dut.busy==dut.reference_busy,"Busy did not settle on index=0");
        require(dut.read_data==(read_sample ? expected_read : held_read),"Registered read/hold mismatch");
        dut.clk2x=0; dut.reference_clock=0; dut.eval();
        ++ticks; phase^=1;
        return !dut.reset && enable && !phase; // accepted at prior index=1
    };
    auto random_tick = [&]() { return tick(rng()%4 != 0); };
    auto write = [&](unsigned addr,uint32_t data) {
        dut.write_addr=addr; dut.write_data=data; dut.write_enable=1;
        while (!random_tick()) {}
        dut.write_enable=0;
    };
    auto read = [&](unsigned addr) {
        dut.read_addr=addr; dut.read_enable=1;
        do { random_tick(); } while (!dut.ce);
        dut.read_enable=0;
        return uint32_t(dut.read_data);
    };
    auto execute = [&](uint32_t command) {
        require(!dut.busy,"Busy before command");
        dut.command=command; dut.command_enable=1;
        while (!random_tick()) {}
        dut.command_enable=0;
        require(dut.busy,"Command failed to start");
        unsigned elapsed=0;
        while (dut.busy) {
            if (random_tick()) ++elapsed;
            require(elapsed<=100,"Command exceeded enabled-cycle bound");
        }
    };
    auto reset = [&]() {
        dut.reset=1; dut.command_enable=0; dut.write_enable=0; dut.read_enable=0;
        for (unsigned i=0;i<8;++i) tick(false); // reset must work with CE off
        dut.reset=0; ++resets;
        for (unsigned i=0;i<8;++i) tick(true);
        require(!dut.busy,"Reset left GTE busy");
    };
    try {
        dut.clk2x=0; dut.reference_clock=0; dut.reset=1;
        dut.read_addr=0; dut.write_addr=0; dut.write_data=0; dut.command=0;
        reset();
        std::ifstream input(argv[1]); require(bool(input),"Missing vector file");
        char operation; unsigned address; uint32_t value;
        while (input >> operation >> std::hex >> address >> value) {
            require(!dut.busy,"Busy before mathematical probe");
            if (operation=='W') write(address,value);
            else if (operation=='R') {
                const uint32_t actual=read(address);
                require(actual==value,"Mathematical mismatch probe="+std::to_string(operations)+
                        " register="+std::to_string(address)+" actual="+std::to_string(actual)+
                        " expected="+std::to_string(value)); ++checks;
            } else if (operation=='C') { execute(value); ++commands; }
            else require(false,"Unknown probe");
            ++operations;
        }
        require(input.eof(),"Truncated vector file");
        // All register addresses, same/different-address read-before-write,
        // read hold, alternating phases and paused transfers.
        for (unsigned i=0;i<4096;++i) {
            dut.read_addr=i%64; dut.read_enable=1;
            dut.write_addr=(i%3==0) ? dut.read_addr : rng()%64;
            dut.write_data=rng(); dut.write_enable=1;
            random_tick();
        }
        dut.read_enable=0; dut.write_enable=0;
        const unsigned ids[]={1,6,12,16,17,18,19,20,22,27,28,30,32,40,41,42,45,46,48,61,62,63};
        for (unsigned id:ids) for (unsigned mode=0;mode<4;++mode) for (unsigned case_no=0;case_no<8;++case_no) {
            for (unsigned addr=0;addr<64;++addr) write(addr,rng());
            uint32_t cmd=id | ((mode&1)<<19) | ((mode>>1)<<10);
            if (id==18) cmd|=(rng()%64)<<13; // all MVMVA matrix/vector/translation selectors
            execute(cmd); ++stress_commands;
            for (unsigned addr=0;addr<64;++addr) read(addr);
            if (case_no==7) reset();
        }
        // Abort in-progress commands with reset while CE is low.
        for (unsigned id:ids) {
            dut.command=id; dut.command_enable=1;
            while (!random_tick()) {}
            require(dut.busy,"Abort probe did not start");
            reset();
            for (unsigned addr=0;addr<64;++addr) read(addr);
        }
        require(paused_busy>0 && overlaps>1000 && samples>checks,"Missing stress coverage");
        dut.final();
        std::cout << "PASS GTE adapter seed=" << seed << " operations=" << operations
                  << " commands=" << commands << " checks=" << checks << " stress_commands=" << stress_commands
                  << " ticks=" << ticks << " paused_ticks=" << disabled << " paused_busy=" << paused_busy
                  << " read_samples=" << samples << " overlapping_samples=" << overlaps
                  << " resets=" << resets << "\n";
    } catch (const std::exception& error) {
        std::cerr << "FAIL GTE adapter seed=" << seed << " " << error.what() << "\n";
        return 1;
    }
}
