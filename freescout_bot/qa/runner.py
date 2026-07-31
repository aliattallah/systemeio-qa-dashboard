from __future__ import annotations

import logging
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from freescout_bot.qa.aggregator import compute_agent_summaries, detect_warnings
from freescout_bot.qa.config import ALL_MAILBOX_IDS, EXCLUDED_AGENT_NAMES, MAILBOX_NAMES
from freescout_bot.qa.freescout import FreeScoutQAClient
from freescout_bot.qa.models import AgentSummary, RunConfig, RunResult, ScoredTicket
from freescout_bot.qa.scorer import QAScorer
from freescout_bot.qa.storage import SQLiteStorage

log = logging.getLogger(__name__)

_MAX_WORKERS = 20


class QARunner:
    """Orchestrates the full QA evaluation pipeline end-to-end."""

    def __init__(
        self,
        freescout: FreeScoutQAClient,
        scorer:    QAScorer,
        storage:   SQLiteStorage | None,
    ) -> None:
        self._freescout = freescout
        self._scorer    = scorer
        self._storage   = storage

    def run(self, config: RunConfig) -> RunResult:
        run_date    = datetime.now().strftime("%Y-%m-%d")
        mailbox_ids = config.mailbox_ids or ALL_MAILBOX_IDS

        log.info("=== QA Evaluation | %s | Last %d days ===", run_date, config.days_back)

        if self._storage and not config.dry_run:
            self._storage.connect()

        evaluated_ids: set[tuple[int, int]] = (
            self._storage.get_evaluated_ids()
            if self._storage and not config.dry_run
            else set()
        )
        log.info("Already evaluated: %d (conv, agent) pair(s)", len(evaluated_ids))

        agent_names = self._freescout.get_agent_names()

        conversations = self._freescout.fetch_closed_conversations(
            mailbox_ids, config.days_back,
        )
        # Shuffle so all mailboxes are interleaved — prevents English from
        # filling agent caps before Italian/Arabic/Japanese tickets are reached
        random.shuffle(conversations)
        log.info("Conversations after shuffle: %d", len(conversations))

        # Shared mutable state — all access must hold _lock
        _lock         = threading.Lock()
        # Start fresh each run — evaluated_ids already prevents re-scoring any ticket,
        # so the cap here only controls how many NEW tickets are added this run.
        _agent_counts: dict[str, int] = {}
        _name_cache:   dict[int, str] = {}

        def _process_one(conv: dict) -> list[ScoredTicket]:
            threads = self._freescout.get_threads(conv["id"])
            customer_ctx, agent_replies, full_thread = (
                self._freescout.extract_per_agent_exchanges(threads)
            )

            if not agent_replies or not customer_ctx:
                return []

            # Drop agents already evaluated for this conversation
            agent_replies = {
                uid: reply for uid, reply in agent_replies.items()
                if (conv["id"], uid) not in evaluated_ids
            }
            if not agent_replies:
                return []

            # Assess resolution once per conversation — shared across all agents in it
            resolution = None
            if full_thread:
                try:
                    resolution = self._scorer.assess_resolution(full_thread)
                except Exception as e:
                    log.warning("  → Resolution assessment failed conv %d: %s", conv["id"], e)

            mailbox_id = conv.get("_mailbox_id") or conv.get("mailboxId", 0)
            results: list[ScoredTicket] = []

            for agent_uid, agent_reply in agent_replies.items():
                with _lock:
                    agent_name = agent_names.get(agent_uid)
                    if not agent_name:
                        if agent_uid not in _name_cache:
                            _name_cache[agent_uid] = self._freescout.resolve_agent_name(agent_uid)
                        agent_name = _name_cache[agent_uid]

                    if agent_name in EXCLUDED_AGENT_NAMES:
                        continue

                    if config.max_per_agent > 0:
                        count = _agent_counts.get(agent_name, 0)
                        if count >= config.max_per_agent:
                            continue
                        _agent_counts[agent_name] = count + 1

                try:
                    score = self._scorer.score(full_thread, agent_reply)
                except Exception as e:
                    log.error("  → Scoring failed conv %d agent %d: %s", conv["id"], agent_uid, e)
                    continue

                log.info("  → %s/10 — %s (conv %d)", score.total_score, agent_name, conv["id"])

                results.append(ScoredTicket(
                    conv_id=            conv["id"],
                    ticket_number=      str(conv.get("number", "")),
                    mailbox_id=         mailbox_id,
                    mailbox_name=       MAILBOX_NAMES.get(mailbox_id, f"Mailbox {mailbox_id}"),
                    agent_id=           agent_uid,
                    agent_name=         agent_name,
                    customer_message=   customer_ctx,
                    agent_reply=        agent_reply,
                    score=              score,
                    evaluated_at=       datetime.now(),
                    conv_date=          (conv.get("createdAt") or "")[:10],
                    resolution_verdict= resolution,
                ))

            return results

        scored:  list[ScoredTicket] = []
        skipped: int = 0

        log.info("Scoring with %d parallel workers…", _MAX_WORKERS)
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            futures = {executor.submit(_process_one, conv): conv for conv in conversations}
            for future in as_completed(futures):
                try:
                    tickets = future.result()
                except Exception as e:
                    log.error("Unexpected error: %s", e)
                    skipped += 1
                    continue

                if not tickets:
                    skipped += 1
                else:
                    for ticket in tickets:
                        scored.append(ticket)
                        # Save immediately so progress survives a crash or kill
                        if self._storage and not config.dry_run:
                            self._storage.save_tickets([ticket], run_date)

        log.info("Scored: %d | Skipped: %d", len(scored), skipped)

        if not scored:
            log.info("Nothing new to write.")
            return RunResult(scored=0, skipped=skipped, warnings=0)

        if config.dry_run:
            log.info("Dry run — %d ticket(s) ready, not saved.", len(scored))
            self._print_summary(compute_agent_summaries(scored))
            return RunResult(scored=len(scored), skipped=skipped, warnings=0)

        return self._persist(scored, run_date, skipped)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _persist(
        self,
        scored: list[ScoredTicket],
        run_date: str,
        skipped: int,
    ) -> RunResult:
        # Tickets already saved incrementally during scoring
        self._storage.set_last_run_date(run_date)

        summaries  = compute_agent_summaries(scored)
        historical = self._storage.get_historical_scores(exclude_run_date=run_date)
        warnings   = detect_warnings(summaries, historical, run_date)

        for w in warnings:
            log.warning(
                "ALERT: %s dropped to %.1f (historical avg %.1f)",
                w.agent_name, w.current_avg, w.historical_avg,
            )

        self._print_summary(summaries)

        return RunResult(
            scored=   len(scored),
            skipped=  skipped,
            warnings= len(warnings),
        )

    @staticmethod
    def _print_summary(summaries: list[AgentSummary]) -> None:
        print("\n── Team Summary " + "─" * 44)
        print(f"{'Agent':<25} {'Tickets':>7} {'Avg':>5}  {'Weakest':<15} Status")
        print("─" * 60)
        for s in summaries:
            flag = "⚠" if s.status == "Below target" else "✓"
            print(
                f"{s.agent_name:<25} {s.ticket_count:>7} {s.avg_score:>5}  "
                f"{s.weakest_area:<15} {flag} {s.status}"
            )
        print()
