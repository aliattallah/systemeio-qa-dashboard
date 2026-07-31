from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import pandas as pd

from freescout_bot.qa.models import ScoredTicket

log = logging.getLogger(__name__)

_DDL_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
)
"""

_DDL = """
CREATE TABLE IF NOT EXISTS scored_tickets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id             INTEGER NOT NULL,
    agent_id            INTEGER NOT NULL,
    ticket_number       TEXT,
    mailbox_name        TEXT,
    agent_name          TEXT,
    customer_message    TEXT,
    agent_reply         TEXT,
    topic               TEXT,
    accuracy            INTEGER,
    accuracy_note       TEXT,
    clarity             INTEGER,
    clarity_note        TEXT,
    tone                INTEGER,
    tone_note           TEXT,
    completeness        INTEGER,
    completeness_note   TEXT,
    topic_variety       INTEGER,
    topic_variety_note  TEXT,
    total_score         REAL,
    feedback            TEXT,
    resolution_verdict  TEXT,
    resolution_reason   TEXT,
    conv_date           TEXT,
    run_date            TEXT,
    evaluated_at        TEXT,
    UNIQUE(conv_id, agent_id)
)
"""

_INSERT = """
INSERT OR IGNORE INTO scored_tickets (
    conv_id, agent_id, ticket_number, mailbox_name, agent_name,
    customer_message, agent_reply,
    topic,
    accuracy, accuracy_note,
    clarity, clarity_note,
    tone, tone_note,
    completeness, completeness_note,
    topic_variety, topic_variety_note,
    total_score, feedback,
    resolution_verdict, resolution_reason,
    conv_date, run_date, evaluated_at
) VALUES (
    :conv_id, :agent_id, :ticket_number, :mailbox_name, :agent_name,
    :customer_message, :agent_reply,
    :topic,
    :accuracy, :accuracy_note,
    :clarity, :clarity_note,
    :tone, :tone_note,
    :completeness, :completeness_note,
    :topic_variety, :topic_variety_note,
    :total_score, :feedback,
    :resolution_verdict, :resolution_reason,
    :conv_date, :run_date, :evaluated_at
)
"""


class SQLiteStorage:
    """Persists QA evaluation results to a local SQLite database."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        with self._conn() as conn:
            conn.execute(_DDL_META)
            conn.execute(_DDL)
        log.info("Storage ready: %s", self._db_path)

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_evaluated_ids(self) -> set[tuple[int, int]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT conv_id, agent_id FROM scored_tickets").fetchall()
        return {(row[0], row[1]) for row in rows}

    def get_last_run_date(self) -> str | None:
        """Returns the date of the last completed run, or None if first run."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'last_run_date'"
            ).fetchone()
        return row[0] if row else None

    def set_last_run_date(self, date: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_run_date', ?)",
                (date,),
            )

    def get_agent_counts(self) -> dict[str, int]:
        """Returns how many tickets are already scored per agent in the DB."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT agent_name, COUNT(*) FROM scored_tickets GROUP BY agent_name"
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def get_historical_scores(self, exclude_run_date: str) -> dict[str, list[float]]:
        """Returns historical avg scores per agent, excluding the current run date."""
        query = """
            SELECT agent_name, run_date, AVG(total_score) as avg_score
            FROM scored_tickets
            WHERE run_date != ?
            GROUP BY agent_name, run_date
        """
        with self._conn() as conn:
            rows = conn.execute(query, (exclude_run_date,)).fetchall()

        historical: dict[str, list[float]] = {}
        for agent, _, avg in rows:
            historical.setdefault(agent, []).append(float(avg))
        return historical

    def load_dataframe(self) -> pd.DataFrame:
        with self._conn() as conn:
            df = pd.read_sql("SELECT * FROM scored_tickets ORDER BY run_date DESC", conn)
        df["run_date"]  = pd.to_datetime(df["run_date"])
        df["week"]      = df["run_date"].dt.strftime("%Y-W%W")
        if "conv_date" in df.columns:
            df["conv_date"] = pd.to_datetime(df["conv_date"], errors="coerce")
        return df

    # ── Write ─────────────────────────────────────────────────────────────────

    def save_tickets(self, tickets: list[ScoredTicket], run_date: str) -> None:
        rows = [self._to_record(ticket, run_date) for ticket in tickets]
        with self._conn() as conn:
            conn.executemany(_INSERT, rows)
        log.info("Saved %d ticket(s) to %s", len(rows), self._db_path.name)

    # ── Private helpers ───────────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _to_record(ticket: ScoredTicket, run_date: str) -> dict:
        rv = ticket.resolution_verdict
        return {
            "conv_id":             ticket.conv_id,
            "agent_id":            ticket.agent_id,
            "ticket_number":       ticket.ticket_number,
            "mailbox_name":        ticket.mailbox_name,
            "agent_name":          ticket.agent_name,
            "customer_message":    ticket.customer_message,
            "agent_reply":         ticket.agent_reply,
            "topic":               ticket.score.topic,
            "accuracy":            ticket.score.accuracy,
            "accuracy_note":       ticket.score.accuracy_note,
            "clarity":             ticket.score.clarity,
            "clarity_note":        ticket.score.clarity_note,
            "tone":                ticket.score.tone,
            "tone_note":           ticket.score.tone_note,
            "completeness":        ticket.score.completeness,
            "completeness_note":   ticket.score.completeness_note,
            "topic_variety":       ticket.score.topic_variety,
            "topic_variety_note":  ticket.score.topic_variety_note,
            "total_score":         ticket.score.total_score,
            "feedback":            ticket.score.feedback,
            "resolution_verdict":  rv.verdict if rv else None,
            "resolution_reason":   rv.reason  if rv else None,
            "conv_date":           ticket.conv_date,
            "run_date":            run_date,
            "evaluated_at":        ticket.evaluated_at.isoformat(),
        }
