"""Check recreation.gov for Carlsbad Caverns tickets on a given date.

Exit code 0 = nothing available, 10 = tickets available, 1 = request error.
Prints a one-line summary and writes it to $GITHUB_OUTPUT as `summary`.
"""
import json
import os
import sys
import urllib.request

FACILITY = os.environ.get("FACILITY_ID", "234637")
TOUR = os.environ.get("TOUR_ID", "10086811")
DATE = os.environ.get("TARGET_DATE", "2026-11-27")  # YYYY-MM-DD
LINK = f"https://www.recreation.gov/ticket/{FACILITY}/ticket/{TOUR}"


def main() -> int:
    if os.environ.get("SIMULATE_ERROR") == "true":
        print("request failed: simulated error", file=sys.stderr)
        return 1
    year, month = DATE.split("-")[:2]
    url = (
        f"https://www.recreation.gov/api/ticket/availability/facility/{FACILITY}/"
        f"monthlyAvailabilitySummaryView?year={year}&month={int(month)}&inventoryBucket=FIT"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        day = data["facility_availability_summary_view_by_local_date"][DATE]
        t = day["tour_availability_summary_view_by_tour_id"][TOUR]
    except Exception as e:  # network error, 403, schema change, date missing
        print(f"request failed: {e}", file=sys.stderr)
        return 1

    open_count = t["scheduled_count"] - t["reserved_count"]
    available = open_count > 0 or t["availability_level"] != "NONE"
    if available:
        summary = (f"TICKETS AVAILABLE for {DATE}: {open_count} open, "
                   f"level={t['availability_level']}, times={t['available_times']}. {LINK}")
    else:
        summary = f"No tickets yet for {DATE} ({t['reserved_count']}/{t['scheduled_count']} reserved)"
    print(summary)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"summary={summary}\n")
    return 10 if available else 0


if __name__ == "__main__":
    sys.exit(main())
