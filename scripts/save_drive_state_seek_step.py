# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Routine step "drive-state-seek" for run_save_project_cpu.py: part 2 of prepare_save_drive_state_routine.py
(an idle drive with the motor on and a known position: Setloc + SeekL there) on a routine with part 1."""
import prepare_save_drive_state_routine as step

FAULTS = {k: step.FAULTS[k] for k in ('no-seek', 'no-wait')}
fault_text = step.fault_text


def firmware_text(text):
    return step.firmware_text(text, 'seek')
