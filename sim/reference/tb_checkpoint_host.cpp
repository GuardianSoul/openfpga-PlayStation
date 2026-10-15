// SPDX-License-Identifier: GPL-2.0-only
// Compare the serializable host against the original coroutine-based host.
#include <array>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <vector>
#include "Vtb_checkpoint_host.h"
#include "verilated.h"
#include "psx_checkpoint_host.h"

static void compare(const Vtb_checkpoint_host& rtl,const PsxCheckpointHost& host,uint64_t cycle) {
    if(rtl.sector_ack!=host.ack || rtl.sector_write!=host.write || rtl.sector_data!=host.data ||
       rtl.track_addr!=host.track_addr || rtl.track_data!=host.track_data ||
       rtl.track_write!=host.track_write || rtl.mounted!=host.mounted || rtl.sector_count!=host.sectors) {
        std::cerr<<"Host mismatch cycle="<<cycle<<" phase="<<host.phase
                 <<" ack="<<unsigned(rtl.sector_ack)<<'/'<<host.ack
                 <<" write="<<unsigned(rtl.sector_write)<<'/'<<host.write
                 <<" mounted="<<unsigned(rtl.mounted)<<'/'<<host.mounted<<'\n';
        throw std::runtime_error("Disc protocol differs");
    }
}

int main(int argc,char** argv) {
    try {
        auto context=std::make_unique<VerilatedContext>();context->commandArgs(argc,argv);
        auto model=std::make_unique<Vtb_checkpoint_host>(context.get());
        auto host=std::make_unique<PsxCheckpointHost>();
        auto configure=[](PsxCheckpointHost& value) { value.open("Disc Host Test/Disc Image.bin","trackinfo.hex32",3,2,13); };
        configure(*host);
        std::ifstream expected_file("expected.hex16");
        std::vector<uint16_t> expected;uint32_t value;
        while(expected_file>>std::hex>>value) expected.push_back(value);
        if(expected.size()!=8232) throw std::runtime_error("Expected sector fixture incomplete");
        const std::array<uint32_t,7> requests={0,149,150,151,152,153,524287};
        std::array<bool,9> restored{};
        uint32_t metadata_checks=0,word_checks=0,comparisons=0,restore_checks=0;
        bool request=false,start=false;
        model->clk=1;model->start=0;model->sector_request=0;model->eval();
        for(uint64_t cycle=0;cycle<20000 && host->sectors<7;cycle++) {
            if(host->ack) request=false;
            else if(host->mounted && host->phase==PsxCheckpointHost::IDLE) request=true;
            model->sector_request=request;
            model->physical_lba=requests[host->sectors];
            host->tick(cycle<5,request,model->physical_lba);
            context->timeInc(5);model->clk=0;model->eval();
            // The bench's start pulse follows its accepting falling edge.
            if(!start && host->phase==PsxCheckpointHost::HEADER_ON) {
                model->start=1;model->eval();start=true;
            }
            compare(*model,*host,cycle);comparisons++;
            const auto phase=host->phase;
            if(!restored[phase] && (phase!=PsxCheckpointHost::WORDS || host->word==453)) {
                VerilatedSave out;out.open("host-checkpoint.bin");host->save(out);out.close();
                auto fresh=std::make_unique<PsxCheckpointHost>();configure(*fresh);
                VerilatedRestore in;in.open("host-checkpoint.bin");fresh->restore(in);in.close();
                host=std::move(fresh);restored[phase]=true;restore_checks++;
                compare(*model,*host,cycle);
            }
            context->timeInc(5);model->clk=1;model->eval();
            compare(*model,*host,cycle);comparisons++;
            if(host->track_write) {
                if(host->track_addr!=metadata_checks || host->track_data!=host->metadata[metadata_checks])
                    throw std::runtime_error("Track record differs");
                metadata_checks++;
            }
            if(host->write) {
                if(word_checks>=expected.size() || host->data!=expected[word_checks++])
                    throw std::runtime_error("Sector word differs from independent fixture");
            }
        }
        if(metadata_checks!=400 || word_checks!=8232 || host->sectors!=7 || restore_checks!=9)
            throw std::runtime_error("Host comparison incomplete");
        model->final();
        std::cout<<"PASS checkpoint host metadata="<<metadata_checks<<" words="<<word_checks
                 <<" sectors="<<host->sectors<<" cycle_outputs="<<comparisons<<" restored_phases="<<restore_checks<<'\n';
        return 0;
    } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
