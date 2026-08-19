CouncilWatch private five-story review pilot

PURPOSE
Generate one unpublished test story for the latest READY meeting in:
- Rancho Santa Margarita
- Aliso Viejo
- Mission Viejo
- Lake Forest
- Laguna Niguel

It reads:
  /home/flese/councilwatch-pi-v2/data/councilwatch.db

It does NOT modify or publish to the existing RSM website.

PIPELINE
recording
-> audio extraction
-> Gemini source notes
-> official agenda/source text
-> draft article
-> fact/name/number/repetition audit
-> READY FOR REVIEW

PRIVATE REVIEW PAGE
  http://raspberrypi.local:8080

This is intended for the local LAN. Do not port-forward port 8080.

Completed draft JSON files are skipped on rerun.
Retry failures:
  ./.venv/bin/python generate_five.py

Force all five to regenerate:
  ./.venv/bin/python generate_five.py --force

Logs:
  journalctl --user -u councilwatch-generate-five.service -n 200 --no-pager
