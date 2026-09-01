"""
SQL interface for the Schedule Advisor.
---------------------------------------
db_Tech.sql is a SQL SERVER script (not SQLite) that creates a `Tech`
database with a dbo.Schedule table:

    ScheduleID INT IDENTITY(1,1) PRIMARY KEY,
    [date]     DATE       NOT NULL,
    [time]     TIME(0)    NOT NULL,
    position   VARCHAR(20) NOT NULL,   -- 'Python Dev' / 'Sql Dev' / 'Analyst' / 'ML'
    available  BIT        NOT NULL

Five things live here, same pattern as embedding.py:

1. ScheduleDB                    - connects to SQL Server, queries/updates
                                    availability.
2. build_check_availability_tool - nearest N open slots from a date.
3. build_validate_slot_tool      - is this ONE candidate-proposed slot open?
4. build_book_slot_tool          - reserve one specific slot (available -> 0).
5. build_schedule_tools          - convenience: all three tools above,
                                    sharing one ScheduleDB connection, ready
                                    for Agent(sch_tools=build_schedule_tools()).

These map directly onto schedule_advisor.md's "Query & Validate" (tools 2+3)
and the Main Agent's "Option 3: Schedule an Interview" (tool 4) - this is
the "Function Calling" step of the project.

SETUP REQUIRED before this works:
* Create the `Tech` database / dbo.Schedule table on a real SQL Server
  instance and seed it - either:
    - db_Tech.sql, run once via SSMS/sqlcmd against a local SQL Server
      (Express, Developer, or Docker), or
    - db_Tech_AzureSQL.sql against an Azure SQL Database (free tier) - the
      same table + seed, minus the CREATE DATABASE/USE Master part, since
      an Azure SQL "database" is already the connection target.
* Install the ODBC Driver for SQL Server (e.g. "ODBC Driver 18 for SQL
  Server") - a Windows/system-level install, not a pip package.
* pip install pyodbc
* Add connection details to .env - two supported setups:

  Local SQL Server with Windows Authentication (SSMS default):
    SQL_SERVER=localhost                  # or ".\\SQLEXPRESS"
    SQL_DATABASE=Tech
    SQL_TRUSTED_CONNECTION=yes            # default
    SQL_DRIVER=ODBC Driver 18 for SQL Server

  Azure SQL Database (free tier) with SQL Authentication:
    SQL_SERVER=<your-server>.database.windows.net
    SQL_DATABASE=Tech
    SQL_TRUSTED_CONNECTION=no
    SQL_USERNAME=<admin login you set when creating the server>
    SQL_PASSWORD=<its password>
    SQL_DRIVER=ODBC Driver 18 for SQL Server

  Optional, both setups (sane defaults already applied):
    SQL_ENCRYPT=yes                       # Azure requires this
    SQL_TRUST_SERVER_CERTIFICATE=yes      # needed locally with Driver 18
                                           # + a self-signed/dev cert;
                                           # safe to set "no" against Azure,
                                           # which has a real CA certificate
"""

import json
import os
from datetime import date as date_cls, datetime, time as time_cls
from dotenv import load_dotenv
from langchain.tools import tool

try:
    import pyodbc
except ImportError:
    pyodbc = None  # only raise when someone actually tries to connect


def _parse_date(value):
    """Accept a datetime.date already, or a 'YYYY-MM-DD' string."""
    if isinstance(value, date_cls):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"'{value}' is not a valid date - expected YYYY-MM-DD.")


def _parse_time(value):
    """Accept a datetime.time already, or an 'HH:MM' (24h) string."""
    if isinstance(value, time_cls):
        return value
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except ValueError:
        raise ValueError(f"'{value}' is not a valid time - expected 24h HH:MM.")


