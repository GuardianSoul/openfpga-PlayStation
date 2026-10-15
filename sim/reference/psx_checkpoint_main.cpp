// SPDX-License-Identifier: GPL-2.0-only
// Explicit clocks allow Verilator --savable without timing coroutines.
#include <algorithm>
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include "Vpsx_checkpoint.h"
#include "verilated.h"
#include "verilated_save.h"
#include "psx_checkpoint_host.h"

static constexpr uint64_t MAGIC=0x5053584348500001ULL;
static constexpr std::array<uint64_t,4> PERIOD={14762550,7381275,4920850,9312170};

struct Driver {
    uint64_t time=0, falling_edges=0, audio_frames=0, audio_nonzero=0;
    std::array<uint64_t,4> next=PERIOD;
    PsxCheckpointHost host;
    void save(const std::string& path,Vpsx_checkpoint& model) {
        VerilatedSave out; out.open(path.c_str());
        if(!out.isOpen()) throw std::runtime_error("Cannot write checkpoint");
        out<<MAGIC<<time<<falling_edges<<audio_frames<<audio_nonzero;
        for(auto value:next) out<<value;
        host.save(out); out<<model; out.close();
    }
    void restore(const std::string& path,Vpsx_checkpoint& model) {
        VerilatedRestore in; in.open(path.c_str());
        if(!in.isOpen()) throw std::runtime_error("Cannot read checkpoint");
        uint64_t magic=0; in>>magic;
        if(magic!=MAGIC) throw std::runtime_error("Checkpoint format mismatch");
        in>>time>>falling_edges>>audio_frames>>audio_nonzero;
        for(auto& value:next) in>>value;
        host.restore(in); in>>model; in.close();
    }
};

static std::string argument(int argc,char** argv,const std::string& key,const std::string& fallback="") {
    const std::string prefix="+"+key+"=";
    for(int i=1;i<argc;i++) if(std::string(argv[i]).find(prefix)==0) return argv[i]+prefix.size();
    return fallback;
}
static uint64_t number(int argc,char** argv,const std::string& key,uint64_t fallback) {
    return std::stoull(argument(argc,argv,key,std::to_string(fallback)));
}

