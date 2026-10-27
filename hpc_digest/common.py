"""Shared helpers: config, Slack, stable per-user colours, donut drawing, cleanup."""

import glob
import json
import os
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

import numpy as np
import matplotlib
matplotlib.use("Agg")  # non-interactive backend for batch jobs
import matplotlib.pyplot as plt

DEFAULT_CONFIG = os.environ.get("HPCD_CONFIG", "config.toml")

# Utilisation band colours, shared by both reports.
GREEN, ORANGE, RED, DARKRED = "#2E7D32", "#F57C00", "#C62828", "#7F0000"

# tab20 indices chosen to avoid the band hues (orange 2/3, green 4/5, red 6), so
# a user's slice can never be misread as a health colour.
SAFE_COLOR_SLOTS = [0, 8, 18, 12, 10, 14, 1, 9, 19, 13, 11, 15, 7, 16, 17]

LABEL_MIN_PCT = 3.0  # donut slices below this share are legend-only


# --------------------------------- config ----------------------------------
def expand(path):
    return os.path.expanduser(os.path.expandvars(path))


def load_config(path):
    path = expand(path)
    if not os.path.exists(path):
        sys.exit(f"Config not found: {path}\n"
                 "Copy config.example.toml to config.toml and edit it.")
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def output_dir(cfg):
    """Where charts, CSVs and the colour registry live (created if missing)."""
    path = expand(cfg.get("output", {}).get("dir", "out"))
    os.makedirs(path, exist_ok=True)
    return path


