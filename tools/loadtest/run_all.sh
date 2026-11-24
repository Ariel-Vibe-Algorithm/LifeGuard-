#!/bin/sh
# Full reproducible chain (Git Bash): ./tools/loadtest/run_all.sh   (from the repo root or this folder)
cd "$(dirname "$0")"
PY=../../.venv/Scripts/python.exe
$PY bench_baseline.py --reps 2 --seconds 5 --levels 1,16,64 --only circle,incidents,location,messages,get --synchronous NORMAL --indexes --tag quiet_fix_normal_idx > results/quiet_fix_normal_idx.log 2>&1
$PY bench_storm.py --reps 2 --duration 120 --tag quiet_storm > results/quiet_storm.log 2>&1
$PY bench_storm.py --reps 1 --duration 120 --no-move --tag quiet_storm_nomove > results/quiet_storm_nomove.log 2>&1
$PY bench_storm.py --reps 1 --duration 120 --synchronous NORMAL --indexes --tag quiet_storm_fix > results/quiet_storm_fix.log 2>&1
$PY bench_idle.py --levels 100,300,1000,2000 --mode longpoll > results/quiet_idle_longpoll.log 2>&1
$PY bench_idle.py --levels 100,300,1000 --mode sse > results/quiet_idle_sse.log 2>&1
$PY profile_hotpaths.py > results/profile_hotpaths.log 2>&1
$PY sec_pocs.py queued_leak ring_stall vol_identity escort chat sessions lockout register sybil quick_cancel spam streams osrm push invites walks > results/sec_pocs.log 2>&1
echo CHAIN_DONE
