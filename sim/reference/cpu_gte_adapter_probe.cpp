#include "Vcpu_gte_adapter_top.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

static std::vector<uint32_t> load(const char* path) {
    std::ifstream input(path); if (!input) throw std::runtime_error("Missing diagnostic file");
    std::vector<uint32_t> result; uint32_t word;
    while (input >> std::hex >> word) result.push_back(word);
    if (!input.eof()) throw std::runtime_error("Invalid diagnostic hex");
    return result;
}
int main(int argc, char** argv) {
    Verilated::commandArgs(argc,argv);
    if (argc!=4) return 2;
    Vcpu_gte_adapter_top dut;
    const auto program=load(argv[1]), expected=load(argv[2]);
    const unsigned seed=std::stoul(argv[3]);
    std::vector<uint32_t> ram(16384,0);
    bool pending=false,finished=false;
    uint32_t pending_addr=0;
    unsigned delay=0,transactions=0,checks=0,command_pulses=0;
    bool previous_command=false;
    auto require=[&](bool condition,const std::string& message) { if (!condition) throw std::runtime_error(message); };
    dut.clk1x=1; dut.clk2x=1; dut.clk3x=1; dut.ce=1; dut.reset=1; dut.ss_reset=1;
    dut.read_data=0; dut.done=0; dut.ram_done=0; dut.eval();
    try {
        for (unsigned cycle=0;cycle<1000000 && !finished;++cycle) {
            if (cycle==4) dut.ss_reset=0;
            if (cycle==54) dut.reset=0;
            for (unsigned tick=1;tick<=12;++tick) {
                const bool memory_edge=tick==12;
                const bool req=dut.request, rnw=dut.rnw, data=dut.isdata, cached=dut.iscache;
                const uint32_t address=data ? dut.addr_data : dut.addr_instr;
                const uint32_t written=dut.write_data; const unsigned mask=dut.mask;
                dut.clk1x=tick%12<6; dut.clk2x=tick%6<3; dut.clk3x=tick%4<2; dut.eval();
                if (!dut.reset && dut.gte_command_enable && !previous_command) ++command_pulses;
                previous_command=dut.gte_command_enable;
                if (!memory_edge || dut.reset) continue;
                dut.done=0; dut.ram_done=0;
                if (pending) {
                    if (delay) --delay;
                    else {
                        const uint32_t physical=pending_addr&0x1fffffff;
                        if (physical>=0x1fc00000 && physical<0x1fc80000) {
                            const unsigned offset=(physical-0x1fc00000)/4;
                            require(offset<program.size(),"Fetch outside authored program");
                            dut.read_data=program[offset];
                        } else {
                            require(physical<65536,"Unexpected diagnostic read");
                            dut.read_data=ram[physical/4];
                        }
                        dut.done=1; dut.ram_done=1; pending=false;
                    }
                }
                if (req) {
                    require(!pending,"Overlapping diagnostic reads");
                    require(!cached,"Unexpected cache fill"); ++transactions;
                    const uint32_t physical=address&0x1fffffff;
                    if (rnw) { pending=true; pending_addr=address; delay=(transactions+seed)%5; }
                    else {
                        require(data && physical<65536 && mask==15,"Unexpected diagnostic write");
                        ram[physical/4]=written;
                        if (physical>=4096 && physical<4096+expected.size()*4) {
                            const unsigned index=(physical-4096)/4;
                            require(index==checks,"Missing/reordered result stores");
                            require(written==expected[index],"CPU/adapter mismatch index="+std::to_string(index)+
                                " actual="+std::to_string(written)+" expected="+std::to_string(expected[index]));
                            ++checks;
                        } else if (physical==0xff00) {
                            require(written==0x600df00d && checks==expected.size() && command_pulses==448,"Incomplete diagnostic");
                            require(ram[0xf004/4]==0xffff8001,"LWC2/SWC2 roundtrip mismatch");
                            finished=true;
                            std::cout << "PASS CPU/GTE adapter seed=" << seed << " cycles=" << cycle
                                << " commands=" << command_pulses << " checks=" << checks << " transactions=" << transactions << "\n";
                        }
                    }
                }
                dut.eval();
            }
        }
        require(finished,"CPU/adapter execution timeout"); dut.final();
    } catch(const std::exception& error) { std::cerr << "FAIL CPU/GTE adapter seed=" << seed << " " << error.what() << "\n"; return 1; }
}
