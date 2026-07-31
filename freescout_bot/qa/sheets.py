from __future__ import annotations

import logging
import sys
from pathlib import Path

import gspread
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from freescout_bot.qa.config import (
    GOOGLE_SCOPES, SHEET_NAME, Tabs,
    RAW_HEADERS, SUMMARY_HEADERS, TRENDS_HEADERS, WARNINGS_HEADERS,
)
from freescout_bot.qa.models import AgentSummary, PerformanceWarning, ScoredTicket

log = logging.getLogger(__name__)

_SETUP_INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════╗
║  SETUP REQUIRED: Google Sheets credentials not found         ║
╚══════════════════════════════════════════════════════════════╝

Expected file: {path}

Follow these steps once:
  1. Go to https://console.cloud.google.com
  2. Log in with supp.systeme.io@gmail.com
  3. Create a project → name it "QA Bot"
  4. Enable: Google Sheets API + Google Drive API
     (APIs & Services → Library → search each → Enable)
  5. APIs & Services → Credentials → Create Credentials
     → OAuth 2.0 Client IDs → Desktop App → name it "QA Script"
  6. Download the JSON → save as: scripts/credentials.json
  7. Run the script again — browser will open for a one-time login
"""


class SheetsExporter:
    """Handles all Google Sheets I/O for the QA evaluation pipeline."""

    def __init__(self, credentials_file: Path, token_file: Path) -> None:
        self._credentials_file = credentials_file
        self._token_file       = token_file
        self._client: gspread.Client | None      = None
        self._sheet:  gspread.Spreadsheet | None = None

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        self._client = self._build_client()
        self._sheet  = self._get_or_create_sheet()
        log.info("Connected to Google Sheet: %s", self.sheet_url)

    @property
    def sheet_url(self) -> str:
        return self._sheet.url if self._sheet else ""

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_evaluated_ids(self) -> set[int]:
        values = self._tab(Tabs.RAW_DATA).get_all_values()
        if len(values) <= 1:
            return set()
        return {int(row[1]) for row in values[1:] if len(row) > 1 and row[1].isdigit()}

    def get_historical_scores(self, exclude_run_date: str) -> dict[str, list[float]]:
        records    = self._tab(Tabs.WEEKLY_TRENDS).get_all_records()
        historical: dict[str, list[float]] = {}

        for record in records:
            if str(record.get("Run Date")) == exclude_run_date:
                continue
            agent = str(record.get("Agent", "")).strip()
            try:
                score = float(record["Avg Score"])
            except (KeyError, ValueError):
                continue
            historical.setdefault(agent, []).append(score)

        return historical

    # ── Write ─────────────────────────────────────────────────────────────────

    def write_raw_data(self, tickets: list[ScoredTicket], run_date: str) -> None:
        rows = [self._ticket_to_row(ticket, run_date) for ticket in tickets]
        self._tab(Tabs.RAW_DATA).append_rows(rows, value_input_option="USER_ENTERED")
        log.info("Written %d row(s) → '%s'", len(rows), Tabs.RAW_DATA)

    def write_agent_summary(self, summaries: list[AgentSummary]) -> None:
        ws = self._tab(Tabs.AGENT_SUMMARY)
        ws.clear()
        ws.append_row(SUMMARY_HEADERS, value_input_option="USER_ENTERED")
        ws.append_rows(
            [self._summary_to_row(s) for s in summaries],
            value_input_option="USER_ENTERED",
        )
        log.info("Updated '%s' — %d agent(s)", Tabs.AGENT_SUMMARY, len(summaries))

    def append_weekly_trends(
        self,
        summaries: list[AgentSummary],
        week_label: str,
        run_date: str,
    ) -> None:
        rows = [
            [
                week_label, run_date, s.agent_name, s.ticket_count,
                s.avg_score, s.avg_accuracy, s.avg_clarity,
                s.avg_tone, s.avg_completeness,
            ]
            for s in summaries
        ]
        self._tab(Tabs.WEEKLY_TRENDS).append_rows(rows, value_input_option="USER_ENTERED")
        log.info("Appended trends for %d agent(s) → '%s'", len(rows), Tabs.WEEKLY_TRENDS)

    def write_warnings(self, warnings: list[PerformanceWarning]) -> None:
        if not warnings:
            return
        rows = [
            [w.run_date, w.agent_name, w.historical_avg, w.current_avg, f"-{w.drop}", w.note]
            for w in warnings
        ]
        self._tab(Tabs.WARNINGS).append_rows(rows, value_input_option="USER_ENTERED")
        log.info("Written %d warning(s) → '%s'", len(rows), Tabs.WARNINGS)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_client(self) -> gspread.Client:
        if not self._credentials_file.exists():
            print(_SETUP_INSTRUCTIONS.format(path=self._credentials_file))
            sys.exit(1)

        creds: Credentials | None = None
        if self._token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self._token_file), GOOGLE_SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                log.info("Refreshing Google token...")
                creds.refresh(GoogleRequest())
            else:
                log.info("Opening browser for one-time Google login...")
                flow  = InstalledAppFlow.from_client_secrets_file(str(self._credentials_file), GOOGLE_SCOPES)
                creds = flow.run_local_server(port=0)
            self._token_file.write_text(creds.to_json())
            log.info("Token saved to %s — no login needed next time.", self._token_file.name)

        return gspread.authorize(creds)

    def _get_or_create_sheet(self) -> gspread.Spreadsheet:
        try:
            sheet = self._client.open(SHEET_NAME)
        except gspread.SpreadsheetNotFound:
            sheet = self._client.create(SHEET_NAME)
            log.info("Created new sheet: %s", SHEET_NAME)

        self._ensure_tabs(sheet)
        return sheet

    def _ensure_tabs(self, sheet: gspread.Spreadsheet) -> None:
        existing = {ws.title for ws in sheet.worksheets()}

        for tab, headers in [
            (Tabs.RAW_DATA,      RAW_HEADERS),
            (Tabs.AGENT_SUMMARY, SUMMARY_HEADERS),
            (Tabs.WEEKLY_TRENDS, TRENDS_HEADERS),
            (Tabs.WARNINGS,      WARNINGS_HEADERS),
        ]:
            if tab not in existing:
                ws = sheet.add_worksheet(title=tab, rows=2000, cols=max(len(headers), 10))
                ws.append_row(headers, value_input_option="USER_ENTERED")
                log.info("Created tab: %s", tab)

        if "Sheet1" in existing and len(existing) > 1:
            try:
                sheet.del_worksheet(sheet.worksheet("Sheet1"))
            except Exception:
                pass

    def _tab(self, name: str) -> gspread.Worksheet:
        return self._sheet.worksheet(name)

    @staticmethod
    def _ticket_to_row(ticket: ScoredTicket, run_date: str) -> list:
        rv = ticket.rating_validation
        return [
            run_date,
            ticket.conv_id,
            ticket.ticket_number,
            ticket.mailbox_name,
            ticket.agent_name,
            ticket.customer_message,
            ticket.agent_reply,
            ticket.score.accuracy,
            ticket.score.clarity,
            ticket.score.tone,
            ticket.score.completeness,
            ticket.score.total_score,
            ticket.score.feedback,
            ticket.customer_rating or "",
            rv.verdict if rv else "",
            rv.reason  if rv else "",
        ]

    @staticmethod
    def _summary_to_row(summary: AgentSummary) -> list:
        return [
            summary.agent_name,
            summary.ticket_count,
            summary.avg_score,
            summary.avg_accuracy,
            summary.avg_clarity,
            summary.avg_tone,
            summary.avg_completeness,
            summary.weakest_area,
            summary.status,
        ]
