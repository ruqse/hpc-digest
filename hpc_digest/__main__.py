"""python3 -m hpc_digest {compute,storage} [options]"""

import sys

USAGE = """usage: python3 -m hpc_digest {compute,storage} [options]

  compute   month-to-date core-hours per user, per Slurm account (sreport)
  storage   CephFS quota gauges and per-owner files/bytes, with QA

Add --help after a command for its options. Nothing is posted without --post."""


def main():
    cmd, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else (None, [])
    if cmd == "compute":
        from .compute import main as run
    elif cmd == "storage":
        from .storage import main as run
    else:
        print(USAGE)
        sys.exit(0 if cmd in ("-h", "--help") else 2)
    run(rest)


if __name__ == "__main__":
    main()
