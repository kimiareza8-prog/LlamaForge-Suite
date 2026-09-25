from datetime import datetime

import pytest
from llamaforge.core.workspace import CalendarStore


def test_multiday_event_spans_visible_days_and_excludes_midnight_end(tmp_path):
    cal = CalendarStore(tmp_path)
    event = cal.write('create', {'title': 'سفر', 'start': '2026-09-24T10:00', 'end': '2026-09-27T00:00'})
    month = cal.month(1405, 7)
    occupied = [d['gregorian'] for d in month['days'] if any(e['id'] == event['id'] for e in d['events'])]
    assert occupied == ['2026-09-24', '2026-09-25', '2026-09-26']
    assert month['events'][0]['end'] == event['end']


def test_month_clips_long_event_and_reports_overflow_without_losing_edit_data(tmp_path):
    cal = CalendarStore(tmp_path)
    for i in range(10):
        cal.write('create', {'title': str(i), 'start': '2026-09-01T10:00', 'end': '2026-11-01T00:00'})
    month = cal.month(1405, 7)
    assert len(month['events']) == 10
    assert all(d['event_count'] == 10 for d in month['days'])
    assert all(len(d['events']) <= 8 for d in month['days'])


@pytest.mark.parametrize('operation', ['create', 'update'])
def test_calendar_rejects_blank_title_and_zero_duration(tmp_path, operation):
    cal = CalendarStore(tmp_path)
    original = cal.write('create', {'title': 'جلسه', 'start': '2026-09-25T10:00'})
    with pytest.raises(ValueError, match='title'):
        cal.write(operation, {'id': original['id'], 'title': '  ', 'start': '2026-09-25T10:00'})
    with pytest.raises(ValueError, match='after start'):
        cal.write(operation, {'id': original['id'], 'title': 'جلسه', 'start': '2026-09-25T10:00', 'end': '2026-09-25T10:00'})
    assert cal.list_events()[0] == original


def test_imported_offsets_are_sorted_by_instant(tmp_path):
    cal = CalendarStore(tmp_path)
    cal.import_snapshot({'events': [
        {'id': 'later', 'title': 'later', 'start': '2026-09-25T07:00+00:00'},
        {'id': 'earlier', 'title': 'earlier', 'start': '2026-09-25T10:00+04:00'},
    ]})
    assert [e['id'] for e in cal.list_events()] == ['earlier', 'later']