def slack_token(cfg):
    """SLACK_BOT_TOKEN from the environment, else the first line of [slack].token_file."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token:
        return token
    token_file = cfg.get("slack", {}).get("token_file")
    if token_file:
        try:
            with open(expand(token_file)) as fh:
                return fh.readline().strip() or None
        except OSError as exc:
            print(f"WARNING: could not read token_file: {exc}")
    return None


# ------------------------- stable per-user colours -------------------------
def _load_registry(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def assign_user_colors(logins, registry_path):
    """Map each login to a colour that never changes between runs or reports.

    The {login: slot} registry is append-only: existing logins keep their slot,
    new logins take the smallest free slot (wrapping once every slot is used).
    Both reports share one registry, so a person has the same colour in each.
    """
    registry = _load_registry(registry_path)
    n = len(SAFE_COLOR_SLOTS)
    for login in sorted(logins):                 # sorted -> deterministic slots
        if login not in registry:
            used = set(registry.values())
            registry[login] = next((s for s in range(n) if s not in used), len(registry) % n)
    try:
        with open(registry_path, "w") as fh:
            json.dump(registry, fh, indent=2, sort_keys=True)
    except OSError as exc:
        print(f"WARNING: could not write colour registry: {exc}")

    cmap = plt.cm.tab20
    return {login: cmap(SAFE_COLOR_SLOTS[registry[login] % n]) for login in logins}


# --------------------------------- drawing ---------------------------------
def draw_donut(ax, values, color_map, title, center_text, title_size=13):
    """Per-user donut with outside leader-line labels (name + %).

    Labels sit on white, so contrast is never an issue, and naming each slice
    keeps identity off colour alone. Slices under LABEL_MIN_PCT are legend-only.
    Returns what was drawn, for QA.
    """
    items = sorted(((k, v) for k, v in values.items() if v > 0),
                   key=lambda kv: kv[1], reverse=True)
    ax.set_xlim(-1.85, 1.85)
    ax.set_ylim(-1.45, 1.45)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(title, fontsize=title_size, fontweight="bold", pad=4)

    if not items:
        ax.add_patch(plt.Circle((0, 0), 0.58, color="lightgray"))
        ax.text(0, 0, "No data", ha="center", va="center",
                fontsize=16, color="gray", fontweight="bold")
        return {"labels": [], "sizes": [], "pcts": []}

    labels = [k for k, _ in items]
    sizes = [v for _, v in items]
    total = sum(sizes)
    pcts = [s / total * 100 for s in sizes]
    wedges, _ = ax.pie(sizes, colors=[color_map[k] for k in labels], startangle=90,
                       counterclock=False, radius=1.0,
                       wedgeprops=dict(width=0.42, edgecolor="white", linewidth=1.4))

    kw = dict(va="center", fontsize=10.5, fontweight="bold", annotation_clip=False)
    for w, label, pct in zip(wedges, labels, pcts):
        if pct < LABEL_MIN_PCT:
            continue
        ang = (w.theta1 + w.theta2) / 2.0
        x, y = np.cos(np.deg2rad(ang)), np.sin(np.deg2rad(ang))
        ax.annotate(f"{label}  {pct:.1f}%", xy=(x * 0.99, y * 0.99),
                    xytext=(1.32 * np.sign(x), 1.18 * y), ha="left" if x >= 0 else "right",
                    arrowprops=dict(arrowstyle="-", color="0.5",
                                    connectionstyle=f"angle,angleA=0,angleB={ang}"),
                    **kw)
    ax.text(0, 0, center_text, ha="center", va="center", fontsize=13,
            fontweight="bold", color="#263238")
    return {"labels": labels, "sizes": sizes, "pcts": pcts}


# ---------------------------------- Slack ----------------------------------
def post_image(token, channel, message, image_path):
    """Upload the chart with a caption. Returns True on success."""
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    try:
        WebClient(token=token).files_upload_v2(
            channel=channel, file=image_path, initial_comment=message,
            title=os.path.basename(image_path))
        print(f"Posted to Slack channel {channel}")
        return True
    except SlackApiError as e:
        print(f"Slack error: {e.response['error']}")
        return False


def delete_post(token, channel, ts):
    """Delete a prior post and, where the bot can see it, the file it carried.

    Used to replace a bad report: post the new one first, then call this, so the
    channel is never left empty if the upload fails. Best-effort.
    """
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    client = WebClient(token=token)
    try:
        hist = client.conversations_history(channel=channel, latest=ts, oldest=ts,
                                            inclusive=True, limit=1)
        msgs = hist.get("messages", [])
    except SlackApiError as e:
        print(f"Slack history lookup failed ({e.response['error']}); "
              "deleting the message only.")
        msgs = []
    for f in (msgs[0].get("files", []) if msgs else []):
        try:
            client.files_delete(file=f["id"])
            print(f"Deleted file {f['id']}")
        except SlackApiError as e:
            print(f"Could not delete file {f.get('id')}: {e.response['error']}")
    try:
        client.chat_delete(channel=channel, ts=ts)
        print(f"Deleted prior message {ts}")
        return True
    except SlackApiError as e:
        print(f"Could not delete message {ts}: {e.response['error']}")
        return False


def publish(cfg, args, message, image_path, artifact_globs, outdir, qa_ok=True):
    """Shared --post / --delete-ts / --cleanup handling for both reports."""
    posted = False
    if args.post:
        token = slack_token(cfg)
        channel = args.channel or cfg.get("slack", {}).get("channel")
        if not qa_ok and not args.force:
            print("Refusing to post: QA failed. Re-run with --force to override.")
        elif not token:
            print("Refusing to post: no SLACK_BOT_TOKEN and no readable [slack].token_file.")
        elif not channel:
            print("Refusing to post: no channel ([slack].channel or --channel).")
        else:
            posted = post_image(token, channel, message, image_path)
            if posted and args.delete_ts:
                delete_post(token, channel, args.delete_ts)

    # The Slack post is the record of each snapshot, so local artifacts are only
    # removed once it has landed; a failed or skipped post keeps them.
    if args.cleanup:
        if posted:
            cleanup(outdir, artifact_globs)
        else:
            print("Cleanup skipped: nothing was posted (artifacts kept).")
    return posted


def cleanup(outdir, patterns):
    removed = 0
    for pat in patterns:
        for path in sorted(glob.glob(os.path.join(outdir, pat))):
            try:
                os.remove(path)
                removed += 1
            except OSError as exc:
                print(f"Cleanup: could not remove {os.path.basename(path)}: {exc}")
    print(f"Cleanup: removed {removed} artifact(s) from {outdir}")
    return removed


def add_common_args(ap):
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help="TOML config (default: $HPCD_CONFIG or ./config.toml)")
    ap.add_argument("--out", default=None, help="output PNG path")
    ap.add_argument("--post", action="store_true", help="post the chart to Slack")
    ap.add_argument("--channel", default=None, help="override [slack].channel")
    ap.add_argument("--force", action="store_true", help="post even if QA fails")
    ap.add_argument("--delete-ts", default=None,
                    help="after posting, delete this earlier message (Slack ts)")
    ap.add_argument("--cleanup", action="store_true",
                    help="after a successful post, delete this report's local artifacts")
