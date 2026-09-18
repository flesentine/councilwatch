from __future__ import annotations

from pathlib import Path
from typing import Callable


def agenda_snapshot_path(
    drafts: Path,
    slug: str,
    external_id: str,
) -> Path:
    return (
        Path(drafts)
        / f"{slug}--{external_id}.agenda.txt"
    )


def reviewer_evidence_path(
    drafts: Path,
    slug: str,
    external_id: str,
) -> Path:
    return (
        Path(drafts)
        / f"{slug}--{external_id}.evidence.txt"
    )


def load_agenda_evidence(
    drafts: Path,
    slug: str,
    external_id: str,
    agenda_url: str,
    fetcher: Callable[[str], str],
    *,
    refresh: bool = False,
) -> tuple[str, str]:
    """
    Return official agenda/source text plus its provenance.

    The first successful retrieval is cached beside the draft so
    later re-audits use the same official evidence snapshot even if
    the city site is temporarily unavailable or changes shape.

    refresh=True explicitly asks for a new live snapshot. If that
    refresh fails, the last cached snapshot remains the safe fallback.
    """
    path = agenda_snapshot_path(
        drafts,
        slug,
        external_id,
    )

    cached = ""

    if path.exists():
        try:
            cached = path.read_text(
                encoding="utf-8"
            ).strip()
        except Exception:
            cached = ""

    if cached and not refresh:
        return cached, "cache"

    url = str(
        agenda_url or ""
    ).strip()

    if url:
        try:
            live = str(
                fetcher(url)
                or ""
            ).strip()
        except Exception:
            if cached:
                return cached, "cache"
            raise

        if live:
            path.write_text(
                live + "\n",
                encoding="utf-8",
            )
            return live, "live"

    if cached:
        return cached, "cache"

    return "", "none"


def read_reviewer_evidence(
    drafts: Path,
    slug: str,
    external_id: str,
) -> str:
    path = reviewer_evidence_path(
        drafts,
        slug,
        external_id,
    )

    if not path.exists():
        return ""

    try:
        return path.read_text(
            encoding="utf-8"
        ).strip()
    except Exception:
        return ""


def write_reviewer_evidence(
    drafts: Path,
    slug: str,
    external_id: str,
    text: str,
) -> Path:
    path = reviewer_evidence_path(
        drafts,
        slug,
        external_id,
    )

    cleaned = str(
        text or ""
    ).strip()

    if cleaned:
        path.write_text(
            cleaned + "\n",
            encoding="utf-8",
        )
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    return path


def assemble_audit_notes(
    notes: str,
    *,
    verification_context: str = "",
    reviewer_evidence: str = "",
) -> str:
    """
    Build the audit note bundle from source evidence only.

    Coverage plans and editorial summaries are intentionally excluded.
    Reviewer evidence is explicitly labeled because it is a human-added
    source supplement, not model-generated editorial reasoning.
    """
    sections = [
        str(
            notes or ""
        ).strip()
    ]

    verification = str(
        verification_context or ""
    ).strip()

    if verification:
        sections.append(
            verification
        )

    supplement = str(
        reviewer_evidence or ""
    ).strip()

    if supplement:
        sections.append(
            "REVIEWER-ADDED SOURCE EVIDENCE:\n"
            "Use only as factual source support. "
            "Do not treat editorial conclusions as evidence.\n"
            + supplement
        )

    return "\n\n".join(
        section
        for section in sections
        if section
    )
