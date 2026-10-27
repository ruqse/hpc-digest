# hpc-digest

Weekly digests of a research group's compute and storage use on an HPC cluster,
posted to Slack as one chart each.

- **compute**: month-to-date core-hours per user for each Slurm account, with
  used vs allocated and whether usage is ahead of the month's pace.
- **storage**: CephFS quota gauges (files and bytes) for each project, plus who
  holds the files and who holds the bytes. The two often differ a lot.

Both run as self-resubmitting Slurm jobs, need no admin rights, and keep each
person's colour the same across weeks and across both charts.

| Compute | Storage |
|---|---|
| ![compute example](examples/compute.png) | ![storage example](examples/storage.png) |

*Charts made from fake data by `examples/make_examples.py`.*

## Requirements

- Python 3.9 or newer with `matplotlib`, `numpy`, `slack_sdk` (and `tomli` on
  Python < 3.11): `pip install -r requirements.txt`
- **compute**: Slurm accounting, read with `sreport`.
- **storage**: a CephFS file system with quotas set on the project directories.
  The report reads CephFS's own recursive statistics (`ceph.dir.rentries`,
  `ceph.dir.rbytes`) and quota attributes (`ceph.quota.max_files`,
  `ceph.quota.max_bytes`), so it never walks millions of files and takes about a
  second per project.
- A Slack app with a bot token that has the `files:write` and `chat:write`
  scopes, invited to the target channel. `--delete-ts` also needs
  `channels:history` (or `groups:history` for a private channel) to remove the
  old post's file.

## Quick start

```bash
git clone <this repo> hpc-digest && cd hpc-digest
pip install -r requirements.txt
cp config.example.toml config.toml        # then edit: channel, accounts, project paths

# The bot token: an environment variable, or a private file named in config.toml
# (read -s keeps it out of your shell history)
mkdir -p ~/.config/hpc-digest
( umask 077; read -rsp 'Slack bot token: ' t && echo "$t" > ~/.config/hpc-digest/slack_token )

# Render and check locally. Nothing is posted without --post.
python3 -m hpc_digest storage
python3 -m hpc_digest compute

# Post to a test channel first
python3 -m hpc_digest storage --post --channel C0TESTCHANNEL
```

Charts, QA reports and per-owner CSVs go to `[output].dir`.

## Scheduling with Slurm

Each `slurm/*.sbatch` script resubmits itself for the following week before it
does anything else, so one submission starts a weekly chain. Run them from the
repository root. If your cluster needs a module or a virtual environment for
Python, copy `slurm/env.example.sh` to `slurm/env.sh` and set it up there.

```bash
export HPCD_WATCHDOG=1     # only if you run both reports; see below
sbatch -A <account> --begin="$(date -d 'next Monday 09:00' +%FT%T)"    slurm/compute.sbatch
sbatch -A <account> --begin="$(date -d 'next Wednesday 08:30' +%FT%T)" slurm/storage.sbatch
squeue -u "$USER" -n hpcd-compute,hpcd-storage     # one pending job each
```

To change the day or time, edit `NEXT_RUN` at the top of the script (any
`date -d` expression). Logs go to `logs/`.

**Why the watchdog.** A self-resubmitting chain breaks if a job dies before its
first line runs, for example when it lands on a node that is failing: no log, no
resubmission, and the report silently stops. With `HPCD_WATCHDOG=1`, each report
checks that the other one has a job queued and restarts it if not, so a dead
node costs at most one week. The check does nothing if `squeue` itself fails, so
it cannot create duplicate jobs.

**To stop a chain**, `scancel` its pending job. With the watchdog on, cancel
both chains, or the remaining one will restart the other.

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
sum to the attributed total. A failure blocks `--post` (override with `--force`);
`--strict-qa` also makes it exit non-zero. Results go to
`storage_qa_<date>.txt`.

## Options

Both commands take `--config`, `--post`, `--channel`, `--force`,
`--delete-ts`, `--cleanup` and `--out`; run `python3 -m hpc_digest storage --help`.

- `--cleanup` deletes the report's local files, but only after a successful post.
- `--delete-ts <ts>` replaces an earlier post. The new chart is posted first and
  the old one deleted after, so the channel is never left empty. The ts is the
  number at the end of the message link: `.../p1781075218221099` becomes
  `1781075218.221099`.

## Troubleshooting

- **No pending job (chain broken).** Look for the last job with
  `sacct -u $USER --name=hpcd-storage`. A `TIMEOUT` with no `logs/` file means
  the job never started its script; `sacctmgr show event nodes=<node>` will often
  show that node going down. Turn on the watchdog and resubmit.
- **"no quota found" on a gauge.** The path has no readable
  `ceph.quota.*` attributes (the quota may sit on a parent directory). Point
  `path` at the directory that carries the quota, or set `max_files` / `max_tib`
  in the config.
- **`not_in_channel` from Slack.** Invite the bot to the channel.

## License

MIT, see [LICENSE](LICENSE).
