"""Fetch and plot audits over time, counted from telemetry.

Each fetch merges into docs/assets/runs_history.json, so a dead source shows a
flat line instead of a gap. Runs need TELEMETRY_PK, a personal read-scope key.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests

ACCENT = "#E8756A"  # house coral
POSTHOG_HOST = "https://us.posthog.com"
POSTHOG_PROJECT_ID = "485680"
TIMEOUT = (5, 30)  # (connect, read); keeps a hung fetch from stalling the job

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets"


def fetch_runs() -> dict[str, int]:
    """Started runs per day, from telemetry. Counts ``ifixai_started`` events."""
    response = requests.post(
        f"{POSTHOG_HOST}/api/projects/{POSTHOG_PROJECT_ID}/query/",
        headers={"Authorization": f"Bearer {os.environ['TELEMETRY_PK']}"},
        json={
            "query": {
                "kind": "HogQLQuery",
                "query": (
                    "SELECT toDate(timestamp) AS day, count() AS runs "
                    "FROM events WHERE event = 'ifixai_started' "
                    "GROUP BY day ORDER BY day"
                ),
            }
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return {str(day): int(runs) for day, runs in response.json()["results"]}


def merge(path: Path, fresh: dict[str, int]) -> dict[str, int]:
    """Fold a fetch into the stored history, last write winning on a repeated date."""
    history: dict[str, int] = json.loads(path.read_text()) if path.exists() else {}
    history.update(fresh)
    path.write_text(json.dumps(history, indent=2, sort_keys=True))
    return history


def _cumulative(history: dict[str, int]) -> tuple[list[datetime], list[int]]:
    dates = sorted(history)
    xs = [datetime.strptime(d, "%Y-%m-%d") for d in dates]
    ys: list[int] = []
    running = 0
    for date in dates:
        running += history[date]
        ys.append(running)
    return xs, ys


def plot_audits(history: dict[str, int], out: Path) -> None:
    """Plot cumulative audits as a single line with its end value labelled."""
    xs, ys = _cumulative(history)

    with plt.xkcd():
        fig, ax = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")

        ax.plot(xs, ys, color=ACCENT, linewidth=2.5)
        ax.set_title(f"No. of Audits (since {xs[0]:%b %d, %Y})", fontsize=16, pad=20)
        ax.set_xlabel("Date", fontsize=12)
        ax.set_ylabel("Cumulative audits", fontsize=12)

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=7))
        fig.autofmt_xdate(rotation=0, ha="center")
        ax.set_ylim(0, ys[-1] * 1.25)

        ax.plot(xs[-1], ys[-1], "o", color=ACCENT, markersize=9)
        ax.annotate(
            f"{ys[-1]:,}",
            xy=(xs[-1], ys[-1]),
            xytext=(-62, 26),
            textcoords="offset points",
            fontsize=13,
            color=ACCENT,
            ha="center",
            va="center",
            arrowprops=dict(arrowstyle="->", color=ACCENT, lw=2, connectionstyle="arc3,rad=-0.2"),
        )

        fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)

    print(f"Wrote {out.name}: audits {ys[-1]:,}")


if __name__ == "__main__":
    if not os.environ.get("TELEMETRY_PK"):
        raise SystemExit("TELEMETRY_PK unset: cannot plot audits")

    runs = merge(DATA_DIR / "runs_history.json", fetch_runs())
    plot_audits(runs, DATA_DIR / "audits_chart.png")
