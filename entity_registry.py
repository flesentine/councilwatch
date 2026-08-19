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
    / "entity_registry.json"
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = text.replace("&", " and ")
    text = text.lower()

    # Helps transcripts such as "O C F A" compare with "OCFA".
    text = re.sub(
        r"\b(?:[a-z]\s+){2,}[a-z]\b",
        lambda m: m.group(0).replace(" ", ""),
        text,
    )

    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


@lru_cache(maxsize=1)
def load_registry():
    data = json.loads(
        REGISTRY_PATH.read_text(encoding="utf-8")
    )

    ambiguous = {
        normalize(x)
        for x in data.get("ambiguous_aliases", [])
    }

    aliases = []

    for entity in data["entities"]:
        names = {
            entity["canonical"],
            *entity.get("aliases", []),
        }

        for name in names:
            aliases.append(
                (
                    normalize(name),
                    name,
                    entity,
                )
            )

    return data, ambiguous, aliases


def match_entity(
    text: str,
    *,
    fuzzy_threshold: float = 0.89,
):
    """
    Returns None when there is no sufficiently confident match.

    Exact aliases are preferred.

    Fuzzy matching is conservative because this runs before
    LLM/web verification.
    """
    query = normalize(text)

    if not query:
        return None

    _, ambiguous, aliases = load_registry()

    # Some acronyms can mean multiple agencies.
    if query in ambiguous:
        return None

    # Exact first.
    for alias_norm, alias, entity in aliases:
        if query == alias_norm:
            return {
                "heard": text,
                "canonical": entity["canonical"],
                "matched_alias": alias,
                "match_type": "exact",
                "confidence": 1.0,
                "jurisdiction": entity["jurisdiction"],
                "entity_type": entity["type"],
                "source": "local_entity_registry",
            }

    best = None
    best_score = 0.0
    best_alias = None

    for alias_norm, alias, entity in aliases:
        score = SequenceMatcher(
            None,
            query,
            alias_norm,
        ).ratio()

        if score > best_score:
            best_score = score
            best = entity
            best_alias = alias

    if best is None or best_score < fuzzy_threshold:
        return None

    return {
        "heard": text,
        "canonical": best["canonical"],
        "matched_alias": best_alias,
        "match_type": "fuzzy",
        "confidence": round(best_score, 4),
        "jurisdiction": best["jurisdiction"],
        "entity_type": best["type"],
        "source": "local_entity_registry",
    }


def find_entities_in_text(text: str):
    """
    Find known government/public-agency entities directly in free text.

    Only explicit registry aliases are accepted here. We deliberately do
    NOT fuzzy-scan arbitrary prose because fuzzy replacement belongs in
    the secondary verification layer.

    Longer aliases win so "OC Public Works" is preferred over a nested
    generic phrase such as "Public Works".
    """
    haystack = normalize(text)

    if not haystack:
        return []

    _, ambiguous, aliases = load_registry()

    candidates = []

    for alias_norm, alias, entity in aliases:
        if not alias_norm:
            continue

        # Never automatically resolve deliberately ambiguous abbreviations.
        if alias_norm in ambiguous:
            continue

        # Municipal department terminology differs between cities.
        # For municipal entities, only accept the canonical name itself.
        if (
            entity.get("jurisdiction") == "municipal"
            and alias_norm != normalize(entity["canonical"])
        ):
            continue

        pattern = (
            r"(?<![a-z0-9])"
            + re.escape(alias_norm)
            + r"(?![a-z0-9])"
        )

        for match in re.finditer(pattern, haystack):
            candidates.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "length": match.end() - match.start(),
                    "alias": alias,
                    "entity": entity,
                }
            )

    # Prefer the longest match when aliases overlap.
    candidates.sort(
        key=lambda x: (
            -x["length"],
            x["start"],
        )
    )

    accepted = []
    occupied = []

    for candidate in candidates:
        start = candidate["start"]
        end = candidate["end"]

        overlap = any(
            start < used_end and end > used_start
            for used_start, used_end in occupied
        )

        if overlap:
            continue

        accepted.append(candidate)
        occupied.append((start, end))

    # One output row per canonical entity.
    results = []
    seen = set()

    for candidate in accepted:
        entity = candidate["entity"]
        canonical = entity["canonical"]

        key = normalize(canonical)

        if key in seen:
            continue

        seen.add(key)

        registry_type = entity.get("type", "other")

        if registry_type == "public_facility":
            intelligence_type = "place"
        else:
            intelligence_type = "government_body"

        results.append(
            {
                "observed_text": candidate["alias"],
                "canonical_text": canonical,
                "entity_type": intelligence_type,
                "status": "VERIFIED",
                "confidence": "high",
                "evidence": (
                    "Matched an explicit canonical name or alias "
                    "in the CouncilWatch authoritative government "
                    "entity registry."
                ),
                "official_source_url": "",
                "verification_source": "local_entity_registry",
                "jurisdiction": entity.get(
                    "jurisdiction",
                    "",
                ),
                "registry_entity_type": registry_type,
            }
        )

    return results
