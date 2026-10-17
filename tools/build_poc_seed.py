"""Build app/beds/seed_poc.json from the STRUCTURE of IHH's (synthetic) census workbook.

Usage: python tools/build_poc_seed.py "<path to Test Census.xlsx>"

Copies facility names, bed labels and bed statuses only. Every occupant is invented placebo data
(POC-0001, "Test Client 001", ...). County and program type are placeholders that IHH must confirm.
"""
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.beds import census  # noqa: E402
from app.beds.service import slug  # noqa: E402

OUT = ROOT / "app" / "beds" / "seed_poc.json"
NOTE = "POC placeholder county/program — confirm with IHH"
PAYERS = ["Cal Optima", "LA Care", "DHS", "Health Net", "Molina", "Anthem", "Blue Shield", "Kaiser"]
EXTRA = {"PETS": "No", "VI-SPDAT": "N/A", "Phone": "000-000-0000"}

# facility name -> (county, program_type)
FACILITIES = {
    **{n: ("Los Angeles", "recuperative_care") for n in ("RC Whittier 2.0", "SFS", "Lincoln Park", "Broadway")},
    **{n: ("Los Angeles", "project_roomkey") for n in ("Royal Pagoda PRK", "LONG BEACH PRK")},
    "ALO": ("Los Angeles", "shelter"),
    **{n: ("Orange", "recuperative_care") for n in ("RC Midway", "Midway Recup", "Buena ParkRC FULL", "MWC", "Coral",
                                                    "Midway - Closed")},
    **{n: ("Orange", "shelter") for n in ("BP EMERGENCY SHELTER QUARANTINE", "Laguna Hills VPS", "Anaheim VPS",
                                          "Stanton VPS", "Joplin")},
    "RVS": ("Riverside", "recuperative_care"),
}
INACTIVE = {"Midway - Closed"}


def placebo_stay(n: int, rng: random.Random, today: date) -> dict:
    intake = today - timedelta(days=rng.randint(1, 60))
    return {
        "client_id": f"POC-{n:04d}", "first_name": "Test", "last_name": f"Client {n:03d}",
        "gender": rng.choice(["M", "F"]), "intake_date": intake.isoformat(),
        "expected_exit_date": (intake + timedelta(days=30)).isoformat(), "payer": PAYERS[(n - 1) % len(PAYERS)],
        "case_manager": f"CM Placeholder {n}", "notes": "POC placebo occupant", "extra": dict(EXTRA),
    }


def build(workbook: Path) -> dict:
    sheets, _ = census.parse_workbook(workbook.read_bytes())
    by_name = {s.sheet.strip(): s for s in sheets}
    rng = random.Random(42)
    today = date.today()
    n = 0
    facilities = []
    for name, (county, program) in FACILITIES.items():
        sh = by_name.get(name)
        if sh is None:
            print(f"warning: sheet {name!r} has no beds in the workbook; omitted", file=sys.stderr)
            continue
        beds, seen = [], set()
        for r in sh.rows:
            bid = slug(f"{slug(name)}-{r.label}")
            if bid in seen:
                continue
            seen.add(bid)
            stay = None
            if r.stay is not None:
                n += 1
                stay = placebo_stay(n, rng, today)
            beds.append({"label": r.label, "status": r.status, "notes": r.bed_notes, "wheelchair": r.wheelchair,
                         "sex_designation": r.sex_designation, "stay": stay})
        facilities.append({"id": slug(name), "name": name, "county": county, "program_type": program,
                           "active": name not in INACTIVE, "notes": NOTE, "beds": beds})
    return {"generated": today.isoformat(), "facilities": facilities}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    data = build(Path(sys.argv[1]))
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    beds = sum(len(f["beds"]) for f in data["facilities"])
    stays = sum(1 for f in data["facilities"] for b in f["beds"] if b["stay"])
    print(f"wrote {OUT}: {len(data['facilities'])} facilities, {beds} beds, {stays} stays")


if __name__ == "__main__":
    main()
