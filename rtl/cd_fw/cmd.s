; cd_subcpu firmware: command side (old cd_top command process 1136-2231).

; ------------------------------------------------------------------ helpers
; set/clear working (flag, shadow, ACK guard): r15 = 0/1
set_working:
    if r15 == 0
        bclr r11, r11, B_WORKING
        li r1, FL_WORK
        out FLAG_CLR, r1
        ; the old workDelay keeps counting, but its action needs working: drop the staged commit
        out C_WORK_STROBE, r0
        out C_WORK_FLAG, r0
    else
        bset r11, r11, B_WORKING
        li r1, 0x40000          ; FL_WORK << 16
        out NOW_STROBE, r1
    endif
fwflag_sync:                    ; fwflag: WORKING, ACKPAUSE (nextCmd 01 during a Pause work), DMOTOR unless tail
    li r1, FW_DMOTOR
    if bit r11, B_TAIL
        li r1, 0
    endif
    if bit r11, B_WORKING
        ori r1, r1, FW_WORKING
        if r13 == 1
            if r14 == 9
                ori r1, r1, FW_ACKPAUSE
            endif
        endif
    endif
    if r14 == 0x0A               ; initseek holds (old workDelay rule)
        if bit r11, B_TAIL
        else
            ori r1, r1, FW_WMOTOR
        endif
        if r10 == D_SEEKIMPL
            if bit r11, B_IMPL1X
                ori r1, r1, FW_WHOLD1
            endif
        endif
    endif
    out FWFLAG, r1
    ret

; load the WORK timer: r15 = value (0 = disarm)
work_load_aged:                         ; less the clocks since the event that caused the load (hardware)
    if r15 == 0
        b work_load
    endif
    ; less the clocks since the event (IN_AGE sampled 4 instructions before the write), -2 for the WORK commit clock
    in r1, IN_AGE
    sub r15, r15, r1
    addi r15, r15, AGED_K - 2
    bmi .wla_floor
    out T_WORK, r15
    bset r12, r12, B_WORKARMED
    ret
.wla_floor:
    li r15, 1
    out T_WORK, r15
    bset r12, r12, B_WORKARMED
    ret
work_load:
    if r15 == 0
        out T_WORK_OFF, r0
        bclr r12, r12, B_WORKARMED
    else
        out T_WORK, r15
        bset r12, r12, B_WORKARMED
    endif
    ret

; r15 = status shadow -> internalStatus
status_out:
    out P_internalStatus, r8
    ret

; paramCountNew for r1 = command -> r2
paramcount:
    li r2, 0
    if r1 == 2
        li r2, 3
    elif r1 == 0x0D
        li r2, 2
    elif r1 == 0x1D
        li r2, 2
    elif r1 == 0x0E
        li r2, 1
    elif r1 == 0x12
        li r2, 1
    elif r1 == 0x14
        li r2, 1
    elif r1 == 0x19
        li r2, 1
    endif
    ret

; conditions shared by the ACK table: r3 = bit mask
;  bit0 pausedData1x  bit1 settledPausedData  bit2 stoppedDataDrive  bit3 stoppedAt1x  bit4 nearPauseGetStat(r1=newCmd)
cond_bits:
    li r3, 0
    in r4, IN_hasCD
    in r5, IN_LIDopen
    if r10 == D_IDLE
        if bit r11, B_HDRVALID
            if bit r8, 1
                if bit r9, 7
                else
                    ori r3, r3, 1           ; pausedData1x
                endif
                if r4 != 0
                    if r5 == 0
                        in r6, IN_isAudio
                        if r6 == 0
                            if bit r11, B_WORKING
                            elif bit r12, B_WORKARMED
                            elif bit r11, B_TAIL
                            else
                                ori r3, r3, 2   ; settledPausedData
                            endif
                        endif
                    endif
                endif
            endif
        endif
        if r14 == 8
            if bit r12, B_WORKARMED
            elif bit r8, 1
            elif r4 == 0
            elif r5 != 0
            else
                in r6, IN_isAudioCD
                if r6 == 0
                    ori r3, r3, 4           ; stoppedDataDrive
                    if bit r9, 7
                    else
                        ori r3, r3, 8       ; stoppedAt1x
                    endif
                endif
            endif
        endif
    endif
    ; nearPauseGetStat: newCmd 01, working, workCommand 09, nextCmd 09, pausedData1x, no tail, disc, not audio,
    ; 0 < workDelay, not beyond the overlap (workDelay < 98304: bits 24:17 = 0 and not (16:15 = 11 and 14:13 /= 0))
    if r1 == 1
        if bit r11, B_WORKING
            if r14 == 9
                if r13 == 9
                    btst r3, 0
                    beq .np_no
                    if bit r11, B_TAIL
                    elif r4 == 0
                    elif r5 != 0
                    else
                        in r6, IN_isAudio
                        in r7, IN_TWORK
                        if r6 == 0
                            if bit r12, B_WORKARMED
                                li r6, 106496   ; first value beyond the overlap (0x1A000)
                                if r7 < r6
                                    ori r3, r3, 16
                                endif
                            endif
                        endif
                    endif
                endif
            endif
        endif
    endif
