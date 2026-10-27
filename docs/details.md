# hpc-digest: details

Reference material for setting up and running hpc-digest. Start with the
[README](../README.md).

## Requirements, in full

- Python 3.9 or newer with `matplotlib`, `numpy` and `slack_sdk`, plus `tomli`
  on Python < 3.11 (`pip install -r requirements.txt` handles this).
- **compute**: Slurm accounting, read with `sreport`.
- **storage**: CephFS with quotas set on the project directories. The report
  reads CephFS's recursive statistics (`ceph.dir.rentries`, `ceph.dir.rbytes`)
  and quota attributes (`ceph.quota.max_files`, `ceph.quota.max_bytes`), so it
  never walks the files themselves.
- Slack: a bot token with the `files:write` and `chat:write` scopes, invited to
  the target channel. `--delete-ts` also needs `channels:history` (or
  `groups:history` for a private channel) to remove the old post's file.

## How the numbers are computed

**Compute.** `sreport cluster AccountUtilizationByUser -t Hour` for each
account, from the 1st of the month up to today (`sreport` treats the end date
as exclusive, so today is not included). "Expected" is the share of the month
elapsed; the pace line turns red when usage is ahead of it.

**Storage.**
- *Files* counts entries (files and directories), as CephFS quotas do.
- Per-owner shares: the scan goes down from the project directory, through
  directories owned by `skip_owners` (default `root`), to the first directory
  owned by someone else, and credits that directory's whole subtree to its
  owner. Nothing is counted twice.
- Entries sitting directly in `skip_owners` directories are not credited to
  anyone. The QA flags it if they exceed 1% of the project.
- Byte totals come from `ceph.dir.rbytes`, which sums file sizes. Sparse files
  therefore count at their full size.

## QA before posting

The storage report checks every number it draws before it will post: that each
project has a quota gauge, that drawn percentages and status words match
used/limit, and that every donut slice equals that owner's total and the slices
sum to the attributed total. A failure blocks `--post` (override with
`--force`); `--strict-qa` also makes it exit non-zero. Results go to
`storage_qa_<date>.txt` in the output directory.

## Options

Both commands take `--config`, `--post`, `--channel`, `--force`,
`--delete-ts`, `--cleanup` and `--out`; run `python3 -m hpc_digest storage --help`.

- `--cleanup` deletes the report's local files, but only after a successful post.
- `--delete-ts <ts>` replaces an earlier post. The new chart is posted first and
  the old one deleted after, so the channel is never left empty. The ts is the
  number at the end of the message link: `.../p1781075218221099` becomes
  `1781075218.221099`.

## Scheduling

- **Check the chains:** `squeue -u "$USER" -n hpcd-compute,hpcd-storage` should
  show one pending job each.
- **Change the day or time:** edit `NEXT_RUN` at the top of the sbatch script
  (any `date -d` expression). With the watchdog on, also update `SIBLING_NEXT`
  in the other script.
- **Logs** go to `logs/`; charts, QA reports and per-owner CSVs go to
  `[output].dir`.
- **The watchdog** (`HPCD_WATCHDOG=1`) runs after each job's own resubmit: if
  the other report has no job queued, it submits one. It does nothing if
  `squeue` itself fails, so it cannot create duplicate jobs.
- **To stop a chain,** `scancel` its pending job. With the watchdog on, cancel
  both chains, or the remaining one will restart the other.

## Troubleshooting

- **No pending job (chain broken).** Look for the last job with
  `sacct -u $USER --name=hpcd-storage`. A `TIMEOUT` with no `logs/` file means
  the job never started its script; `sacctmgr show event nodes=<node>` will often
  show that node going down. Turn on the watchdog and resubmit.
- **"no quota found" on a gauge.** The path has no readable `ceph.quota.*`
  attributes (the quota may sit on a parent directory). Point `path` at the
  directory that carries the quota, or set `max_files` / `max_tib` in the config.
- **`not_in_channel` from Slack.** Invite the bot to the channel.
