from __future__ import annotations

from collections import defaultdict

from freescout_bot.qa.config import SCORE_MAXIMUMS, TARGET_MIN_SCORE, WARNING_DROP_THRESHOLD
from freescout_bot.qa.models import AgentSummary, PerformanceWarning, ScoredTicket


def compute_agent_summaries(tickets: list[ScoredTicket]) -> list[AgentSummary]:
    by_agent: dict[str, list[ScoredTicket]] = defaultdict(list)
    for ticket in tickets:
        by_agent[ticket.agent_name].append(ticket)

    summaries = [_summarise(agent, rows) for agent, rows in sorted(by_agent.items())]
    return sorted(summaries, key=lambda s: s.avg_score, reverse=True)


def detect_team_patterns(tickets: list[ScoredTicket]) -> dict[str, float]:
    """Returns percentage of tickets where each dimension was the weakest area."""
    if not tickets:
        return {}

    counts: dict[str, int] = {"Accuracy": 0, "Clarity": 0, "Tone": 0, "Completeness": 0}
    for ticket in tickets:
        weakest = _weakest_area(
            ticket.score.accuracy,
            ticket.score.clarity,
            ticket.score.tone,
            ticket.score.completeness if ticket.score.completeness is not None else 10,
        )
        counts[weakest] += 1

    total = len(tickets)
    return {dim: round(count / total * 100, 1) for dim, count in counts.items()}


def compute_topic_distribution(tickets: list[ScoredTicket]) -> dict[str, int]:
    """Returns count of tickets per topic category."""
    counts: dict[str, int] = {}
    for ticket in tickets:
        topic = ticket.score.topic or "Other"
        counts[topic] = counts.get(topic, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def detect_warnings(
    summaries: list[AgentSummary],
    historical: dict[str, list[float]],
    run_date: str,
) -> list[PerformanceWarning]:
    warnings: list[PerformanceWarning] = []

    for summary in summaries:
        history = historical.get(summary.agent_name)
        if not history:
            continue

        hist_avg = round(sum(history) / len(history), 2)
        drop     = round(hist_avg - summary.avg_score, 2)

        if drop >= WARNING_DROP_THRESHOLD:
            warnings.append(PerformanceWarning(
                run_date=       run_date,
                agent_name=     summary.agent_name,
                historical_avg= hist_avg,
                current_avg=    summary.avg_score,
                drop=           drop,
                note=           "Performance decline detected — requires review",
            ))

    return warnings


# ── Private helpers ───────────────────────────────────────────────────────────

def _summarise(agent_name: str, tickets: list[ScoredTicket]) -> AgentSummary:
    n = len(tickets)

    def avg(values: list[int | float]) -> float:
        return round(sum(values) / n, 2)

    avg_accuracy     = avg([t.score.accuracy    for t in tickets])
    avg_clarity      = avg([t.score.clarity     for t in tickets])
    avg_tone         = avg([t.score.tone        for t in tickets])
    completeness_vals = [t.score.completeness for t in tickets if t.score.completeness is not None]
    avg_completeness = round(sum(completeness_vals) / len(completeness_vals), 2) if completeness_vals else 0.0
    avg_score        = avg([t.score.total_score for t in tickets])

    # Use sentinel 10 when no completeness data so it doesn't falsely appear as weakest area
    weakest_completeness = avg_completeness if completeness_vals else 10.0

    return AgentSummary(
        agent_name=       agent_name,
        ticket_count=     n,
        avg_score=        avg_score,
        avg_accuracy=     avg_accuracy,
        avg_clarity=      avg_clarity,
        avg_tone=         avg_tone,
        avg_completeness= avg_completeness,
        weakest_area=     _weakest_area(avg_accuracy, avg_clarity, avg_tone, weakest_completeness),
        status=           "Below target" if avg_score < TARGET_MIN_SCORE else "OK",
    )


def _weakest_area(
    accuracy: float,
    clarity: float,
    tone: float,
    completeness: float,
) -> str:
    # All criteria are now 0-10 so ratios are equal — just compare raw values
    areas = {
        "Accuracy":     accuracy,
        "Clarity":      clarity,
        "Tone":         tone,
        "Completeness": completeness,
    }
    return min(areas, key=areas.get)
