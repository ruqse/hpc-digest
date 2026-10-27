"""Month-to-date compute usage per user, one donut per Slurm account.

Usage comes from `sreport cluster AccountUtilizationByUser` in core-hours. When
an account has a monthly allocation in the config, its donut also shows used vs
allocated and whether usage is ahead of the month's pace.
"""

import argparse
import calendar
import os
import subprocess
from datetime import date

import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from . import common
from .common import GREEN, ORANGE, RED

ARTIFACT_GLOBS = ["compute_*.png"]


def month_to_date(today=None):
    """(first of this month, today) as ISO dates. sreport treats End as
    exclusive, so the window runs through yesterday."""
    today = today or date.today()
    return today.replace(day=1).isoformat(), today.isoformat()


def month_progress(today=None):
    """Share of the current month elapsed, in percent."""
    today = today or date.today()
    return today.day / calendar.monthrange(today.year, today.month)[1] * 100


def get_usage(account, start, end):
    """{login: core-hours} for one account, summed across clusters."""
    cmd = ["sreport", "-P", "-t", "Hour", "cluster", "AccountUtilizationByUser",
           f"Accounts={account}", f"Start={start}", f"End={end}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: sreport failed for {account}: {result.stderr.strip()}")
    users = {}
    for line in result.stdout.splitlines():
        if "|" not in line or line.startswith("Cluster|"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        login, used = parts[2].strip(), parts[4]
        if login:                                  # the account total row has no login
            users[login] = users.get(login, 0.0) + (float(used) if used else 0.0)
    return users


def pace_colors(pct, progress):
    """(allocation colour, pace colour): green <80%, orange 80-100%, red >100%;
    pace is red when usage is ahead of the month's elapsed share."""
    alloc = GREEN if pct < 80 else ORANGE if pct <= 100 else RED
    return alloc, (RED if pct > progress else GREEN)


def create_chart(accounts, title, start, end, progress, registry_path, output_path):
    """accounts: list of {label, users: {login: core_h}, monthly_core_hours}."""
    n = len(accounts)
    all_users = sorted({u for a in accounts for u in a["users"]})
    color_map = common.assign_user_colors(all_users, registry_path)

    fig = plt.figure(figsize=(11 * n, 11))
    gs = fig.add_gridspec(1, n, top=0.80, bottom=0.20, left=0.06, right=0.94, wspace=0.45)

    for col, acct in enumerate(accounts):
        ax = fig.add_subplot(gs[0, col])
        total = sum(acct["users"].values())
        common.draw_donut(ax, acct["users"], color_map, acct["label"],
                          f"{total:,.0f}\ncore-h", title_size=16)

        allocated = acct.get("monthly_core_hours") or 0
        if allocated > 0:
            pct = total / allocated * 100
            alloc_color, pace_color = pace_colors(pct, progress)
            ax.text(0.5, -0.07, f"{total/1000:.1f}k / {allocated/1000:.1f}k core-h ({pct:.0f}%)",
                    transform=ax.transAxes, ha="center", va="top", fontsize=14,
                    fontweight="bold", color=alloc_color)
            ax.text(0.5, -0.13, f"Expected: {progress:.0f}% • Used: {pct:.0f}%",
                    transform=ax.transAxes, ha="center", va="top", fontsize=11,
                    color=pace_color)
        else:
            ax.text(0.5, -0.07, f"{total:,.0f} core-h", transform=ax.transAxes,
                    ha="center", va="top", fontsize=14, fontweight="bold", color="#263238")

    handles = [Patch(facecolor=color_map[u], edgecolor="white", label=u) for u in all_users]
    if handles:
        fig.legend(handles=handles, title="Users", title_fontsize=12, loc="lower center",
                   bbox_to_anchor=(0.5, 0.02), ncol=min(len(handles), 3), fontsize=11)

    fig.suptitle(title, fontsize=18, fontweight="bold", y=0.95)
    fig.text(0.5, 0.89, f"{start} to {end}", ha="center", fontsize=12, color="#37474F")
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hpc_digest compute", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_common_args(ap)
    args = ap.parse_args(argv)

    cfg = common.load_config(args.config)
    section = cfg.get("compute", {})
    if not section.get("accounts"):
        raise SystemExit("No [[compute.accounts]] in the config.")
    outdir = common.output_dir(cfg)
    registry = os.path.join(outdir, "user_colors.json")

    start, end = month_to_date()
    accounts = []
    for a in section["accounts"]:
        users = get_usage(a["name"], start, end)
        print(f"{a['name']}: {len(users)} users, {sum(users.values()):,.0f} core-h month to date")
        accounts.append({"label": a.get("label", a["name"]), "users": users,
                         "monthly_core_hours": a.get("monthly_core_hours", 0)})

    title = section.get("title", "Month-to-date compute usage")
    out_png = args.out or os.path.join(outdir, f"compute_{end}.png")
    create_chart(accounts, title, start, end, month_progress(), registry, out_png)
    print(f"Chart saved: {out_png}")

    common.publish(cfg, args, f"*{title}*", out_png, ARTIFACT_GLOBS, outdir)
