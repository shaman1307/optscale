"""OptScale REST API unit tests run in UTC.

BreakdownExpenseController.update_params snaps request windows to UTC day
boundaries, and ClickHouse expense dates are compared as UTC calendar days.
Naive datetime(...).timestamp() fixtures are therefore only consistent when the
process timezone is UTC; otherwise CET/CEST shifts the unix timestamps onto the
previous UTC day and expense totals become 0 (see first_seen/last_seen
breakdown tests).
"""
import os
import time

os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()