.np_no:
    ret

; ACK delay for r1 = newCmd (before the accept updates state) -> r2 (already less 2, as cmd_delay loads)
ack_value:
    call cond_bits
    in r4, IN_hasCD
    in r5, IN_LIDopen
    li r2, 48000
    if r10 == D_OPENING
        li r2, 15000
    elif r1 == 0x0A
        li r2, 69840
    elif r1 == 7
        btst r3, 2
        beq .av_x7
        in r6, IN_INSTANTSEEK
        if r6 != 0
            b .av_x7
        endif
        li r2, 70184
    elif r1 == 1
        b .av_getstat
    elif r1 == 6
        b .av_read
    elif r1 == 0x1B
        b .av_readn
    elif r1 == 9
        b .av_pause
    elif r1 == 0x1E
        btst r3, 0
        beq .av_done
        li r2, 68000
    elif r1 == 0x1A
        btst r3, 0
        beq .av_done
        if r14 == 9
            li r2, 28000
        elif r14 == 0x1A
            li r2, 35400
        endif
    elif r1 == 2
        li r2, 58000
        if bit r11, B_HDRVALID
            li r2, 39280
        endif
    elif r1 == 0x11
        if r10 == D_READING
            li r2, 36344
            if bit r9, 7
                li r2, 55872
            endif
        endif
    elif r1 == 0x0B
        li r2, 44400
        if bit r3, 1
            li r2, 27224
        elif bit r11, B_HDRVALID
            li r2, 31088
        endif
    elif r1 == 0x0C
        li r2, 46800
        if bit r3, 1
            li r2, 33432
        elif bit r11, B_HDRVALID
            li r2, 26728
        endif
    elif r1 == 0x0E
        li r2, 44200
        if bit r3, 1
            li r2, 30800
        elif bit r11, B_HDRVALID
            li r2, 25536
        endif
    elif r1 == 0x1C
        li r2, 120000
    endif
    b .av_done
.av_x7:                                     ; MotorOn without the R3 case: Init/GetStat rows below do not apply
    if r1 == 7
        li r2, 48000
    endif
    b .av_done
.av_read:                                   ; ReadN
    btst r3, 3
    beq .av_read2
    li r2, 75552                            ; stopped CD4
    b .av_done
.av_read2:
    if r10 == D_IDLE
        if bit r8, 1
            if bit r11, B_HDRVALID
                if r4 != 0
                    if r5 == 0
                        in r6, IN_isAudio
                        if r6 == 0
                            li r2, 33832
                            if bit r9, 7
                                li r2, 69584
                            endif
                            b .av_done
                        endif
                    endif
                endif
            endif
        endif
    endif
.av_readn:
    li r2, 43512
    if bit r9, 7
        li r2, 59968
    endif
    b .av_done
.av_pause:
    if bit r3, 0
        li r2, 29368
    elif bit r3, 1
        li r2, 40200
        if bit r9, 7
            li r2, 30568
        endif
    else
        li r2, 40200
    endif
    b .av_done
.av_getstat:
    if r10 == D_MOTORON
        if bit r11, B_WORKING
            if r14 == 7
                li r2, 22056
                b .av_done
            endif
        endif
    endif
    btst r3, 4
    beq .av_gs1
    li r2, 22300
    b .av_done
