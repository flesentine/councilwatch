CouncilWatch Pi v2 — normalized 5-city detector

WHAT THIS DOES
- Checks Rancho Santa Margarita, Aliso Viejo, Mission Viejo, Lake Forest, and Laguna Niguel.
- Normalizes the latest completed council meeting and, when detectable, the next upcoming meeting.
- Stores state in data/councilwatch.db.
- Re-running updates existing records instead of duplicating them.
- Does NOT transcribe, call Gemini/OpenAI, generate stories, or publish anything yet.

SOURCE STRATEGY
- RSM: Granicus
- Aliso Viejo: Granicus
- Mission Viejo: Granicus archive embedded by the City's official site
- Lake Forest: official agenda page + official YouTube archive (best effort) + PublicInput for upcoming
- Laguna Niguel: CivicEngage Agenda Center + official YouTube archive probe (best effort)

INSTALL ON PI
  chmod +x install_pi.sh run.sh status.py install_timer.sh
  ./install_pi.sh
  ./run.sh

VIEW STORED STATE
  ./.venv/bin/python status.py

AFTER THE MANUAL RUN LOOKS GOOD
  ./install_timer.sh

The timer checks around 7:05 AM and 7:05 PM in the Raspberry Pi's local timezone.
