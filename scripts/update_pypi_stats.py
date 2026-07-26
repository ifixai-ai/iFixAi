"""Archive raw pypistats data before it ages out of the API's rolling ~180-day window.

Fetches every per-day endpoint for the package and merges the rows into
docs/assets/pypi_stats_history.json (endpoint -> category -> date -> downloads),
so history accumulates beyond the window. Existing rows are overwritten with the
latest value for their date; rows the API no longer serves are kept as-is.
"""

import json
from pathlib import Path

import requests

PACKAGE = "ifixai"
TIMEOUT = (5, 30)  # (connect, read); keeps a hung fetch from stalling the job
ENDPOINTS = ("overall", "python_major", "python_minor", "system")

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets"
HISTORY_FILE = DATA_DIR / "pypi_stats_history.json"


def fetch(endpoint: str) -> list[dict]:
    """Daily rows for one pypistats endpoint, all categories (mirrors included)."""
    response = requests.get(
        f"https://pypistats.org/api/packages/{PACKAGE}/{endpoint}",
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["data"]


def main() -> None:
    history = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else {}
    new_rows = 0
    for endpoint in ENDPOINTS:
        section = history.setdefault(endpoint, {})
        for row in fetch(endpoint):
            series = section.setdefault(str(row["category"]), {})
            if row["date"] not in series:
                new_rows += 1
            series[row["date"]] = row["downloads"]
    HISTORY_FILE.write_text(json.dumps(history, indent=1, sort_keys=True) + "\n")
    total = sum(len(series) for section in history.values() for series in section.values())
    print(f"{new_rows} new rows; {total} total across {len(history)} endpoints")


if __name__ == "__main__":
    main()
