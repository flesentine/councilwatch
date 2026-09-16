import official_entities as oe


def test_mission_viejo_sources_include_economic_development_meeting_index():
    source_url = (
        "https://dms.missionviejo.gov/"
        "OnBaseAgendaOnline/Meetings"
    )

    assert source_url in oe.OFFICIAL_ENTITY_SOURCES["mission-viejo"]

    pages = [
        {
            "url": source_url,
            "text": (
                "Economic Development Committee Meeting\n"
                "Economic Development Committee\n"
                "Planning And Transportation Commission"
            ),
        }
    ]

    assert oe.find_official_support(
        "Economic Development Committee",
        "",
        "",
        pages,
    ) == source_url