.av_gs1:
    if r13 == 0x0A
        if bit r11, B_WARMINIT
            if r4 != 0
                if r5 == 0
                    li r2, 27776
                    b .av_done
                endif
            endif
        endif
    endif
    if r13 == 9
        if bit r11, B_WORKING
            if bit r11, B_TAIL
                in r6, IN_TMOTOR
                li r7, 65536
                if r6 >= r7
                    if r4 != 0
                        if r5 == 0
                            li r2, 28976
                            b .av_done
                        endif
                    endif
                endif
            endif
        endif
    endif
    if bit r11, B_WORKING
        in r6, IN_TWORK
        li r7, 65536
        if r6 >= r7
            if r4 != 0
                if r5 == 0
                    in r6, IN_isAudio
                    if r6 == 0
                        if r13 == 8
                            if bit r11, B_COASTING
                                li r2, 24376
                                b .av_done
                            endif
                        elif r13 == 9
                            btst r3, 0
                            beq .av_gs2
                            if bit r11, B_TAIL
                            else
                                li r2, 24376
                                b .av_done
                            endif
                        endif
                    endif
                endif
            endif
        endif
    endif
.av_gs2:
    if bit r3, 1
        li r2, 26992
    elif bit r11, B_HDRVALID
        li r2, 33160
    else
        li r2, 39000
    endif
.av_done:
    addi r2, r2, -2
    ret

; ------------------------------------------------------------------ command accept (beginCommand)
cmd_accept:
    in r1, IN_newCmd
    bclr r11, r11, B_REJPAUSE
    out P_rejectedReadPause, r0
    if r1 != 1
        bclr r11, r11, B_WARMINIT
        bclr r11, r11, B_COASTING
    endif
    ; (Reel Fishing: a Setmode while Setloc is pending raises no event; the glue ignores it)
    ; a new command cancels the pending Pause INT2 report
    if r14 == 9
        if bit r11, B_WORKING
            bclr r11, r11, B_RPTPAUSE
        elif bit r12, B_WORKARMED
            bclr r11, r11, B_RPTPAUSE
        endif
    endif
    ; earlyWarmInitRead (CD2: ReadN before the seek bit appeared)
    bclr r11, r11, B_EARLYWARM
    if r1 == 6
        if bit r11, B_WARMINIT
            if r10 == D_SEEKIMPL
                if bit r11, B_WORKING
                    if r14 == 0x0A
                        if bit r11, B_SETLOC
                        else
                            in r3, IN_TWORK
                            li r4, INIT_SEEK_VISIBLE_WORK
                            ld r5, [r0+M_CURLBA]
                            li r6, 1153          ; PREGAP + 1003
                            in r7, IN_hasCD
                            if r3 >= r4
                                if r5 <= r6
                                    if r7 != 0
                                        in r7, IN_LIDopen
                                        if r7 == 0
                                            bset r11, r11, B_EARLYWARM
                                        endif
                                    endif
                                endif
                            endif
                        endif
                    endif
                endif
            endif
        endif
    endif
    ; this command's parameter bytes (the guest wrote them before the command byte)
    in r3, IN_cdrw_params
    li r4, M_PARAMS
    st [r4+0], r3
    ; parameter FIFO reset when the previous command took more parameters
    call paramcount
    if bit r11, B_PENDING
        ld r3, [r0+M_PARAMCNT]
        if r3 > r2
            out P_FifoParam_reset, r0
        endif
    endif
    st [r0+M_PARAMCNT], r2
    ; ACK delay (uses the state before this command)
    li r6, M_NEWCMD
    st [r6+0], r1
    call ack_value
    li r1, M_NEWCMD
    ld r1, [r1+0]
    call cond_bits                      ; r3 bit4 = nearPauseGetStat
    in r15, IN_BCAGE                    ; less the clocks the old cmd_delay counted since the command byte
    sub r2, r2, r15
    addi r2, r2, ACK_K
    bmi .ca_floor
    out T_ACK, r2
    b .ca_armed
.ca_floor:
    out T_ACK, r0
