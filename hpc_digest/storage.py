"""Storage and inode quota per CephFS project, with per-owner breakdowns.

Everything is read from CephFS's own recursive statistics, so no tree walk over
millions of files is needed:

    ceph.dir.rentries / ceph.dir.rbytes      usage under a directory
    ceph.quota.max_files / ceph.quota.max_bytes  the project's quota

"Files" means entries (files + directories), which is what CephFS quotas count.
Per-owner shares come from the first directory on each branch whose owner is
not in [storage].skip_owners (e.g. root): that directory's whole subtree is
credited to its owner, so nothing is counted twice.

A QA pass cross-checks every drawn number before the chart can be posted.
"""

import argparse
import csv
import os
import pwd
import re
import sys
from datetime import date

import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from . import common
from .common import GREEN, ORANGE, RED, DARKRED

BYTES_PER_GIB = 1024 ** 3
BYTES_PER_TIB = 1024 ** 4

ARTIFACT_GLOBS = ["storage_*.png", "storage_qa_*.txt", "storage_owners_*.csv"]


# ------------------------------- helpers -----------------------------------
def band_info(pct):
    """(colour, status word). Colour and word together, so the signal survives
    colour blindness and greyscale."""
    if pct > 100:
        return DARKRED, "OVER"
    if pct >= 95:
        return RED, "NEAR LIMIT"
    if pct >= 80:
        return ORANGE, "WATCH"
    return GREEN, "OK"


def commafy(n):
    return f"{int(round(n)):,}"


def fmt_bytes(nbytes):
    tib = nbytes / BYTES_PER_TIB
    return f"{tib:,.1f} TiB" if tib >= 1 else f"{nbytes / BYTES_PER_GIB:,.0f} GiB"


def slugify(label):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or "project"


# ------------------------------- the scan ----------------------------------
def xattr_int(path, name):
    """Integer CephFS virtual xattr, or None if absent/unreadable."""
    try:
        return int(os.getxattr(path, name))
    except (OSError, ValueError):
        return None


def owner_of(path):
    uid = os.stat(path).st_uid
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def attribute(root, skip_owners, max_depth):
    """[(owner, entries, bytes, path)] for each owner's subtree roots: the first
    directory on each branch not owned by a skip_owners user. Symlinks are not
    followed. Entries in skip_owners-owned directories stay unattributed."""
    rows = []

    def walk(path, depth):
        try:
            owner = owner_of(path)
        except OSError as exc:
            print(f"WARNING: cannot stat {path}: {exc}")
            return
        if owner not in skip_owners:
            rows.append((owner, xattr_int(path, "ceph.dir.rentries") or 0,
                         xattr_int(path, "ceph.dir.rbytes") or 0, path))
            return
        if depth >= max_depth:
            return
        try:
            children = sorted(e.path for e in os.scandir(path)
                              if e.is_dir(follow_symlinks=False))
        except OSError as exc:
            print(f"WARNING: cannot list {path}: {exc}")
            return
        for child in children:
            walk(child, depth + 1)

    walk(root.rstrip("/") or "/", 0)
    return rows


def scan_project(proj, skip_owners, max_depth):
    """Usage, limits and per-owner attribution for one configured project."""
    path = proj["path"]
    rows = attribute(path, skip_owners, max_depth)
    # Read the totals right after the walk so the two agree as closely as possible.
    entries = xattr_int(path, "ceph.dir.rentries")
    nbytes = xattr_int(path, "ceph.dir.rbytes")
    # A quota of 0 or a missing xattr means "no quota here": fall back to config.
    max_files = xattr_int(path, "ceph.quota.max_files") or proj.get("max_files")
    max_bytes = xattr_int(path, "ceph.quota.max_bytes") or (
        int(proj["max_tib"] * BYTES_PER_TIB) if proj.get("max_tib") else None)

    owners_files, owners_bytes = {}, {}
    for owner, n, b, _ in rows:
        owners_files[owner] = owners_files.get(owner, 0) + n
        owners_bytes[owner] = owners_bytes.get(owner, 0) + b
    return {
        "label": proj.get("label", path), "path": path,
        "entries": entries, "bytes": nbytes, "max_files": max_files, "max_bytes": max_bytes,
        "rows": rows, "owners_files": owners_files, "owners_bytes": owners_bytes,
        "attributed_files": sum(owners_files.values()),
        "attributed_bytes": sum(owners_bytes.values()),
    }


