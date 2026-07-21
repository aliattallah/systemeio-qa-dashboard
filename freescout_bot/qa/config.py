from __future__ import annotations

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPTS_DIR: Path = Path(__file__).parent.parent.parent / "scripts"

# ── Google Sheets ─────────────────────────────────────────────────────────────

SHEET_NAME = "QA Evaluations"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class Tabs:
    RAW_DATA      = "Raw Data"
    AGENT_SUMMARY = "Agent Summary"
    WEEKLY_TRENDS = "Weekly Trends"
    WARNINGS      = "Warnings"


RAW_HEADERS = [
    "Date", "Ticket ID", "Ticket #", "Mailbox", "Agent",
    "Customer Message", "Agent Reply",
    "Accuracy (0-3)", "Clarity (0-3)", "Tone (0-2)", "Completeness (0-2)",
    "Total Score (0-10)", "AI Feedback",
    "Customer Rating", "Rating Verdict", "Rating Feedback",
]

SUMMARY_HEADERS = [
    "Agent", "Tickets", "Avg Score",
    "Avg Accuracy", "Avg Clarity", "Avg Tone", "Avg Completeness",
    "Weakest Area", "Status",
]

TRENDS_HEADERS = [
    "Week", "Run Date", "Agent", "Tickets", "Avg Score",
    "Avg Accuracy", "Avg Clarity", "Avg Tone", "Avg Completeness",
]

WARNINGS_HEADERS = [
    "Run Date", "Agent", "Historical Avg", "Current Avg", "Drop", "Note",
]

# ── FreeScout ─────────────────────────────────────────────────────────────────

BOT_USER_ID = 23

MAILBOX_NAMES: dict[int, str] = {
    3:  "English",
    16: "Spanish",
    17: "French",
    18: "Italian",
    19: "Japanese",
    21: "Dutch",
    22: "Portuguese",
    31: "Arabic",
    30: "Chinese",
}

ALL_MAILBOX_IDS: list[int] = list(MAILBOX_NAMES.keys())

# ── Scoring ───────────────────────────────────────────────────────────────────

SCORE_MAXIMUMS: dict[str, int] = {
    "accuracy":     3,
    "clarity":      3,
    "tone":         2,
    "completeness": 2,
}

TARGET_MIN_SCORE       = 6.0
WARNING_DROP_THRESHOLD = 1.5
MAX_MESSAGE_LENGTH     = 2000