.ca_armed:
    bset r11, r11, B_ACKARMED
    bset r12, r12, B2_STREAM
    ; flags: pending + busy now (glue set them on the write), working <= nearPauseGetStat
    li r15, 0x30000                     ; (FL_BUSY | FL_PEND) << 16
    out NOW_STROBE, r15
    out P_BUSY_CLR, r0
    bset r11, r11, B_PENDING
    mov r13, r1
    li r15, 0
    if bit r3, 4
        li r15, 1
    endif
    call set_working
    if r10 == D_IDLE
        if bit r8, 1
            if r13 == 0x11
                bset r11, r11, B_PHYUPD
            endif
        endif
    endif
    ; fall through: stage the ACK commit

; ------------------------------------------------------------------ ACK staging (what handleCommand will commit)
; r5 = strobes, r6 = flag word (set 2:0, response reset 3, clear 7:4); error code in r7 (reason in r4)
cmd_stage:
    btst r11, B_ACKARMED
    beq .cs_ret
    btst r12, B2_STREAM
    beq .cs_nosq
    out SQ_CLR, r0
.cs_nosq:
    li r5, 0
    li r6, 0x90                       ; clear cmd_busy, parameter FIFO reset (old handleCommand)
    li r7, 0
    li r4, 0
    ; long responses keep the FIFO; everything else resets it on the commit clock
    mov r1, r13
    if r1 == 0x0F
    elif r1 == 0x10
    elif r1 == 0x11
    elif r1 == 0x13
    elif r1 == 0x14
    elif r1 == 0x19
    else
        ori r6, r6, FL_RESPRST
    endif
    in r2, IN_fifoParamCount
    li r3, M_PCOUNT
    st [r3+0], r2
    in r3, IN_setLocParamBad
    li r7, M_PBAD
    st [r7+0], r3
    li r7, 0
    ld r3, [r0+M_PARAMCNT]
    if r2 < r3
        li r7, 1
        li r4, 0x20
        ori r6, r6, 0x20               ; clear pending
        b .cs_commit
    endif
    in r2, IN_hasCD
    if r1 == 0x00
        li r4, 0x40
    elif r1 == 0x01
        li r5, S_cmdAck
    elif r1 == 0x02
        li r3, M_PBAD
        ld r3, [r3+0]
        li r5, S_cmdAck
        if r3 != 0
            li r5, 0
            li r4, 0x10
        endif
    elif r1 == 0x03
        li r5, S_cmdAck
        if r2 == 0
            li r5, 0
            li r4, 0x80
        endif
    elif r1 == 0x04
        b .cs_ffw
    elif r1 == 0x05
        b .cs_ffw
    elif r1 == 0x06
        b .cs_read
    elif r1 == 0x1B
        b .cs_read
    elif r1 == 0x07
        li r5, S_cmdAck
        if bit r8, 1
            li r5, 0
            li r4, 0x20
        elif bit r11, B_WORKING
        else
            ori r6, r6, FL_WORK
        endif
    elif r1 == 0x08
        li r5, S_cmdAck
        ori r6, r6, FL_WORK
    elif r1 == 0x09
        b .cs_pause
    elif r1 == 0x0A
        b .cs_init
    elif r1 == 0x0B
        li r5, S_cmdAck
    elif r1 == 0x0C
        li r5, S_cmdAck
    elif r1 == 0x0D
        li r5, S_cmdAck
    elif r1 == 0x0E
        li r5, S_cmdAck
    elif r1 == 0x0F
        li r5, S_cmdIRQ
        call sq_getparam_s
    elif r1 == 0x10
        if bit r11, B_HDRVALID
            li r5, S_cmdIRQ
            call sq_getlocl_s
        else
            li r4, 0x80
        endif
    elif r1 == 0x11
        li r5, S_cmdIRQ
        if r2 == 0
            li r5, 0
            li r4, 0x80
        else
            call sq_getlocp_s
        endif
    elif r1 == 0x12
        li r5, S_cmdAck
        if r2 == 0
            li r5, 0
            li r4, 0x80
        elif r10 == D_READING
            li r5, 0
            li r4, 0x80
        elif r10 == D_PLAYING
            li r5, 0
            li r4, 0x80
        endif
    elif r1 == 0x13
        li r5, S_cmdIRQ
        if r2 == 0
            li r5, 0
            li r4, 0x80
        else
            call sq_gettn_s
        endif
    elif r1 == 0x14
        b .cs_gettd
    elif r1 == 0x15
        li r5, S_cmdAck
    elif r1 == 0x16
        li r5, S_cmdAck
    elif r1 == 0x19
        b .cs_test
    elif r1 == 0x1A
        li r5, S_cmdAck
        if r2 == 0
            li r5, 0
            li r4, 0x80
        else
            ori r6, r6, FL_WORK
        endif
    elif r1 == 0x1E
        li r5, S_cmdAck
        if r2 == 0
            li r5, 0
            li r4, 0x80
        else
            ori r6, r6, FL_WORK
        endif
    else
        li r4, 0x40
    endif
