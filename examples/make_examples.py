"""Render the README example charts from made-up data (no cluster needed).

    python3 examples/make_examples.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hpc_digest import compute, storage  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TIB = storage.BYTES_PER_TIB


def storage_project(label, entries, max_files, tib, max_tib, owners):
    """owners: {login: (entries, TiB)}; a few entries stay unattributed, as in a real scan."""
    files = {o: n for o, (n, _) in owners.items()}
    nbytes = {o: int(t * TIB) for o, (_, t) in owners.items()}
    return {"label": label, "path": f"/cephfs/proj/{label}",
            "entries": entries, "bytes": int(tib * TIB),
            "max_files": max_files, "max_bytes": int(max_tib * TIB), "rows": [],
            "owners_files": files, "owners_bytes": nbytes,
            "attributed_files": sum(files.values()),
            "attributed_bytes": sum(nbytes.values())}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        registry = os.path.join(tmp, "user_colors.json")

        compute.create_chart(
            [{"label": "Genomics", "monthly_core_hours": 8000,
              "users": {"alice": 2900, "bob": 1450, "carol": 760, "dave": 310, "erin": 90}},
             {"label": "Imaging", "monthly_core_hours": 12000,
              "users": {"bob": 4100, "frank": 2300, "grace": 1700, "heidi": 520}}],
            "Month-to-date compute usage", "2026-10-01", "2026-10-21",
            progress=67.7, registry_path=registry,
            output_path=os.path.join(HERE, "compute.png"))

        projects = [
            storage_project("genomics", 1_712_000, 2_000_000, 11.4, 30,
                            {"alice": (905_000, 3.1), "bob": (412_000, 5.2),
                             "carol": (260_000, 1.9), "dave": (98_000, 0.8),
                             "erin": (36_500, 0.4)}),
            storage_project("imaging", 1_150_000, 5_000_000, 41.2, 48,
                            {"frank": (520_000, 22.5), "grace": (388_000, 12.1),
                             "bob": (180_000, 4.6), "heidi": (61_800, 2.0)}),
        ]
        drawn = storage.create_chart(projects, "Storage & inode quota", "2026-10-21",
                                     registry, os.path.join(HERE, "storage.png"))
        ok, lines = storage.qa_checks(drawn)
        print(f"storage example QA: {'PASS' if ok else 'FAIL'}")
        for status, msg in lines:
            if status != "PASS":
                print(f"  [{status}] {msg}")
    print("Wrote examples/compute.png and examples/storage.png")


if __name__ == "__main__":
    main()
