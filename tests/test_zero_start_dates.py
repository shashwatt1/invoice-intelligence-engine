"""
Which date a zero start uses — tests/test_zero_start_dates.py (no DB).

The server decides "today" in UTC and refuses a start date after it. A browser east of UTC (IST at 03:09 on 31 Oct
is still 30 Oct 21:39 UTC) has a calendar a day ahead: sending its own "today" was rejected as the future, so the
dialog now sends a date only when the person picks one and the server applies its own default. These tests pin the
server's rule at the boundary, with a frozen clock.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.api.v1 import inventory as inventory_api
from app.core.exceptions import ValidationError


def frozen(monkeypatch, *, year=2026, month=10, day=30, hour=21, minute=39, second=0):
    """Pin the clock the route reads (and only that clock) to a UTC instant."""
    instant = datetime(year, month, day, hour, minute, second, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(inventory_api, "datetime", Clock)
    return instant


class TestTheServerDecidesToday:
    def test_no_date_means_the_servers_utc_today(self, monkeypatch):
        frozen(monkeypatch)
        assert inventory_api._checked_date(None) == (date(2026, 10, 30), date(2026, 10, 30))

    def test_the_ist_utc_boundary_a_browsers_own_today_a_day_ahead_is_refused_as_the_future(self, monkeypatch):
        frozen(monkeypatch)                                             # 03:09 IST on 31 Oct is 21:39 UTC on 30 Oct
        with pytest.raises(ValidationError) as refused:
            inventory_api._checked_date(date(2026, 10, 31))              # what a browser in IST used to send
        assert refused.value.detail == {"field": "initialization_date", "reason": "future_date"}
        assert refused.value.http_status == 422

    def test_the_utc_date_itself_is_accepted_as_a_chosen_date(self, monkeypatch):
        frozen(monkeypatch)
        assert inventory_api._checked_date(date(2026, 10, 30)) == (date(2026, 10, 30), date(2026, 10, 30))

    def test_an_earlier_chosen_date_is_accepted(self, monkeypatch):
        frozen(monkeypatch)
        assert inventory_api._checked_date(date(2026, 10, 1)) == (date(2026, 10, 30), date(2026, 10, 1))

    @pytest.mark.parametrize("instant, accepted, refused", [
        ({"hour": 23, "minute": 59, "second": 59}, date(2026, 10, 30), date(2026, 10, 31)),     # last second of 30 Oct UTC
        ({"day": 31, "hour": 0, "minute": 0, "second": 1}, date(2026, 10, 31), date(2026, 11, 1)),  # first second of 31 Oct UTC
    ])
    def test_future_dates_stay_refused_either_side_of_midnight_utc(self, monkeypatch, instant, accepted, refused):
        frozen(monkeypatch, **instant)
        assert inventory_api._checked_date(accepted)[1] == accepted
        with pytest.raises(ValidationError):
            inventory_api._checked_date(refused)

    def test_a_browser_behind_utc_is_unaffected(self, monkeypatch):
        frozen(monkeypatch, hour=3, minute=9)                           # 20:09 on 29 Oct in Pacific time, 03:09 UTC on 30 Oct
        assert inventory_api._checked_date(date(2026, 10, 29))[1] == date(2026, 10, 29)
        assert inventory_api._checked_date(None)[1] == date(2026, 10, 30)
