"""
Unit tests for app/modules/sql_interface/schedule_db.py

pyodbc is mocked throughout, so these run without a real SQL Server -
they check the query/parameter construction and the tool <-> ScheduleDB
wiring that the Schedule Advisor depends on.

Run from the repo root with:  python -m pytest tests/test_schedule_db.py -v
"""
import json
import sys
import types
import unittest
from datetime import date, time
from unittest.mock import MagicMock

# schedule_db.py does `try: import pyodbc except ImportError: pyodbc = None`
# at import time - install a fake module first so that succeeds everywhere,
# including machines with no ODBC driver installed.
if "pyodbc" not in sys.modules:
    _fake_pyodbc = types.ModuleType("pyodbc")
    _fake_pyodbc.connect = MagicMock()
    sys.modules["pyodbc"] = _fake_pyodbc

from app.modules.sql_interface import schedule_db as sdb  # noqa: E402


def _make_mock_conn(fetchall_return=None, fetchone_return=None, rowcount=0):
    """A mock connection usable as `with self._connect() as conn:`."""
    cursor = MagicMock()
    cursor.fetchall.return_value = fetchall_return or []
    cursor.fetchone.return_value = fetchone_return
    cursor.rowcount = rowcount

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn, cursor


class TestParsing(unittest.TestCase):
    def test_parse_date_accepts_iso_string(self):
        self.assertEqual(sdb._parse_date("2026-08-18"), date(2026, 8, 18))

    def test_parse_date_rejects_bad_format(self):
        with self.assertRaises(ValueError):
            sdb._parse_date("18/08/2026")

    def test_parse_time_accepts_hhmm(self):
        self.assertEqual(sdb._parse_time("10:00"), time(10, 0))

    def test_parse_time_rejects_bad_format(self):
        with self.assertRaises(ValueError):
            sdb._parse_time("10 AM")


class TestConnectionString(unittest.TestCase):
    def test_local_windows_auth_defaults(self):
        db = sdb.ScheduleDB(server="localhost", database="Tech", trusted_connection="yes")
        cs = db._connection_string()
        self.assertIn("SERVER=localhost", cs)
        self.assertIn("DATABASE=Tech", cs)
        self.assertIn("Trusted_Connection=yes", cs)
        self.assertIn("Encrypt=yes", cs)  # Driver 18 safe default
        self.assertIn("TrustServerCertificate=yes", cs)  # local self-signed cert safe default

    def test_azure_sql_auth(self):
        db = sdb.ScheduleDB(
            server="myserver.database.windows.net",
            database="Tech",
            trusted_connection="no",
            username="admin",
            password="s3cret",
        )
        cs = db._connection_string()
        self.assertIn("UID=admin", cs)
        self.assertIn("PWD=s3cret", cs)
        self.assertNotIn("Trusted_Connection", cs)


class TestScheduleDBQueries(unittest.TestCase):
    def setUp(self):
        self.db = sdb.ScheduleDB(server="localhost", database="Tech", trusted_connection="yes")

    def test_get_available_slots_maps_rows_and_parameterizes_query(self):
        conn, cursor = _make_mock_conn(
            fetchall_return=[(date(2026, 8, 18), time(10, 0)), (date(2026, 8, 18), time(14, 0))]
        )
        self.db._connect = MagicMock(return_value=conn)

        slots = self.db.get_available_slots(from_date="2026-08-18", position="Python Dev", limit=3)

        self.assertEqual(
            slots,
            [{"date": "2026-08-18", "time": "10:00"}, {"date": "2026-08-18", "time": "14:00"}],
        )
        args, _ = cursor.execute.call_args
        self.assertIn("SELECT TOP (?)", args[0])
        self.assertEqual(args[1], (3, "Python Dev", date(2026, 8, 18)))

    def test_validate_slot_true_when_available(self):
        conn, _ = _make_mock_conn(fetchone_return=(True,))
        self.db._connect = MagicMock(return_value=conn)
        self.assertTrue(self.db.validate_slot("2026-08-18", "10:00"))

    def test_validate_slot_false_when_taken(self):
        conn, _ = _make_mock_conn(fetchone_return=(False,))
        self.db._connect = MagicMock(return_value=conn)
        self.assertFalse(self.db.validate_slot("2026-08-18", "10:00"))

    def test_validate_slot_false_when_slot_does_not_exist(self):
        conn, _ = _make_mock_conn(fetchone_return=None)
        self.db._connect = MagicMock(return_value=conn)
        self.assertFalse(self.db.validate_slot("2026-08-18", "10:00"))

    def test_book_slot_succeeds_when_row_updated(self):
        conn, _ = _make_mock_conn(rowcount=1)
        self.db._connect = MagicMock(return_value=conn)
        self.assertTrue(self.db.book_slot("2026-08-18", "10:00"))

    def test_book_slot_fails_when_already_taken(self):
        conn, cursor = _make_mock_conn(rowcount=0)
        self.db._connect = MagicMock(return_value=conn)
        self.assertFalse(self.db.book_slot("2026-08-18", "10:00"))
        # the available=1 guard must be in the SQL itself (race-safety),
        # not just checked afterwards in Python
        args, _ = cursor.execute.call_args
        self.assertIn("available = 1", args[0])


class TestTools(unittest.TestCase):
    """Exercise the @tool wrappers exactly as the LLM agent would call them."""

    def test_check_availability_tool_returns_json_slots(self):
        mock_db = MagicMock()
        mock_db.get_available_slots.return_value = [{"date": "2026-08-18", "time": "10:00"}]
        t = sdb.build_check_availability_tool(db=mock_db)

        result = t.invoke({"from_date": "2026-08-18", "limit": 1})

        self.assertEqual(json.loads(result), {"slots": [{"date": "2026-08-18", "time": "10:00"}]})

    def test_check_availability_tool_reports_bad_date_as_json_error(self):
        mock_db = MagicMock()
        mock_db.get_available_slots.side_effect = ValueError("bad date")
        t = sdb.build_check_availability_tool(db=mock_db)

        result = t.invoke({"from_date": "not-a-date"})

        self.assertIn("error", json.loads(result))

    def test_validate_proposed_slot_tool(self):
        mock_db = MagicMock()
        mock_db.validate_slot.return_value = True
        t = sdb.build_validate_slot_tool(db=mock_db)

        result = t.invoke({"date": "2026-08-18", "time": "10:00"})

        self.assertEqual(json.loads(result), {"available": True})

    def test_book_interview_slot_tool_success(self):
        mock_db = MagicMock()
        mock_db.book_slot.return_value = True
        t = sdb.build_book_slot_tool(db=mock_db)

        result = t.invoke({"date": "2026-08-18", "time": "10:00"})

        self.assertEqual(json.loads(result), {"booked": True})

    def test_book_interview_slot_tool_already_taken(self):
        mock_db = MagicMock()
        mock_db.book_slot.return_value = False
        t = sdb.build_book_slot_tool(db=mock_db)

        result = t.invoke({"date": "2026-08-18", "time": "10:00"})

        parsed = json.loads(result)
        self.assertFalse(parsed["booked"])
        self.assertIn("reason", parsed)

    def test_build_schedule_tools_returns_three_tools(self):
        tools = sdb.build_schedule_tools(server="localhost", database="Tech")
        names = {t.name for t in tools}
        self.assertEqual(names, {"check_availability", "validate_proposed_slot", "book_interview_slot"})


if __name__ == "__main__":
    unittest.main()