def write_owner_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["owner", "entries", "bytes", "path"])
        for row in sorted(rows, key=lambda r: r[1], reverse=True):
            w.writerow(row)


# ------------------------------- the figure --------------------------------
def draw_gauge(ax, header, metrics):
    """Files + Storage bars. Labels sit right of the quota line so they never
    overlap the bars. metrics: [(name, used, limit, value_label)], top first.
    Returns {name: {"pct", "status"}} for QA."""
    n = len(metrics)
    facts = {}
    for i, (name, used, limit, vlabel) in enumerate(metrics):
        y = n - 1 - i
        pct = used / limit * 100
        color, status = band_info(pct)
        facts[name] = {"pct": pct, "status": status}
        ax.barh(y, 1.0, height=0.5, color="#ECEFF1", edgecolor="#CFD8DC", zorder=1)
        ax.barh(y, min(pct / 100.0, 1.0), height=0.5, color=color, zorder=2)
        if pct > 100:                            # hatched overflow nub past the line
            ax.barh(y, min((pct - 100) / 100.0, 0.12), left=1.0, height=0.5,
                    color=color, hatch="////", edgecolor="white", zorder=2)
        ax.text(1.06, y, f"{pct:.0f}%   {vlabel}   · {status} ·", va="center",
                ha="left", fontsize=12.5, fontweight="bold", color=color)

    ax.axvline(1.0, color="#37474F", lw=1.5, ls="--", zorder=3)
    ax.set_xlim(0, 1.78)
    ax.set_ylim(-0.6, n - 0.4)
    ax.set_yticks(range(n))
    ax.set_yticklabels([m[0] for m in reversed(metrics)], fontsize=12, fontweight="bold")
    ax.set_xticks([0, 0.5, 1.0])
    ax.set_xticklabels(["0%", "50%", "quota"], fontsize=9)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.set_title(header, fontsize=16, fontweight="bold", pad=10)
    return facts


