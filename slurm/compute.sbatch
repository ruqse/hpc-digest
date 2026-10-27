#!/bin/bash
#SBATCH -J hpcd-compute
#SBATCH -t 00:10:00
#SBATCH -c 1
#SBATCH -o logs/%x_%j.log
#
# Weekly compute digest as a self-resubmitting Slurm chain. Submit it ONCE,
# from the repository root:
#
#   sbatch -A <account> --begin="$(date -d 'next Monday 09:00' +%FT%T)" slurm/compute.sbatch
#
# If you also run slurm/storage.sbatch, submit both with HPCD_WATCHDOG=1 in the
# environment. Each run then restarts the other chain when it has no queued job,
# which covers a job that dies before its first line runs (e.g. on a failing node).

NEXT_RUN="next Monday 09:00"
SIBLING_NAME="hpcd-storage"
SIBLING_SCRIPT="slurm/storage.sbatch"
SIBLING_NEXT="next Wednesday 08:30"

set -uo pipefail
cd "$SLURM_SUBMIT_DIR"

# Resubmit first, so the chain survives even if the report fails.
sbatch -A "$SLURM_JOB_ACCOUNT" --begin="$(date -d "$NEXT_RUN" +%FT%T)" slurm/compute.sbatch

# Watchdog. Does nothing if squeue itself fails, so it cannot create duplicates.
if [ "${HPCD_WATCHDOG:-0}" = 1 ]; then
    if q=$(squeue -h -u "$USER" -n "$SIBLING_NAME" -o %i) && [ -z "$q" ]; then
        echo "watchdog: no $SIBLING_NAME job queued, restarting it"
        sbatch -A "$SLURM_JOB_ACCOUNT" --begin="$(date -d "$SIBLING_NEXT" +%FT%T)" "$SIBLING_SCRIPT"
    fi
fi

[ -f slurm/env.sh ] && source slurm/env.sh
python3 -m hpc_digest compute --post --cleanup