class ScheduleDB:
    """
    Thin wrapper around a SQL Server connection for the Schedule Advisor.
    Works against either a local SQL Server (Windows Auth) or an Azure SQL
    Database (SQL Auth) - see the module docstring for the .env values each
    setup needs.
    """

    def __init__(
        self,
        server=None,
        database=None,
        trusted_connection=None,
        username=None,
        password=None,
        driver=None,
        encrypt=None,
        trust_server_certificate=None,
    ):
        if pyodbc is None:
            raise ImportError("pyodbc is not installed. Run: pip install pyodbc")

        self.server = server or os.environ.get("SQL_SERVER", "localhost")
        self.database = database or os.environ.get("SQL_DATABASE", "Tech")
        self.driver = driver or os.environ.get("SQL_DRIVER", "ODBC Driver 18 for SQL Server")

        trusted = trusted_connection
        if trusted is None:
            trusted = os.environ.get("SQL_TRUSTED_CONNECTION", "yes")
        self.trusted_connection = str(trusted).lower() in ("yes", "true", "1")

        self.username = username or os.environ.get("SQL_USERNAME")
        self.password = password or os.environ.get("SQL_PASSWORD")

        # Driver 18 requires encryption by default - set explicitly so both
        # local dev (self-signed/dev cert) and Azure SQL (real cert) work
        # without a surprise "certificate chain" connection error.
        encrypt = encrypt if encrypt is not None else os.environ.get("SQL_ENCRYPT", "yes")
        self.encrypt = "yes" if str(encrypt).lower() in ("yes", "true", "1") else "no"

        trust_cert = (
            trust_server_certificate
            if trust_server_certificate is not None
            else os.environ.get("SQL_TRUST_SERVER_CERTIFICATE", "yes")
        )
        self.trust_server_certificate = "yes" if str(trust_cert).lower() in ("yes", "true", "1") else "no"

    def _connection_string(self):
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.server}",
            f"DATABASE={self.database}",
            f"Encrypt={self.encrypt}",
            f"TrustServerCertificate={self.trust_server_certificate}",
        ]
        if self.trusted_connection:
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={self.username}")
            parts.append(f"PWD={self.password}")
        return ";".join(parts)

    def _connect(self):
        return pyodbc.connect(self._connection_string())

    def get_available_slots(self, from_date, position="Python Dev", limit=3):
        """
        Nearest `available = 1` slots for a position, on/after from_date.
        Returns a list of {"date": "YYYY-MM-DD", "time": "HH:MM"} dicts,
        nearest first.
        """
        from_date = _parse_date(from_date)
        query = """
            SELECT TOP (?) [date], [time]
            FROM dbo.Schedule
            WHERE available = 1
              AND position = ?
              AND [date] >= ?
            ORDER BY [date], [time]
        """
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (limit, position, from_date))
            rows = cursor.fetchall()

        return [
            {"date": row[0].isoformat(), "time": row[1].strftime("%H:%M")}
            for row in rows
        ]

    def validate_slot(self, slot_date, slot_time, position="Python Dev"):
        """Is this ONE specific date+time currently available?"""
        slot_date = _parse_date(slot_date)
        slot_time = _parse_time(slot_time)
        query = """
            SELECT available
            FROM dbo.Schedule
            WHERE [date] = ? AND [time] = ? AND position = ?
        """
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (slot_date, slot_time, position))
            row = cursor.fetchone()

        if row is None:
            return False  # no such slot in the schedule at all
        return bool(row[0])

    def book_slot(self, slot_date, slot_time, position="Python Dev"):
        """
        Reserve one specific slot (available 1 -> 0). The `available = 1`
        guard in the WHERE clause makes this safe against a race with
        another candidate booking the same slot at the same time - only
        one UPDATE can ever win.

        Returns True if this call reserved the slot, False if it was
        already unavailable/booked, or does not exist.
        """
        slot_date = _parse_date(slot_date)
        slot_time = _parse_time(slot_time)
        query = """
            UPDATE dbo.Schedule
            SET available = 0
            WHERE [date] = ? AND [time] = ? AND position = ? AND available = 1
        """
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (slot_date, slot_time, position))
            updated = cursor.rowcount

        return updated > 0


