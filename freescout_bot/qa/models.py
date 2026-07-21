from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class QAScore:
    accuracy:     int
    clarity:      int
    tone:         int
    completeness: int
    total_score:  int
    feedback:     str


@dataclass(frozen=True)
class RatingValidation:
    verdict: str  # "Valid" | "Invalid" | "Unclear"
    reason:  str


@dataclass
class ScoredTicket:
    conv_id:           int
    ticket_number:     str
    mailbox_id:        int
    mailbox_name:      str
    agent_id:          int
    agent_name:        str
    customer_message:  str
    agent_reply:       str
    score:             QAScore
    evaluated_at:      datetime
    customer_rating:   int | None              = None
    rating_validation: RatingValidation | None = None


@dataclass
class AgentSummary:
    agent_name:       str
    ticket_count:     int
    avg_score:        float
    avg_accuracy:     float
    avg_clarity:      float
    avg_tone:         float
    avg_completeness: float
    weakest_area:     str
    status:           str  # "OK" | "Below target"


@dataclass(frozen=True)
class PerformanceWarning:
    run_date:       str
    agent_name:     str
    historical_avg: float
    current_avg:    float
    drop:           float
    note:           str


@dataclass
class RunConfig:
    days_back:     int       = 30
    mailbox_ids:   list[int] = field(default_factory=list)
    dry_run:       bool      = False
    max_per_agent: int       = 0  # 0 = no limit; set to 10 for bi-weekly sampling


@dataclass
class RunResult:
    scored:    int
    skipped:   int
    warnings:  int
    sheet_url: str = ""