.cs_clrpend:
    ori r6, r6, 0x20
.cs_commit:                           ; r4 /= 0: error response with reason r4
    if r4 != 0
        li r5, S_errCmd
        li r3, 1
        out P_cdrw_err_cmd, r3
        out P_cdrw_rsn_cmd, r4
    endif
    ; GetStat byte modifiers (old response mux, resp_sel 1 with cmdAck)
    li r2, 0
    li r3, 0
    if r13 == 1
        if bit r11, B_WARMINIT
            if r10 == D_SEEKIMPL
                in r1, IN_TWORK
                li r4, INIT_SEEK_VISIBLE_WORK
                if r1 < r4
                    bset r2, r2, 6
                endif
            endif
        endif
        if bit r11, B_TAIL
            bset r2, r2, 5
        endif
    endif
    ; Stop sets stopReadCoasting on its own ACK clock (1x data read, motor on)
    li r1, 0
    if bit r11, B_COASTING
        li r1, 1
    elif r13 == 8
        if bit r9, 7
        elif bit r8, 5
            if bit r8, 1
                if r10 == D_READING
                    in r4, IN_LIDopen
                    if r4 == 0
                        li r1, 1
                    endif
                endif
            endif
        endif
    endif
    if r1 != 0
        bclr r2, r2, 5
        bset r3, r3, 5
        bset r2, r2, 1
    endif
    out P_cdrw_st_or, r2
    out P_cdrw_st_clr, r3
    li r2, 0                           ; parameter FIFO reset: Setloc reads three bytes first, Setfilter two
    if r13 == 2
        li r2, 5
    elif r13 == 0x0D
        li r2, 3
    endif
    out PRM_DLY, r2
    out C_ACK_STROBE, r5
    out C_ACK_FLAG, r6
    out ACK_FRESH, r0
    bclr r12, r12, B2_STREAM
.cs_ret:
    ret
.cs_ffw:                               ; Forward/Backward: no pending clear on the error
    if r10 == D_PLAYING
        li r5, S_cmdAck
        b .cs_clrpend
    endif
    li r4, 0x80
    b .cs_commit
.cs_read:
    li r5, S_cmdAck
    if r2 == 0
        li r5, 0
        li r4, 0x80
    else
        in r3, IN_isAudioCD
        if r3 != 0
            if bit r9, 0
            else
                li r5, 0
                li r4, 0x40
            endif
        endif
    endif
    b .cs_clrpend
.cs_pause:                             ; rejected while seeking or before the first sector
    li r3, 0
    if r10 == D_SEEKLOG
        li r3, 1
    elif r10 == D_SEEKPHYS
        li r3, 1
    elif r10 == D_READING
        btst r8, 6
        beq .cs_p2
        li r3, 1
    elif r10 == D_PLAYING
        btst r8, 6
        beq .cs_p2
        li r3, 1
    endif
.cs_p2:
    if r3 != 0
        li r4, 0x80
        li r3, 1
        if bit r9, 7
            li r3, 0
        endif
        out P_cdrw_mask_seek, r3
        b .cs_clrpend
    endif
    out P_cdrw_mask_seek, r0
    li r5, S_cmdAck
    ori r6, r6, FL_WORK
    b .cs_clrpend
.cs_init:
    if bit r11, B_WORKING
        if r14 == 0x0A
            b .cs_clrpend              ; second pass: only the pending flag
        endif
    endif
    li r5, S_cmdAck
    li r6, 0x8C                        ; keeps cmd_busy (Init answers twice): FIFO resets + working
    b .cs_commit
