from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path


SERVICE = (
    "https://ocgis.com/arcpub/rest/services/"
    "Map_Layers/Street_Centerlines_With_Labels/"
    "FeatureServer/2/query"
)

OUT = Path("data/street_registry.json")
BATCH = 1000


SUFFIXES = {
    "AV": "Avenue",
    "AVE": "Avenue",
    "BLVD": "Boulevard",
    "CIR": "Circle",
    "CK": "Creek",
    "CT": "Court",
    "DR": "Drive",
    "HWY": "Highway",
    "LN": "Lane",
    "PKWY": "Parkway",
    "PL": "Place",
    "PLZ": "Plaza",
    "RD": "Road",
    "ST": "Street",
    "TER": "Terrace",
    "TRL": "Trail",
    "WAY": "Way",
}


LOWERCASE_WORDS = {
    "de",
    "del",
    "la",
    "las",
    "los",
    "el",
    "y",
}


def smart_title(text: str) -> str:
    words = text.strip().lower().split()
    result = []

    for i, word in enumerate(words):
        if i and word in LOWERCASE_WORDS:
            result.append(word)
        else:
            result.append(word.title())

    return " ".join(result)


def make_name(prefix, street, suffix):
    prefix = str(prefix or "").strip()
    street = str(street or "").strip()
    suffix = str(suffix or "").strip()

    # Some OC GIS rows embed the street type in STREETNAME
    # rather than storing it separately in SUFFIX.
    if not suffix and street:
        words = street.split()

        if len(words) > 1:
            trailing = words[-1].upper()

            if trailing in SUFFIXES:
                suffix = trailing
                street = " ".join(words[:-1])

    parts = []

    if prefix:
        parts.append(prefix.upper())

    if street:
        parts.append(smart_title(street))

    if suffix:
        parts.append(
            SUFFIXES.get(
                suffix.upper(),
                smart_title(suffix),
            )
        )

    return " ".join(parts).strip()


def fetch_page(offset: int):
    params = {
        "where": "STREETNAME IS NOT NULL",
        "outFields": (
            "OBJECTID,STREETNAME,OLDSTREETNAME,"
            "PREFIX,SUFFIX,STREETCODE"
        ),
        "returnGeometry": "false",
        "resultOffset": str(offset),
        "resultRecordCount": str(BATCH),
        "orderByFields": "OBJECTID",
        "f": "json",
    }

    url = SERVICE + "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "CouncilWatch/1.0"},
    )

    with urllib.request.urlopen(
        req,
        timeout=60,
    ) as response:
        return json.load(response)


def main():
    names = {}
    offset = 0
    rows = 0

    while True:
        data = fetch_page(offset)

        if "error" in data:
            raise RuntimeError(
                json.dumps(data["error"], indent=2)
            )

        features = data.get("features", [])

        for feature in features:
            attrs = feature.get("attributes", {})

            street = str(
                attrs.get("STREETNAME") or ""
            ).strip()

            if not street:
                continue

            canonical = make_name(
                attrs.get("PREFIX"),
                street,
                attrs.get("SUFFIX"),
            )

            if not canonical:
                continue

            key = canonical.casefold()

            row = names.setdefault(
                key,
                {
                    "canonical": canonical,
                    "official_streetname": street,
                    "prefix": str(
                        attrs.get("PREFIX") or ""
                    ).strip(),
                    "suffix": str(
                        attrs.get("SUFFIX") or ""
                    ).strip(),
                    "old_names": set(),
                    "street_codes": set(),
                },
            )

            old = str(
                attrs.get("OLDSTREETNAME") or ""
            ).strip()

            if old:
                row["old_names"].add(
                    smart_title(old)
                )

            code = attrs.get("STREETCODE")

            if code is not None:
                row["street_codes"].add(code)

            rows += 1

        print(
            f"Fetched {len(features)} rows "
            f"at offset {offset}"
        )

        if len(features) < BATCH:
            break

        offset += len(features)

    streets = []

    for row in names.values():
        row["old_names"] = sorted(
            row["old_names"]
        )
        row["street_codes"] = sorted(
            row["street_codes"]
        )
        streets.append(row)

    streets.sort(
        key=lambda x: x["canonical"].casefold()
    )

    output = {
        "schema_version": 2,
        "source": (
            "Orange County Street Centerlines "
            "With Labels"
        ),
        "source_agency": (
            "County of Orange GIS"
        ),
        "source_url": SERVICE.rsplit(
            "/query",
            1,
        )[0],
        "feature_rows": rows,
        "streets": streets,
    }

    OUT.write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    print()
    print(
        f"Wrote {len(streets)} unique "
        f"street names to {OUT}"
    )


if __name__ == "__main__":
    main()
