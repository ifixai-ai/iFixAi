import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MultipleLocator

DATA_DIR = Path(__file__).parent.parent / "docs" / "assets"
HISTORY_FILE = DATA_DIR / "cloners_history.json"
CSV_FILE = DATA_DIR / "Unique cloners.csv"


def load_history() -> dict[str, int]:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {}


def save_history(history: dict[str, int]) -> None:
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, sort_keys=True)


def merge_csv(history: dict[str, int]) -> dict[str, int]:
    year = datetime.now().year
    with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [k.strip('"') for k in reader.fieldnames]
        for row in reader:
            date_str = row["Category"].strip('"')
            value = int(row["Unique"])
            try:
                dt = datetime.strptime(f"{date_str}/{year}", "%m/%d/%Y")
            except ValueError:
                continue
            key = dt.strftime("%Y-%m-%d")
            history[key] = max(history.get(key, 0), value)
    return history


def plot(history: dict[str, int]) -> Path:
    dates_sorted = sorted(history.keys())
    dt_dates = [datetime.strptime(d, "%Y-%m-%d") for d in dates_sorted]

    cumulative: list[int] = []
    total = 0
    for d in dates_sorted:
        total += history[d]
        cumulative.append(total)

    with plt.xkcd():
        fig, ax = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")

        ax.plot(dt_dates, cumulative, color="#E8756A", linewidth=2.5)
        ax.fill_between(dt_dates, cumulative, alpha=0.08, color="#E8756A")

        ax.set_title("Unique Cloners History", fontsize=16, pad=20)
        ax.set_xlabel("Date", fontsize=12)
        ax.set_ylabel("Cumulative Unique Cloners", fontsize=12)

        launch = datetime(2026, 4, 27)
        last_date = max(
            dt_dates[-1],
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
        )
        days_to_friday = (4 - (launch + timedelta(days=1)).weekday()) % 7
        first_friday = launch + timedelta(days=1 + days_to_friday)
        fridays: list[datetime] = []
        d = first_friday
        while d <= last_date:
            fridays.append(d)
            d += timedelta(weeks=1)
        weekly_ticks = [launch] + fridays
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax.set_xticks(weekly_ticks)
        fig.autofmt_xdate(rotation=0, ha="center")

        latest = cumulative[-1]
        top = ((latest // 200) + 1) * 200
        ax.set_ylim(0, top)
        ax.yaxis.set_major_locator(MultipleLocator(200))

        ax.plot(dt_dates[-1], latest, "o", color="#E8756A", markersize=9)
        # move whole label+arrow together by changing label_offset (points from the dot)
        label_offset = (-60, 34)
        ax.annotate(
            f"{latest} unique clones!",
            xy=(dt_dates[-1], latest),
            xytext=label_offset,
            textcoords="offset points",
            fontsize=13,
            color="#E8756A",
            ha="center",
            va="center",
            arrowprops=dict(
                arrowstyle="->",
                color="#E8756A",
                lw=2,
                connectionstyle="arc3,rad=-0.2",
                shrinkA=6,
                shrinkB=6,
            ),
        )

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        plt.tight_layout()

        out = DATA_DIR / f"unique_cloners_chart.png"
        plt.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close()

    return out


def main() -> None:
    history = load_history()
    history = merge_csv(history)
    save_history(history)
    out = plot(history)
    print(f"Saved: {out}")
    print(f"Total unique cloners tracked: {sum(history.values())}")


if __name__ == "__main__":
    main()
