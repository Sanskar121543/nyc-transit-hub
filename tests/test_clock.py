import calendar
import datetime as dt
import unittest

from transit_hub import clock


def utc(*parts):
    return calendar.timegm(parts)


class ClockTests(unittest.TestCase):
    def test_offset_switches_at_the_us_transition_instants(self):
        self.assertEqual(clock.utc_offset(utc(2026, 3, 8, 6, 59, 59)), clock.STANDARD)
        self.assertEqual(clock.utc_offset(utc(2026, 3, 8, 7, 0, 0)), clock.DAYLIGHT)
        self.assertEqual(clock.utc_offset(utc(2026, 11, 1, 5, 59, 59)), clock.DAYLIGHT)
        self.assertEqual(clock.utc_offset(utc(2026, 11, 1, 6, 0, 0)), clock.STANDARD)

    def test_local_date_follows_new_york_not_utc(self):
        self.assertEqual(clock.local_date(utc(2026, 11, 21, 3, 0, 0)), dt.date(2026, 11, 20))
        self.assertEqual(clock.local_date(utc(2026, 7, 4, 3, 59, 0)), dt.date(2026, 7, 3))
        self.assertEqual(clock.local_date(utc(2026, 7, 4, 4, 0, 0)), dt.date(2026, 7, 4))

    def test_service_day_start_is_noon_minus_twelve_hours(self):
        self.assertEqual(clock.service_day_start(dt.date(2026, 11, 20)), utc(2026, 11, 20, 5, 0, 0))
        self.assertEqual(clock.service_day_start(dt.date(2026, 7, 4)), utc(2026, 7, 4, 4, 0, 0))
        self.assertEqual(clock.service_day_start(dt.date(2026, 3, 8)), utc(2026, 3, 8, 4, 0, 0))
        self.assertEqual(clock.service_day_start(dt.date(2026, 11, 1)), utc(2026, 11, 1, 5, 0, 0))

    def test_format_clock(self):
        self.assertEqual(clock.format_clock(utc(2026, 11, 20, 18, 5, 0)), "1:05 PM")
        self.assertEqual(clock.format_clock(utc(2026, 11, 20, 5, 0, 0)), "12:00 AM")

    def test_parse_clock_rolls_forward_when_the_time_has_passed(self):
        now = utc(2026, 11, 20, 18, 0, 0)
        self.assertEqual(clock.parse_clock("14:30", now), utc(2026, 11, 20, 19, 30, 0))
        self.assertEqual(clock.parse_clock("12:30", now), utc(2026, 11, 21, 17, 30, 0))

    def test_parse_clock_rejects_out_of_range_values(self):
        now = utc(2026, 11, 20, 18, 0, 0)
        for text in ("99:99", "24:00", "12:60"):
            with self.assertRaises(ValueError):
                clock.parse_clock(text, now)


if __name__ == "__main__":
    unittest.main()
