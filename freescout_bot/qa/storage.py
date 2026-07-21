from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import pandas as pd

from freescout_bot.qa.models import ScoredTicket

log = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS scored_tickets (
    conv_id          INTEGER PRIMARY KEY,
    ticket_number    TEXT,
    mailbox_name     TEXT,
    agent_name       TEXT,
    customer_message TEXT,
    agent_reply      TEXT,
    accuracy         INTEGER,
    clarity          INTEGER,
    tone             INTEGER,
    completeness     INTEGER,
    total_score      INTEGER,
    feedback         TEXT,
    customer_rating  INTEGER,
    rating_verdict   TEXT,
    rating_feedback  TEXT,
    run_date         TEXT,
    evaluated_at     TEXT
)
"""

_INSERT = """
INSERT OR IGNORE INTO scored_tickets VALUES (
    :conv_id, :ticket_number, :mailbox_name, :agent_name,
    :customer_message, :agent_reply,
    :accuracy, :clarity, :tone, :completeness, :total_score, :feedback,
    :customer_rating, :rating_verdict, :rating_feedback,
    :run_date, :evaluated_at
)
"""


class SQLiteStorage:
    """Persists QA evaluation results to a local SQLite database."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        with self._conn() as conn:
            conn.execute(_DDL)
        log.info("Storage ready: %s", self._db_path)

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_evaluated_ids(self) -> set[int]:
        with self._conn() as conn:
            rows = conn.execute("SELECT conv_id FROM scored_tickets").fetchall()
        return {row[0] for row in rows}

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
        df["run_date"] = pd.to_datetime(df["run_date"])
        df["week"]     = df["run_date"].dt.strftime("%Y-W%W")
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
        rv = ticket.rating_validation
        return {
            "conv_id":          ticket.conv_id,
            "ticket_number":    ticket.ticket_number,
            "mailbox_name":     ticket.mailbox_name,
            "agent_name":       ticket.agent_name,
            "customer_message": ticket.customer_message,
            "agent_reply":      ticket.agent_reply,
            "accuracy":         ticket.score.accuracy,
            "clarity":          ticket.score.clarity,
            "tone":             ticket.score.tone,
            "completeness":     ticket.score.completeness,
            "total_score":      ticket.score.total_score,
            "feedback":         ticket.score.feedback,
            "customer_rating":  ticket.customer_rating,
            "rating_verdict":   rv.verdict if rv else None,
            "rating_feedback":  rv.reason  if rv else None,
            "run_date":         run_date,
            "evaluated_at":     ticket.evaluated_at.isoformat(),
        }