.cs_gettd:
    li r5, S_cmdIRQ
    if r2 == 0
        li r5, 0
        li r4, 0x80
    else
        call param0
        in r2, IN_trackcountBCD
        if r3 > r2
            li r5, 0
            li r4, 0x10
        else
            call sq_gettd_s
        endif
    endif
    b .cs_clrpend
.cs_test:
    call param0
    if r3 == 4
    elif r3 == 5
    elif r3 == 0x20
    elif r3 == 0x22
    else
        li r4, 0x40
        b .cs_clrpend
    endif
    li r5, S_cmdIRQ
    call sq_test_s
    b .cs_clrpend

; ------------------------------------------------------------------ long responses (streamer, ACK counts N..1)
; sq_lit r15: push literal byte r15;  sq_src r15: push source code r15 (low 6 bits) with no literal
sq_lit:
    out SQ_PUSH, r15
    ret
sq_src:
    shl4 r15, r15
    shl4 r15, r15
    out SQ_PUSH, r15
    ret
sq_skip:
    li r15, 63
    b sq_src
sq_arm_ack:                            ; streamer on the ACK counts, FIFO reset one count earlier
    li r15, 0x21                       ; mode 01, timer ACK, reset-at
    out SQ_ARM, r15
    ret
sq_getparam:                           ; status, mode, 0, file, channel at counts 5..1 (reset at 6)
    mov r15, r8
    call sq_lit
    mov r15, r9
    call sq_lit
    li r15, 0
    call sq_lit
    ld r15, [r0+M_XAFILE]
    call sq_lit
    ld r15, [r0+M_XACHAN]
    call sq_lit
    b sq_arm_ack
sq_getlocl:                            ; header 0..3, subheader 0..3 at counts 8..1 (reset at 9)
    li r1, SRC_HDR0
.gl_loop:
    mov r15, r1
    call sq_src
    addi r1, r1, 1
    if r1 < 9
        b .gl_loop
    endif
    b sq_arm_ack
sq_getlocp:                            ; subdata 1,2,3,4,5,7,8,9 at counts 8..1, from the subdata shadow: subdata changes
    li r1, M_SUBD + 1                  ; only on copy strobes the firmware issues, and a drive event restages the ACK
.gp_loop:
    ld r15, [r1+0]
    call sq_lit
    addi r1, r1, 1
    if r1 == M_SUBD + 6
        addi r1, r1, 1
    endif
    if r1 < M_SUBD + 10
        b .gp_loop
    endif
    b sq_arm_ack
sq_gettn:                              ; status, 01, trackcount at counts 3..1 (reset at 4)
    mov r15, r8
    call sq_lit
    li r15, 1
    call sq_lit
    li r15, SRC_TRACKCNT
    call sq_src
    b sq_arm_ack
sq_gettd:                              ; status, minutes, seconds (track 0: disc total)
    mov r15, r8
    call sq_lit
    call param0
    li r15, SRC_TOTMIN
    if r3 != 0
        li r15, SRC_TRKMIN
    endif
    call sq_src
    li r15, SRC_TOTSEC
    if r3 != 0
        li r15, SRC_TRKSEC
    endif
    call sq_src
    b sq_arm_ack
sq_test:                               ; ten counts (reset at 11), leading counts skipped
    call param0
    li r2, test_tab
    if r3 == 4
        li r1, 9                       ; one byte: status
        call .st_skips
        mov r15, r8
        call sq_lit
        b sq_arm_ack
    elif r3 == 5
        li r1, 7
        call .st_skips
        mov r15, r8
        call sq_lit
        li r15, 0
        call sq_lit
        li r15, 0
        call sq_lit
        b sq_arm_ack
    elif r3 == 0x20
        li r1, 6
        addi r2, r2, 0
    else
        in r4, IN_region
        if r4 == 0
            li r1, 3
            addi r2, r2, 4
        elif r4 == 1
            li r1, 1
            addi r2, r2, 11
        elif r4 == 2
            li r1, 0
            addi r2, r2, 20
        else
            li r1, 10                  ; region 3: no bytes
            call .st_skips
            b sq_arm_ack
        endif
    endif
    call .st_skips
    li r4, 10
    sub r4, r4, r1                     ; bytes to copy