def create_chart(projects, title, snapshot, registry_path, output_path):
    """Render the figure; returns what was drawn, for QA."""
    n = len(projects)
    all_owners = sorted({o for p in projects
                         for o in set(p["owners_files"]) | set(p["owners_bytes"])})
    color_map = common.assign_user_colors(all_owners, registry_path)

    # rows: 0 = gauges, 1 = files by owner, 2 = storage by owner
    fig = plt.figure(figsize=(11 * n, 16))
    gs = fig.add_gridspec(3, n, height_ratios=[0.9, 2.0, 2.0], hspace=0.40,
                          wspace=0.30, top=0.885, bottom=0.085, left=0.07, right=0.95)

    drawn = {"projects": [], "color_map": {k: tuple(v) for k, v in color_map.items()}}
    alerts = []  # (pct, label, metric, status)

    for col, p in enumerate(projects):
        metrics, gauge = [], {}
        if p["entries"] is not None and p["max_files"]:
            metrics.append(("Files", p["entries"], p["max_files"],
                            f"{commafy(p['entries'])} / {commafy(p['max_files'])} files"))
            gauge["Files"] = (p["entries"], p["max_files"])
        if p["bytes"] is not None and p["max_bytes"]:
            metrics.append(("Storage", p["bytes"], p["max_bytes"],
                            f"{fmt_bytes(p['bytes'])} / {fmt_bytes(p['max_bytes'])}"))
            gauge["Storage"] = (p["bytes"], p["max_bytes"])

        ax_g = fig.add_subplot(gs[0, col])
        if metrics:
            gfacts = draw_gauge(ax_g, p["label"], metrics)
        else:
            gfacts = {}
            ax_g.text(0.5, 0.5, f"{p['label']}\nno quota found", ha="center",
                      va="center", fontsize=13, color="gray")
            ax_g.set_axis_off()
        for name, f in gfacts.items():
            alerts.append((f["pct"], p["label"], name, f["status"]))

        # Both donuts: who holds the files and who holds the bytes can differ a
        # lot (many tiny files vs a few huge ones), so cleanup targets differ.
        donuts = {}
        for row, metric, vals, attributed, label, center in (
                (1, "files", p["owners_files"], p["attributed_files"], "Files by owner",
                 lambda t: f"{commafy(t)}\nfiles"),
                (2, "bytes", p["owners_bytes"], p["attributed_bytes"], "Storage by owner",
                 fmt_bytes)):
            nown = sum(1 for v in vals.values() if v)
            d = common.draw_donut(fig.add_subplot(gs[row, col]), vals, color_map,
                                  f"{label} — {nown} owners", center(attributed))
            donuts[metric] = {**d, "attributed": attributed, "owner_vals": vals}

        drawn["projects"].append({"label": p["label"], "gauge": gauge,
                                  "gauge_facts": gfacts, "donuts": donuts,
                                  "entries": p["entries"],
                                  "attributed_files": p["attributed_files"]})

    handles = [Patch(facecolor=color_map[o], edgecolor="white", label=o) for o in all_owners]
    if handles:
        fig.legend(handles=handles, title="Owners", title_fontsize=12, loc="lower center",
                   bbox_to_anchor=(0.5, 0.035), ncol=min(len(handles), 6), fontsize=10)
    fig.text(0.5, 0.005,
             "Gauge bands:  green <80% OK   ·   orange 80–95% WATCH   ·   "
             "red ≥95% NEAR LIMIT   ·   dark-red >100% OVER   ·   dashed line = quota",
             ha="center", va="bottom", fontsize=9.5, color="#455A64")

    fig.suptitle(title, fontsize=18, fontweight="bold", y=0.97)
    fig.text(0.5, 0.945, f"Snapshot: {snapshot}", ha="center", fontsize=12, color="#37474F")
    if alerts:
        top = max(alerts, key=lambda a: a[0])
        if top[0] >= 80:
            fig.text(0.5, 0.922, f"⚠  {top[1]} {top[2].lower()} at {top[0]:.0f}% "
                     f"— {top[3]} (binding constraint)", ha="center", fontsize=13.5,
                     fontweight="bold", color=band_info(top[0])[0])

    fig.savefig(output_path, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    drawn["output_path"] = output_path
    return drawn


# --------------------------------- QA/QC -----------------------------------
def qa_checks(drawn):
    """Cross-check every drawn number against its source. Returns (ok, lines)."""
    lines, ok = [], True

    def check(cond, msg):
        nonlocal ok
        lines.append(("PASS" if cond else "FAIL", msg))
        ok = ok and bool(cond)

    def warn(cond, msg):
        lines.append(("PASS" if cond else "WARN", msg))

    out = drawn.get("output_path", "")
    size = os.path.getsize(out) if out and os.path.exists(out) else 0
    check(size > 5000, f"PNG written and non-trivial ({size} bytes)")
    cmap = drawn["color_map"]
    check(len(set(cmap.values())) == len(cmap), f"each of {len(cmap)} owners has a distinct colour")

    for p in drawn["projects"]:
        tag = f"[{p['label']}]"
        # The gauges are the point of the report: no quota at all must block posting.
        check(bool(p["gauge"]), f"{tag} quota gauge present (Files and/or Storage)")
        for name, (used, limit) in p["gauge"].items():
            fact = p["gauge_facts"].get(name, {})
            pct = used / limit * 100
            check(abs(pct - fact.get("pct", -999)) < 0.05,
                  f"{tag} {name} drawn {fact.get('pct', float('nan')):.1f}% == used/limit")
            check(fact.get("status") == band_info(pct)[1],
                  f"{tag} {name} status '{fact.get('status')}' matches band ({pct:.0f}%)")

        for metric, d in p["donuts"].items():
            sizes, labels, vals = d["sizes"], d["labels"], d["owner_vals"]
            if not sizes:
                continue
            check(int(sum(sizes)) == int(d["attributed"]),
                  f"{tag} {metric} slices sum to attributed total ({int(sum(sizes)):,})")
            check(all(int(s) == int(vals[l]) for l, s in zip(labels, sizes)),
                  f"{tag} {metric} every slice equals that owner's total")
            check(abs(sum(d["pcts"]) - 100.0) < 0.5,
                  f"{tag} {metric} percentages sum to 100 ({sum(d['pcts']):.2f})")
            check(labels == sorted(labels, key=lambda l: vals[l], reverse=True),
                  f"{tag} {metric} slices ordered largest first")

        if p["entries"]:
            gap = p["entries"] - p["attributed_files"]
            warn(0 <= gap <= max(50, 0.01 * p["entries"]),
                 f"{tag} {gap:,} entries unattributed (in skip_owners dirs or changed "
                 f"during the scan) of {p['entries']:,}")
    return ok, lines


def write_qa_report(path, ok, lines, snapshot):
    with open(path, "w") as fh:
        fh.write(f"hpc-digest storage QA, {snapshot}\nOverall: {'PASS' if ok else 'FAIL'}\n")
        fh.write("=" * 64 + "\n")
        for status, msg in lines:
            fh.write(f"[{status}] {msg}\n")


# ---------------------------------- main -----------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="hpc_digest storage", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_common_args(ap)
    ap.add_argument("--strict-qa", action="store_true", help="exit 1 if QA fails")
    args = ap.parse_args(argv)

    cfg = common.load_config(args.config)
    section = cfg.get("storage", {})
    if not section.get("projects"):
        raise SystemExit("No [[storage.projects]] in the config.")
    outdir = common.output_dir(cfg)
    skip_owners = set(section.get("skip_owners", ["root"]))
    max_depth = int(section.get("max_depth", 8))
    snapshot = date.today().isoformat()
    stamp = snapshot.replace("-", "")

    projects = []
    for proj in section["projects"]:
        print(f"Scanning {proj['path']} ...")
        p = scan_project(proj, skip_owners, max_depth)
        write_owner_csv(os.path.join(outdir, f"storage_owners_{slugify(p['label'])}_{stamp}.csv"),
                        p["rows"])
        print(f"  {p['entries'] or 0:,} entries, {fmt_bytes(p['bytes'] or 0)}; "
              f"{len(p['owners_files'])} owners")
        projects.append(p)

    title = section.get("title", "Storage & inode quota")
    out_png = args.out or os.path.join(outdir, f"storage_{stamp}.png")
    drawn = create_chart(projects, title, snapshot,
                         os.path.join(outdir, "user_colors.json"), out_png)
    print(f"Chart saved: {out_png}")

    ok, lines = qa_checks(drawn)
    qa_path = os.path.join(outdir, f"storage_qa_{stamp}.txt")
    write_qa_report(qa_path, ok, lines, snapshot)
    n_fail = sum(1 for s, _ in lines if s == "FAIL")
    n_warn = sum(1 for s, _ in lines if s == "WARN")
    print(f"QA/QC: {'PASS' if ok else 'FAIL'}  ({n_fail} fail, {n_warn} warn) -> {qa_path}")
    for status, msg in lines:
        if status != "PASS":
            print(f"  [{status}] {msg}")

    common.publish(cfg, args, f"*{title}*", out_png, ARTIFACT_GLOBS, outdir, qa_ok=ok)
    if args.strict_qa and not ok:
        sys.exit(1)
