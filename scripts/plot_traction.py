"""Fetch and plot PyPI installs and telemetry runs over time.

Each fetch merges into a history file under docs/assets/, so a dead source shows a
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

PACKAGE = "ifixai"
ACCENT = "#E8756A"  # house coral
RUNS_COLOR = "#2a78d6"  # blue, checked to stay distinct from ACCENT (incl. color blindness)
POSTHOG_HOST = "https://us.posthog.com"
POSTHOG_PROJECT_ID = "485680"
TIMEOUT = (5, 30)  # (connect, read); keeps a hung fetch from stalling the job

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets"


def fetch_downloads() -> dict[str, int]:
    """Downloads per day from pypistats (rolling ~180 day window), mirrors excluded."""
    response = requests.get(
        f"https://pypistats.org/api/packages/{PACKAGE}/overall",
        params={"mirrors": "false"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return {row["date"]: row["downloads"] for row in response.json()["data"]}


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


Series = tuple[dict[str, int], str, str]  # (daily history, legend label, colour)


def plot_pair(upper: Series, lower: Series, out: Path) -> None:
    """Plot exactly two cumulative series on one shared y-axis.

    End-point labels are hand-placed above and below to avoid overlap, so
    ``upper`` must be the series that ends higher.
    """
    curves = [(*_cumulative(history), label, color) for history, label, color in (upper, lower)]

    with plt.xkcd():
        fig, ax = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")

        for xs, ys, label, color in curves:
            ax.plot(xs, ys, color=color, linewidth=2.5, label=label)

        start = min(xs[0] for xs, _, _, _ in curves)
        ax.set_title(f"Installs and Runs (since {start:%b %d, %Y})", fontsize=16, pad=20)
        ax.set_xlabel("Date", fontsize=12)
        ax.set_ylabel("Cumulative count", fontsize=12)

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=7))
        fig.autofmt_xdate(rotation=0, ha="center")

        peak = max(ys[-1] for _, ys, _, _ in curves)
        ax.set_ylim(0, peak * 1.25)

        for offset, (xs, ys, label, color) in zip(((-62, 26), (-62, -30)), curves, strict=True):
            ax.plot(xs[-1], ys[-1], "o", color=color, markersize=9)
            ax.annotate(
                f"{ys[-1]:,}",
                xy=(xs[-1], ys[-1]),
                xytext=offset,
                textcoords="offset points",
                fontsize=13,
                color=color,
                ha="center",
                va="center",
                arrowprops=dict(
                    arrowstyle="->",
                    color=color,
                    lw=2,
                    connectionstyle="arc3,rad=-0.2",
                ),
            )

        ax.legend(loc="upper left", fontsize=12, frameon=False)

        fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)

    summary = ", ".join(f"{label} {ys[-1]:,}" for _, ys, label, _ in curves)
    print(f"Wrote {out.name}: {summary}")


if __name__ == "__main__":
    downloads = merge(DATA_DIR / "downloads_history.json", fetch_downloads())

    if not os.environ.get("TELEMETRY_PK"):
        raise SystemExit("TELEMETRY_PK unset: cannot plot runs alongside installs")

    runs = merge(DATA_DIR / "runs_history.json", fetch_runs())
    plot_pair(
        (downloads, "pip installs", ACCENT),
        (runs, "runs started", RUNS_COLOR),
        DATA_DIR / "installs_runs_chart.png",
    )
