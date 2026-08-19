import sys

#!/usr/bin/env python3
from pathlib import Path
from state import StateDB

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
db = StateDB(ROOT / "data" / "councilwatch.db")
rows = db.recent(40)
print("\nStored meeting state")
print("=" * 88)
for r in rows:
    print(f"{r['meeting_date'] or '????-??-??'}  {r['city_slug']:<16} {r['kind']:<9} {r['status']:<18} {r['title']}")
