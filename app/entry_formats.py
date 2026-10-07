"""Dates and times typed by the user: 20261006 / 2026-10-06 and 0929 / 09:29 / 092900."""

import re
from datetime import datetime, time

DATE_HINT = "YYYYMMDD, e.g. 20261006"
TIME_HINT = "HHMM, e.g. 0929"


def parse_date_entry(text) -> datetime:
    match = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", str(text).strip())
    if not match:
        raise ValueError(f"Date must be {DATE_HINT}")
    return datetime(*(int(part) for part in match.groups()))   # ValueError for 20261332


def parse_time_entry(text) -> time:
    value = str(text).strip()
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", value):
        parts = value.split(":")
    elif re.fullmatch(r"\d{3,4}|\d{6}", value):                 # 929, 0929, 092930
        digits = value.zfill(4) if len(value) < 6 else value
        parts = [digits[0:2], digits[2:4], digits[4:6] or "0"]
    else:
        raise ValueError(f"Start time must be {TIME_HINT}")
    return time(*(int(part) for part in parts))                 # ValueError for 2575
