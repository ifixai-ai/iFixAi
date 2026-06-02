import os
import csv
import requests
from pathlib import Path
from datetime import datetime

OWNER = "ifixai-ai"
REPO = "iFixAi"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "assets"

CSV_FILE = DATA_DIR / "Unique cloners.csv"

TOKEN = os.environ["GITHUB_TOKEN"]

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
}

url = f"https://api.github.com/repos/{OWNER}/{REPO}/traffic/clones"

response = requests.get(url, headers=headers)
response.raise_for_status()

data = response.json()["clones"]

existing_dates = set()

if CSV_FILE.exists():
    with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [k.strip('"') for k in reader.fieldnames]
        for row in reader:
            existing_dates.add(row["Category"])

rows_to_add = []

for item in data:
    dt = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
    date_str = dt.strftime("%m/%d")

    if date_str not in existing_dates:
        rows_to_add.append({"Category": f"{date_str}", "Unique": item["uniques"]})

needs_newline = False
if CSV_FILE.exists() and CSV_FILE.stat().st_size > 0:
    with open(CSV_FILE, "rb") as f:
        f.seek(-1, os.SEEK_END)
        needs_newline = f.read(1) != b"\n"

with open(CSV_FILE, "a", newline="") as f:
    if needs_newline:
        f.write("\n")
    writer = csv.DictWriter(
        f,
        fieldnames=["Category", "Unique"],
        quoting=csv.QUOTE_NONNUMERIC,
        lineterminator="\n",
    )
    writer.writerows(rows_to_add)

print(f"Added {len(rows_to_add)} rows")
