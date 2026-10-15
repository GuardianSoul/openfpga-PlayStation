#include "Vgte_save_top.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>

int main(int argc,char** argv) {
    Verilated::commandArgs(argc,argv);
    Vgte_save_top d;
    std::mt19937 rng(0x53534754);
    unsigned phase=0,checks=0,roundtrips=0,commands=0;
    auto require=[](bool ok,const char* why) {if(!ok) throw std::runtime_error(why);};
    auto tick=[&]() {d.clk2x=0;d.clk2xIndex=phase;d.eval();d.clk2x=1;d.eval();d.clk2x=0;d.eval();phase^=1;};
    auto write=[&](unsigned a,uint32_t v) {d.write_addr=a;d.write_data=v;d.write_enable=1;tick();tick();d.write_enable=0;};
    auto read=[&](unsigned a) {d.read_addr=a;d.read_enable=1;tick();tick();d.read_enable=0;
        require(d.read_data==d.reference_data,"Restored guest register differs from untouched reference");++checks;return uint32_t(d.read_data);};
    auto execute=[&](uint32_t cmd) {d.command=cmd;d.command_enable=1;tick();tick();d.command_enable=0;
        require(d.busy && d.reference_busy,"Command did not start");
        unsigned n=0;while(d.busy || d.reference_busy) {tick();require(++n<400,"Command timeout");}
        tick();tick();++commands;};
    try {
        d.ce=1;d.reset=1;d.reference_reset=1;for(unsigned i=0;i<8;++i)tick();
        d.reset=0;d.reference_reset=0;for(unsigned i=0;i<8;++i)tick();
        const unsigned ids[]={1,6,12,16,17,18,19,20,22,27,28,30,32,40,41,42,45,46,48,61,62,63};
        for(unsigned trial=0;trial<256;++trial) {
            for(unsigned a=0;a<64;++a)write(a,rng());
            execute(ids[trial%22] | ((trial&1)<<19) | (((trial>>1)&1)<<10));
            std::array<uint32_t,64> image;
            d.ce=0;
            for(unsigned a=0;a<64;++a) {d.ss_addr=a;d.eval();image[a]=d.ss_read;tick();tick();}
            d.reset=1;for(unsigned i=0;i<8;++i)tick();d.reset=0;
            d.loading=1;d.ss_write=1;
            // Pseudorandom order checks restoration does not depend on FIFO,
            // IRGB or leading-zero alias writes or a particular scan order.
            for(unsigned j=0;j<64;++j) {unsigned a=(j*37+trial)%64;d.ss_addr=a;d.ss_data=image[a];tick();tick();}
            d.ss_write=0;
            // The real save sequencer pulses reset again with loading high.
            d.reset=1;for(unsigned i=0;i<8;++i)tick();d.reset=0;
            for(unsigned a=0;a<64;++a) {d.ss_addr=a;d.eval();require(d.ss_read==image[a],"Save image did not round-trip");++checks;}
            d.loading=0;d.ce=1;
            for(unsigned a=0;a<64;++a)read(a);
            execute(ids[(trial+7)%22] | ((trial&1)<<19));
            for(unsigned a=0;a<64;++a)read(a);
            ++roundtrips;
        }
        std::cout<<"PASS GTE save roundtrips="<<roundtrips<<" commands="<<commands<<" comparisons="<<checks<<"\n";
    } catch(const std::exception& e) {std::cerr<<"FAIL "<<e.what()<<" trial="<<roundtrips<<"\n";return 1;}
}