int main(int argc,char** argv) {
    try {
        auto context=std::make_unique<VerilatedContext>();
        context->commandArgs(argc,argv);
        auto model=std::make_unique<Vpsx_checkpoint>(context.get());
        const uint64_t bound=number(argc,argv,"max_cycles",2000000);
        const uint64_t checkpoint_at=number(argc,argv,"checkpoint_at",0);
        const uint64_t capture_interval=number(argc,argv,"capture_interval",0);
        const auto restore_path=argument(argc,argv,"restore");
        const auto save_path=argument(argc,argv,"save","checkpoint.bin");
        const auto pad_path=argument(argc,argv,"pad_file");
        const bool diagnostic=number(argc,argv,"diagnostic",1)!=0;
        const auto disc_path=argument(argc,argv,"disc_file");
        Driver driver;
        if(!disc_path.empty()) driver.host.open(disc_path,argument(argc,argv,"disc_metadata"),
            number(argc,argv,"disc_sectors",0),number(argc,argv,"disc_index",0),number(argc,argv,"disc_delay",0));
        model->clk1x=model->clk2x=model->clk3x=model->clkvid=1;
        model->reset=1; model->diagnostic=diagnostic;
        model->hasCD=0; model->LIDopen=1; model->capture=0;
        model->eval();
        if(!restore_path.empty()) {
            driver.restore(restore_path,*model);
            context->time(driver.time);
            if(bool(model->diagnostic)!=diagnostic) throw std::runtime_error("Restored diagnostic mode differs");
        }
        const uint64_t start=model->cycles;
        const uint64_t audio_start=driver.audio_frames;
        std::ofstream audio("audio.pcm",std::ios::binary),trace("execution.csv"),pads("pad-events.csv");
        if(!audio || !trace || !pads) throw std::runtime_error("Cannot create capture outputs");
        bool saved=false;
        uint16_t previous_pad=model->pad_mask;
        while(!context->gotFinish() && model->cycles<=bound && !model->diagnostic_done) {
            driver.time=*std::min_element(driver.next.begin(),driver.next.end());
            const bool one=driver.next[0]==driver.time;
            const bool falling=one && model->clk1x;
            const bool rising=one && !model->clk1x;
            context->time(driver.time);
            model->capture=falling && (model->cycles==bound ||
                (capture_interval && model->cycles%capture_interval==0));
            model->capture_cycle=model->cycles;
            if(falling) {
                if(++driver.falling_edges==100) model->reset=0;
                if(!disc_path.empty()) driver.host.tick(model->reset_busy,model->cd_hps_req,model->cd_hps_lba);
                model->hasCD=driver.host.mounted; model->LIDopen=!driver.host.mounted;
                model->cd_hps_ack=driver.host.ack; model->cd_hps_write=driver.host.write;
                model->cd_hps_data=driver.host.data; model->trackinfo_addr=driver.host.track_addr;
                model->trackinfo_data=driver.host.track_data; model->trackinfo_write=driver.host.track_write;
                if(!pad_path.empty() && model->cycles%1000000==0) {
                    std::ifstream pad(pad_path); uint32_t value;
                    if(!(pad>>std::hex>>value) || value>65535) throw std::runtime_error("Invalid pad mask");
                    model->pad_mask=value;
                }
                if(model->pad_mask!=previous_pad) {
                    pads<<model->cycles<<','<<std::hex<<model->pad_mask<<std::dec<<'\n'; pads.flush();
                    previous_pad=model->pad_mask;
                }
            }
            if(rising) {
                if(model->cycles%768==0) {
                    const uint16_t left=model->sound_out_left,right=model->sound_out_right;
                    const char frame[4]={char(left),char(left>>8),char(right),char(right>>8)};
                    audio.write(frame,4); driver.audio_frames++;
                    if(left || right) driver.audio_nonzero++;
                }
                if(model->cycles<10000 || model->cycles%1000==0)
                    trace<<model->cycles<<','<<std::hex<<model->cpu_pc<<','<<model->cpu_opcode<<std::dec<<'\n';
                if(model->cycles%500000==0) {
                    std::cout<<"PROGRESS cycles="<<model->cycles<<" pc="<<std::hex<<model->cpu_pc<<std::dec
                             <<" sectors="<<driver.host.sectors<<"\n"<<std::flush;
                    audio.flush();trace.flush();
                }
            }
            for(unsigned i=0;i<4;i++) if(driver.next[i]==driver.time) {
                switch(i) {
                case 0:model->clk1x=!model->clk1x;break;
                case 1:model->clk2x=!model->clk2x;break;
                case 2:model->clk3x=!model->clk3x;break;
                case 3:model->clkvid=!model->clkvid;break;
                }
                driver.next[i]+=PERIOD[i];
            }
            model->eval();
            if(model->capture) {
                audio.flush();
                std::ofstream meta("snapshot-"+std::string(12-std::min<size_t>(12,std::to_string(model->capture_cycle).size()),'0')+
                                   std::to_string(model->capture_cycle)+".csv",std::ios::app);
                meta<<model->capture_cycle<<','<<model->DisplayWidth<<','<<model->DisplayHeight<<','
                    <<model->DisplayOffsetX<<','<<model->DisplayOffsetY<<','<<unsigned(model->display_rgb24)<<','
                    <<unsigned(model->display_disabled)<<','<<std::hex<<model->cpu_pc<<std::dec<<','
                    <<driver.host.sectors<<','<<driver.audio_frames<<','<<driver.audio_nonzero<<','
                    <<std::hex<<model->pad_mask<<std::dec<<','<<audio_start<<'\n';
            }
            // Persist only after the event and all combinational/NBA work settle.
            if(falling && checkpoint_at && model->cycles>=checkpoint_at && !saved) {
                driver.save(save_path,*model); saved=true;
                std::ofstream checkpoint_segment("checkpoint-segment.json");
                checkpoint_segment<<"{\"end_cycle\":"<<model->cycles<<",\"checks\":"<<model->checks
                                  <<",\"sectors\":"<<driver.host.sectors<<",\"audio_frames\":"<<driver.audio_frames
                                  <<",\"audio_nonzero\":"<<driver.audio_nonzero<<"}\n";
                std::cout<<"CHECKPOINT cycles="<<model->cycles<<" time="<<driver.time<<'\n';
                if(number(argc,argv,"stop_at_checkpoint",0)) break;
            }
            if(falling && model->cycles>=bound) break;
        }
        driver.save("final-state.bin",*model);
        std::ofstream result("segment.json");
        result<<"{\"start_cycle\":"<<start<<",\"audio_start_frame\":"<<audio_start<<",\"end_cycle\":"<<model->cycles
              <<",\"checks\":"<<model->checks<<",\"diagnostic_done\":"<<unsigned(model->diagnostic_done)
              <<",\"checkpoint_written\":"<<saved<<",\"sectors\":"<<driver.host.sectors
              <<",\"audio_frames\":"<<driver.audio_frames<<",\"audio_nonzero\":"<<driver.audio_nonzero<<"}\n";
        std::cout<<"STOP cycles="<<model->cycles<<" checks="<<model->checks<<" sectors="<<driver.host.sectors<<'\n';
        model->final();
        if(diagnostic && !model->diagnostic_done && !(saved && number(argc,argv,"stop_at_checkpoint",0))) return 2;
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<error.what()<<'\n';return 1;
    }
}