def build_check_availability_tool(position="Python Dev", db=None, **db_kwargs):
    """
    Factory that returns a ready-to-use @tool bound to a ScheduleDB
    instance - same pattern as build_search_job_description_tool in
    app/modules/embedding.py. Pass an existing `db=` to share one
    connection's config with other tools (see build_schedule_tools).
    """
    db = db or ScheduleDB(**db_kwargs)

    @tool
    def check_availability(from_date: str, limit: int = 3) -> str:
        """
        ## from_date parameter
        Earliest date to search from, format YYYY-MM-DD. Resolve any
        relative date the candidate mentioned (e.g. "next Thursday") to a
        real calendar date BEFORE calling this tool - use today's date as
        the reference point. If the candidate gave no preference at all,
        pass next Sunday's date to get general availability for next week.

        ## limit parameter (optional, default 3)
        How many available slots to return.

        ## Returns
        A JSON formatted string with the nearest available interview slots
        for the Python Dev position, e.g.:
        {"slots": [{"date": "2026-08-13", "time": "10:00"}, ...]}
        If the list is empty, there is no availability in the near future -
        say so honestly, do not invent a slot.

        ## Example
        Input: {"from_date": "2026-08-13", "limit": 3}
        Output: {"slots": [{"date": "2026-08-13", "time": "10:00"}, {"date": "2026-08-13", "time": "14:00"}, {"date": "2026-08-16", "time": "09:00"}]}
        """
        try:
            slots = db.get_available_slots(from_date=from_date, position=position, limit=limit)
            return json.dumps({"slots": slots})
        except Exception as e:
            return json.dumps({"error": str(e)})

    return check_availability


def build_validate_slot_tool(position="Python Dev", db=None, **db_kwargs):
    """
    Factory for the @tool that checks ONE specific candidate-proposed
    date+time, per schedule_advisor.md's "validate candidate-suggested
    times" responsibility.
    """
    db = db or ScheduleDB(**db_kwargs)

    @tool
    def validate_proposed_slot(date: str, time: str) -> str:
        """
        ## date / time parameters
        A specific date+time the candidate proposed themselves.
        date format YYYY-MM-DD, time format 24h HH:MM.

        ## Returns
        JSON formatted string: {"available": true} or {"available": false}.
        Call this BEFORE confirming a candidate-suggested time. If false,
        call check_availability to offer real alternatives instead of
        confirming a slot that doesn't exist or is already taken.

        ## Example
        Input: {"date": "2026-08-18", "time": "10:00"}
        Output: {"available": true}
        """
        try:
            available = db.validate_slot(slot_date=date, slot_time=time, position=position)
            return json.dumps({"available": bool(available)})
        except Exception as e:
            return json.dumps({"error": str(e)})

    return validate_proposed_slot


def build_book_slot_tool(position="Python Dev", db=None, **db_kwargs):
    """
    Factory for the @tool that finalizes a slot once the candidate has
    confirmed it - "Option 3: Schedule an Interview" in main_agent.md.
    """
    db = db or ScheduleDB(**db_kwargs)

    @tool
    def book_interview_slot(date: str, time: str) -> str:
        """
        ## date / time parameters
        The exact slot to reserve, already confirmed with the candidate.
        date format YYYY-MM-DD, time format 24h HH:MM.

        ## Returns
        JSON formatted string. {"booked": true} on success. {"booked":
        false, "reason": "..."} if it was no longer available (e.g. taken
        in the meantime, or never existed) - in that case call
        check_availability again and offer new options instead of telling
        the candidate they're confirmed.

        ## Example
        Input: {"date": "2026-08-18", "time": "10:00"}
        Output: {"booked": true}
        """
        try:
            booked = db.book_slot(slot_date=date, slot_time=time, position=position)
            if booked:
                return json.dumps({"booked": True})
            return json.dumps({"booked": False, "reason": "Slot is no longer available or does not exist."})
        except Exception as e:
            return json.dumps({"error": str(e)})

    return book_interview_slot


def build_schedule_tools(position="Python Dev", **db_kwargs):
    """
    Convenience factory: builds ONE ScheduleDB connection and returns all
    three tools bound to it, ready to drop straight into Agent(sch_tools=...).

    Usage (e.g. in app/main.py, when constructing the Agent):
        from app.modules.sql_interface.schedule_db import build_schedule_tools
        agent = Agent(..., sch_tools=build_schedule_tools())
    """
    db = ScheduleDB(**db_kwargs)
    return [
        build_check_availability_tool(position=position, db=db),
        build_validate_slot_tool(position=position, db=db),
        build_book_slot_tool(position=position, db=db),
    ]


# ---- run this file directly for a quick manual connection test ----
# Example: python -m app.modules.sql_interface.schedule_db
if __name__ == "__main__":
    load_dotenv()
    db = ScheduleDB()
    today = date_cls.today().isoformat()
    print(f"Querying available 'Python Dev' slots from {today}...")
    print(db.get_available_slots(from_date=today))