.st_copy:
    ldt r15, [r2+0]
    call sq_lit
    addi r2, r2, 1
    addi r4, r4, -1
    if r4 != 0
        b .st_copy
    endif
    b sq_arm_ack
.st_skips:                             ; push r1 skip entries
    if r1 != 0
        mov r3, r1
.st_sk:
        call sq_skip
        addi r3, r3, -1
        if r3 != 0
            b .st_sk
        endif
    endif
    ret
test_tab:
    .word 0x95, 0x05, 0x16, 0xC1
    .word 0x66, 0x6F, 0x72, 0x20, 0x55, 0x2F, 0x43
    .word 0x66, 0x6F, 0x72, 0x20, 0x4A, 0x61, 0x70, 0x61, 0x6E
    .word 0x66, 0x6F, 0x72, 0x20, 0x45, 0x75, 0x72, 0x6F, 0x70, 0x65

; ------------------------------------------------------------------ ACK fired: the command's own effects (handleCommand)
cmd_exec:
    bclr r11, r11, B_ACKARMED
    mov r1, r13
    li r2, M_PCOUNT
    ld r2, [r2+0]
    ld r3, [r0+M_PARAMCNT]
    if r2 < r3
        bclr r11, r11, B_PENDING
        ret
    endif
    in r2, IN_hasCD
    ; pending clears with every case except Forward/Backward errors and Init's first pass
    bclr r11, r11, B_PENDING
    if r1 == 0x01
        if r2 != 0
            if r10 != D_OPENING
                bclr r8, r8, 4          ; shell_close (the glue owns the status bit)
                out P_SHELL_CLR, r0
            endif
        endif
    elif r1 == 0x02
        b ce_setloc
    elif r1 == 0x03
        if r2 != 0
            b ce_play
        endif
    elif r1 == 0x04
        b ce_ffw
    elif r1 == 0x05
        b ce_ffw
    elif r1 == 0x06
        b ce_read
    elif r1 == 0x1B
        b ce_read
    elif r1 == 0x07
        b ce_motoron
    elif r1 == 0x08
        b ce_stop
    elif r1 == 0x09
        b ce_pause
    elif r1 == 0x0A
        b ce_init
    elif r1 == 0x0B
        bset r11, r11, B_MUTED
        li r3, 1
        out P_muted, r3
    elif r1 == 0x0C
        bclr r11, r11, B_MUTED
        out P_muted, r0
    elif r1 == 0x0D
        b ce_setfilter
    elif r1 == 0x0E
        call param0
        b drv_setmode
    elif r1 == 0x12
        b ce_session
    elif r1 == 0x15
        b ce_seek
    elif r1 == 0x16
        b ce_seek
    elif r1 == 0x19
        call param0
        if r3 == 4
            bset r8, r8, 1              ; startMotorCMD: motor on
            call status_out
        endif
    elif r1 == 0x1A
        if r2 != 0
            li r15, 18942               ; GetID second response (psx-spx 0x4a00)
            mov r14, r1
            b ce_getid
        endif
    elif r1 == 0x1E
        if r2 != 0
            li r15, 26379998            ; ReadTOC: driveAck when WORK expires (staged like GetID's)
            mov r14, r1
            bset r11, r11, B_WORKING
            call work_load_aged
            call work_stage
            b fwflag_sync
        endif
    endif
    ret

param0:                                ; r3 = first parameter byte (captured at accept)
    li r3, M_PARAMS
    ld r3, [r3+0]
    mov r2, r3
    andi r3, r3, 0x7F
    btst r2, 7
    beq .p0_r
    bset r3, r3, 7
.p0_r:
    ret

; long-response lists are built only when B2_STREAM is set (at accept, or after a GetLocP position update)
sq_getparam_s:
    btst r12, B2_STREAM
    bne sq_getparam
    ret
sq_getlocl_s:
    btst r12, B2_STREAM
    bne sq_getlocl
    ret
sq_getlocp_s:
    btst r12, B2_STREAM
    bne sq_getlocp
    ret
sq_gettn_s:
    btst r12, B2_STREAM
    bne sq_gettn
    ret
sq_gettd_s:
    btst r12, B2_STREAM
    bne sq_gettd
    ret
sq_test_s:
    btst r12, B2_STREAM
    bne sq_test
    ret
