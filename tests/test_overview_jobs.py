import datetime as dt

from jobscout.overview_jobs import in_batch_window


def test_batch_window_is_new_york_morning_and_tracks_dst():
    utc = dt.timezone.utc
    assert not in_batch_window(dt.datetime(2026, 10, 2, 9, 59, tzinfo=utc))
    assert in_batch_window(dt.datetime(2026, 10, 2, 10, 0, tzinfo=utc))
    assert in_batch_window(dt.datetime(2026, 10, 2, 14, 59, tzinfo=utc))
    assert not in_batch_window(dt.datetime(2026, 10, 2, 15, 0, tzinfo=utc))
    assert in_batch_window(dt.datetime(2027, 1, 2, 11, 0, tzinfo=utc))
