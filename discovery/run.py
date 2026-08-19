
#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sources.common import Meeting
from state import StateDB

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
CITIES = json.loads((ROOT / "cities.json").read_text())
DB = StateDB(DATA / "councilwatch.db")


def adapter_for(name: str):
    if name == "granicus":
        from sources.granicus import discover
        return discover
    if name == "mission_viejo":
        from sources.mission_viejo import discover
        return discover
    if name == "youtube_latest":
        from sources.youtube_latest import discover
        return discover
    if name == "civicengage":
        from sources.civicengage import discover
        return discover
    raise ValueError(f"Unknown adapter: {name}")


def short_url(value: str) -> str:
    if not value:
        return "—"
    return value if len(value) <= 72 else value[:69] + "..."


def main():
    scan_id = DB.begin_scan()
    results = []
    ok_count = 0
    error_count = 0

    print("\nCouncilWatch Pi v2 — normalized meeting detector")
    print("=" * 62)

    for city in CITIES:
        print(f"\n{city['name'].upper()}")
        try:
            found = adapter_for(city["adapter"])(city)
            latest = found.get("latest_completed")
            upcoming = found.get("next_upcoming")
            city_result = {"slug": city["slug"], "name": city["name"], "ok": True}

            for key, meeting in (("latest_completed", latest), ("next_upcoming", upcoming)):
                if meeting:
                    item = meeting.to_dict() if hasattr(meeting, "to_dict") else dict(meeting)
                    item["is_new"] = DB.upsert_meeting(item)
                    city_result[key] = item
                else:
                    city_result[key] = None

            # Some adapters return one selected meeting per
            # configured body, such as City Council and
            # Planning Commission.
            handled_ids = {
                item["external_id"]
                for item in (
                    city_result.get("latest_completed"),
                    city_result.get("next_upcoming"),
                )
                if item
            }

            additional_matches = []

            for meeting in found.get("selected_meetings", []):
                item = (
                    meeting.to_dict()
                    if hasattr(meeting, "to_dict")
                    else dict(meeting)
                )

                if item["external_id"] in handled_ids:
                    continue

                item["is_new"] = DB.upsert_meeting(item)
                handled_ids.add(item["external_id"])
                additional_matches.append(item)

                flag = "NEW" if item["is_new"] else "seen"

                print(
                    f"  Additional match : "
                    f"{item['meeting_date']} - "
                    f"{item['title']} "
                    f"[{item['external_id']}, {flag}]"
                )

            city_result["additional_matches"] = additional_matches

            if latest:
                m = city_result["latest_completed"]
                flag = "NEW" if m["is_new"] else "seen"
                print(f"  Latest completed : {m['meeting_date'] or 'date unknown'} — {m['title']}")
                print(f"  External ID      : {m['external_id']} ({flag})")
                print(f"  Agenda           : {'FOUND' if m['agenda_url'] else 'not found'}")
                print(f"  Recording        : {m['recording_status'].upper()}")
                print(f"  Pipeline status  : {m['status'].upper()}")
            else:
                print("  Latest completed : NOT NORMALIZED YET")

            if upcoming:
                u = city_result["next_upcoming"]
                print(f"  Next upcoming    : {u['meeting_date'] or 'date unknown'} — {u['title']}")
            else:
                print("  Next upcoming    : none detected")

            ok_count += 1
            results.append(city_result)
        except Exception as exc:
            error_count += 1
            print(f"  ERROR            : {type(exc).__name__}: {exc}")
            results.append({
                "slug": city["slug"], "name": city["name"], "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })

    payload = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "city_count": len(CITIES),
        "ok_count": ok_count,
        "error_count": error_count,
        "results": results,
    }
    (DATA / "latest_status.json").write_text(json.dumps(payload, indent=2))
    DB.finish_scan(scan_id, ok_count, error_count, payload)

    print("\n" + "=" * 62)
    print(f"Cities checked: {len(CITIES)} | OK: {ok_count} | Errors: {error_count}")
    print(f"SQLite state : {DATA / 'councilwatch.db'}")
    print(f"JSON status  : {DATA / 'latest_status.json'}")

if __name__ == "__main__":
    main()
