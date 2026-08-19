from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path


REGISTRY_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "street_registry.json"
)

STREET_TYPES = {
    "avenue", "boulevard", "circle", "court",
    "creek", "drive", "highway", "lane",
    "parkway", "place", "plaza", "road",
    "street", "terrace", "trail", "way",
}

# Common OC/California street-name leading words that do not
# necessarily have a conventional suffix.
STREET_LEADING_WORDS = {
    "avenida",
    "calle",
    "camino",
    "paseo",
    "via",
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(
        c for c in text
        if not unicodedata.combining(c)
    )

    text = text.lower()

    replacements = {
        "park way": "parkway",
        "high way": "highway",
        "boule vard": "boulevard",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[^a-z0-9]+", " ", text)

    return " ".join(text.split())


def street_type(text: str) -> str:
    words = normalize(text).split()

    if words and words[-1] in STREET_TYPES:
        return words[-1]

    return ""


def base_without_type(text: str) -> str:
    words = normalize(text).split()

    if words and words[-1] in STREET_TYPES:
        words = words[:-1]

    return " ".join(words)


def looks_like_street(text: str) -> bool:
    """
    Conservative gate used before applying street verification
    to a generic proper noun classified as a place.

    This prevents CouncilWatch from treating cities, parks,
    businesses, neighborhoods, etc. as streets merely because
    a similarly named road exists somewhere in Orange County.
    """
    words = normalize(text).split()

    if not words:
        return False

    if street_type(text):
        return True

    return words[0] in STREET_LEADING_WORDS


def source_url() -> str:
    data, _ = load_registry()
    return str(data.get("source_url") or "")


def phonetic_key(text: str) -> str:
    text = base_without_type(text)
    text = text.replace(" ", "")

    # Spanish J commonly sounds like English H.
    text = text.replace("j", "h")

    text = text.replace("ph", "f")
    text = text.replace("qu", "k")
    text = text.replace("ck", "k")

    # Remove vowels.
    text = re.sub(r"[aeiouy]", "", text)

    # Collapse repeated consonants.
    text = re.sub(r"(.)\1+", r"\1", text)

    return text


@lru_cache(maxsize=1)
def load_registry():
    data = json.loads(
        REGISTRY_PATH.read_text(encoding="utf-8")
    )

    rows = []

    for row in data["streets"]:
        names = {
            row["canonical"],
            *row.get("old_names", []),
        }

        for name in names:
            if not name:
                continue

            rows.append({
                "name": name,
                "normalized": normalize(name),
                "type": street_type(row["canonical"]),
                "phonetic": phonetic_key(name),
                "row": row,
            })

    return data, rows


def match_street(
    text: str,
    *,
    fuzzy_threshold: float = 0.90,
    minimum_margin: float = 0.05,
):
    query = normalize(text)

    if not query:
        return None

    _, rows = load_registry()
    qtype = street_type(text)

    # Exact canonical names always win over old-name/alias
    # collisions in the registry.
    canonical_exact = {}

    for item in rows:
        canonical = item["row"]["canonical"]

        if normalize(canonical) == query:
            canonical_exact[canonical] = item

    if len(canonical_exact) == 1:
        item = next(iter(canonical_exact.values()))

        return {
            "observed_text": text,
            "canonical_text": item["row"]["canonical"],
            "status": "VERIFIED",
            "confidence": "high",
            "match_type": "exact",
            "source": "orange_county_street_registry",
        }

    # Then allow an exact old-name/alias match, but treat that
    # as a correction when it differs from the current canonical
    # street name.
    exact = [
        item
        for item in rows
        if item["normalized"] == query
    ]

    exact_unique = {
        item["row"]["canonical"]: item
        for item in exact
    }

    if len(exact_unique) == 1:
        item = next(iter(exact_unique.values()))
        canonical = item["row"]["canonical"]

        same_as_canonical = (
            normalize(canonical) == query
        )

        return {
            "observed_text": text,
            "canonical_text": canonical,
            "status": (
                "VERIFIED"
                if same_as_canonical
                else "CORRECTED"
            ),
            "confidence": "high",
            "match_type": (
                "exact"
                if same_as_canonical
                else "exact_alias"
            ),
            "source": "orange_county_street_registry",
        }

    candidates = rows

    # If the transcript gives us a road type, require the
    # candidate to have the same type.
    if qtype:
        candidates = [
            item
            for item in candidates
            if item["type"] == qtype
        ]

    # Unique phonetic match.
    qphon = phonetic_key(text)

    if qphon and qtype:
        matches = {}

        for item in candidates:
            if item["phonetic"] == qphon:
                matches[
                    item["row"]["canonical"]
                ] = item

        if len(matches) == 1:
            item = next(iter(matches.values()))

            return {
                "observed_text": text,
                "canonical_text":
                    item["row"]["canonical"],
                "status": "CORRECTED",
                "confidence": "high",
                "match_type": "unique_phonetic",
                "source":
                    "orange_county_street_registry",
            }

    # Conservative fuzzy match.
    scores = {}

    for item in candidates:
        score = SequenceMatcher(
            None,
            query,
            item["normalized"],
        ).ratio()

        canonical = item["row"]["canonical"]

        if (
            canonical not in scores
            or score > scores[canonical][0]
        ):
            scores[canonical] = (
                score,
                item,
            )

    ranked = sorted(
        scores.values(),
        key=lambda x: x[0],
        reverse=True,
    )

    if not ranked:
        return None

    best_score, best = ranked[0]
    second_score = (
        ranked[1][0]
        if len(ranked) > 1
        else 0.0
    )

    if best_score < fuzzy_threshold:
        return None

    if best_score - second_score < minimum_margin:
        return None

    return {
        "observed_text": text,
        "canonical_text": best["row"]["canonical"],
        "status": "CORRECTED",
        "confidence": "high",
        "match_type": "unique_fuzzy",
        "score": round(best_score, 4),
        "runner_up_score": round(second_score, 4),
        "source": "orange_county_street_registry",
    }
