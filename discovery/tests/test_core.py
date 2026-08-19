
from pathlib import Path
from unittest.mock import patch

from sources import granicus
from state import StateDB

GRANICUS_HTML = """
<table>
<tr><td>City Council Regular Meeting</td><td>Aug 12, 2026</td><td>01h 25m</td>
<td><a href='AgendaViewer.php?view_id=2&clip_id=976'>Agenda</a></td>
<td><a href='MediaPlayer.php?view_id=2&clip_id=976'>Video</a></td></tr>
<tr><td>Planning Commission Regular Meeting</td><td>Aug 5, 2026</td>
<td><a href='MediaPlayer.php?view_id=2&clip_id=970'>Video</a></td></tr>
<tr><td>City Council Regular Meeting Canceled</td><td>Jul 22, 2026</td>
<td><a href='MediaPlayer.php?view_id=2&clip_id=960'>Video</a></td></tr>
</table>
"""

def test_granicus_filters_and_normalizes():
    city = {'slug':'rsm','timezone':'America/Los_Angeles','source_url':'https://example.com/ViewPublisher.php?view_id=2','meeting_terms':['city council'],'exclude_terms':['planning commission']}
    with patch('sources.granicus.fetch', return_value=(city['source_url'], GRANICUS_HTML)):
        result = granicus.discover(city)
    m = result['latest_completed']
    assert m.external_id == '976'
    assert m.meeting_date == '2026-08-12'
    assert m.recording_status == 'found'
    assert 'AgendaViewer.php' in m.agenda_url

def test_db_is_idempotent(tmp_path):
    db = StateDB(tmp_path/'state.db')
    meeting = {
        'city_slug':'rsm','external_id':'976','meeting_date':'2026-08-12','title':'City Council Regular Meeting',
        'kind':'completed','status':'ready','recording_status':'found','source_url':'x','agenda_url':'a','recording_url':'v','notes':''
    }
    assert db.upsert_meeting(meeting) is True
    assert db.upsert_meeting(meeting) is False
    assert len(db.recent()) == 1
