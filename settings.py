from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

PI_DB = Path(os.getenv("COUNCILWATCH_DB", "/home/flese/councilwatch-pi-v2/data/councilwatch.db"))
DRAFTS = ROOT / "drafts"
WORK = ROOT / "work"
STATUS_FILE = ROOT / "status.json"
DRAFTS.mkdir(exist_ok=True)
WORK.mkdir(exist_ok=True)

CITY_NAMES = {
    "rsm": "Rancho Santa Margarita",
    "aliso-viejo": "Aliso Viejo",
    "mission-viejo": "Mission Viejo",
    "lake-forest": "Lake Forest",
    "laguna-niguel": "Laguna Niguel",
}

def _first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return default

GEMINI_API_KEY = _first(
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"
)
TRANSCRIPT_MODEL = _first(
    "GEMINI_TRANSCRIPT_MODEL", "GEMINI_TRANSCRIBE_MODEL", "TRANSCRIPT_MODEL", "GEMINI_MODEL",
    default="gemini-3.1-flash-lite",
)

TRANSCRIPT_FALLBACK_MODELS = [
    model.strip()
    for model in _first(
        "GEMINI_TRANSCRIPT_FALLBACK_MODELS",
        default="gemini-3.5-flash,gemini-2.5-flash",
    ).split(",")
    if model.strip()
]
STORY_MODEL = _first(
    "GEMINI_STORY_MODEL", "STORY_MODEL", "GEMINI_MODEL",
    default="gemini-3.1-flash-lite",
)
KEEP_MEDIA = os.getenv("KEEP_MEDIA", "").strip().lower() in {"1", "true", "yes"}

NTFY_SERVER = _first(
    "NTFY_SERVER",
    default="https://ntfy.sh",
)

NTFY_TOPIC = _first(
    "NTFY_TOPIC",
)

COUNCILWATCH_REVIEW_BASE_URL = _first(
    "COUNCILWATCH_REVIEW_BASE_URL",
)


BUTTONDOWN_API_KEY = _first(
    "BUTTONDOWN_API_KEY",
)

BUTTONDOWN_API_BASE = _first(
    "BUTTONDOWN_API_BASE",
    default="https://api.buttondown.com/v1",
)
