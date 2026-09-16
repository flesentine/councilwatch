from pathlib import Path


path = Path("review_app.py")
text = path.read_text(encoding="utf-8")

old = '''    entity_rows = []

    for item in target.get(
        "entity_verification",
        [],
    ):
        if not isinstance(item, dict):
            continue

        status = esc(
'''

new = '''    public_entity_text = " ".join(
        [
            str(target.get("headline") or ""),
            str(target.get("dek") or ""),
            *[
                str(value or "")
                for value in target.get("body", [])
            ],
            *[
                str(value or "")
                for value in target.get("key_facts", [])
            ],
            *[
                str(value or "")
                for value in target.get("verification_notes", [])
            ],
        ]
    ).casefold()

    entity_rows = []

    for item in target.get(
        "entity_verification",
        [],
    ):
        if not isinstance(item, dict):
            continue

        entity_type = str(
            item.get("entity_type") or ""
        ).strip().casefold()

        raw_status = str(
            item.get("status") or "UNVERIFIED"
        ).strip().upper()

        raw_observed = str(
            item.get("observed_text") or ""
        ).strip()

        raw_canonical = str(
            item.get("canonical_text")
            or raw_observed
        ).strip()

        # The stored verification array remains complete. This
        # filter only keeps the private review panel focused on
        # identity/canonical-name checks that can affect public copy.
        if entity_type in {"program", "technology"}:
            continue

        if (
            entity_type != "person"
            and raw_status != "CORRECTED"
        ):
            review_types = {
                "government_body",
                "organization",
                "agency",
                "department",
                "place",
                "street",
                "facility",
                "project",
            }

            if entity_type not in review_types:
                continue

            names = [
                value
                for value in (
                    raw_observed,
                    raw_canonical,
                )
                if len(value) >= 4
            ]

            if not any(
                value.casefold() in public_entity_text
                for value in names
            ):
                continue

            compact_observed = "".join(
                char
                for char in raw_observed
                if char.isalnum()
            )

            if (
                raw_status == "UNVERIFIED"
                and raw_observed
                and " " not in raw_observed
                and raw_observed.isupper()
                and len(compact_observed) <= 8
            ):
                continue

        status = esc(
'''

if text.count(old) != 1:
    raise SystemExit(
        f"entity-loop anchor count was {text.count(old)}, expected 1"
    )

text = text.replace(old, new, 1)

old_heading = '''      <div class="panel">
        <h2>Names & places — official-source verification</h2>
        <ul>
          {entities_html or "<li>No entity verification stored for this revision.</li>"}
        </ul>
      </div>
'''

new_heading = '''      <div class="panel">
        <h2>Identity &amp; official-name verification</h2>
        <div class="note">
          Showing people and public-copy names that need identity
          or canonical-name review. Lower-signal terminology checks
          remain stored in the draft verification data.
        </div>
        <ul>
          {entities_html or "<li>No high-signal identity or official-name checks for this revision.</li>"}
        </ul>
      </div>
'''

if text.count(old_heading) != 1:
    raise SystemExit(
        f"entity-panel anchor count was {text.count(old_heading)}, expected 1"
    )

text = text.replace(old_heading, new_heading, 1)
path.write_text(text, encoding="utf-8")
