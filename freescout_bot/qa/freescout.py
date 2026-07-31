from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import requests

from freescout_bot.qa.config import BOT_USER_ID, MAILBOX_NAMES, MAX_CONVS_PER_MAILBOX, MAX_MESSAGE_LENGTH

log = logging.getLogger(__name__)


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._parts)).strip()


class FreeScoutQAClient:
    """Fetches closed conversations and extracts customer/agent message pairs for QA scoring."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base    = base_url.rstrip("/") + "/api"
        self._api_key = api_key
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept":       "application/json",
        })

    # ── Public API ────────────────────────────────────────────────────────────

    def get_agent_names(self) -> dict[int, str]:
        all_users: list[dict] = []
        page = 1

        while True:
            try:
                resp = self._session.get(
                    f"{self._base}/users",
                    params=self._auth({"pageSize": 50, "page": page}),
                    timeout=30,
                )
                resp.raise_for_status()
                data  = resp.json()
                users = data.get("_embedded", {}).get("users", [])
                all_users.extend(users)

                total_pages = data.get("page", {}).get("totalPages", 1)
                if page >= total_pages or not users:
                    break
                page += 1
            except Exception as e:
                log.warning("Could not fetch agent names page %d: %s", page, e)
                break

        log.info("Loaded %d agent(s)", len(all_users))
        return {
            u["id"]: (
                f"{u.get('firstName', '')} {u.get('lastName', '')}".strip()
                or f"Agent {u['id']}"
            )
            for u in all_users
        }

    def resolve_agent_name(self, user_id: int) -> str:
        """Fallback: resolve a single agent name by ID."""
        try:
            resp = self._session.get(
                f"{self._base}/users/{user_id}",
                params=self._auth(),
                timeout=30,
            )
            resp.raise_for_status()
            u = resp.json()
            return (
                f"{u.get('firstName', '')} {u.get('lastName', '')}".strip()
                or f"Agent {user_id}"
            )
        except Exception as e:
            log.warning("Could not resolve agent %d: %s", user_id, e)
            return f"Agent {user_id}"

    def fetch_closed_conversations(
        self,
        mailbox_ids: list[int],
        days_back: int,
        since_date: str | None = None,
    ) -> list[dict]:
        if since_date:
            cutoff = datetime.fromisoformat(since_date).replace(tzinfo=timezone.utc)
            log.info("Fetching conversations since %s", since_date)
        else:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        all_convs: list[dict] = []

        for mailbox_id in mailbox_ids:
            convs = self._fetch_mailbox(mailbox_id, cutoff)
            log.info(
                "Mailbox %s: %d conversation(s)",
                MAILBOX_NAMES.get(mailbox_id, mailbox_id),
                len(convs),
            )
            all_convs.extend(convs)

        log.info("Total conversations fetched: %d", len(all_convs))
        return all_convs

    def get_threads(self, conv_id: int) -> list[dict]:
        try:
            resp = self._session.get(
                f"{self._base}/conversations/{conv_id}",
                params=self._auth(),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("_embedded", {}).get("threads", [])
        except Exception as e:
            log.error("Failed fetching threads for conv %d: %s", conv_id, e)
            return []

    def extract_per_agent_exchanges(
        self,
        threads: list[dict],
    ) -> tuple[str, dict[int, str], str]:
        """
        Returns (customer_context, agent_replies, full_thread).
        - customer_context: all customer messages joined (given to AI as context for every agent)
        - agent_replies: {agent_uid: that agent's replies joined} — one entry per unique agent
        - full_thread: formatted conversation for resolution assessment
        """
        customer_parts:    list[str]       = []
        agent_reply_parts: dict[int, list[str]] = {}
        thread_lines:      list[str]       = []

        # FreeScout API returns threads newest-first; sort ascending so messages
        # are processed in chronological order for correct context and reply ordering.
        threads = sorted(threads, key=lambda t: t.get("createdAt", ""))

        for thread in threads:
            t_type = thread.get("type")
            state  = thread.get("state")
            body   = self._clean_body(thread.get("body") or thread.get("text") or "")

            if t_type == "customer" and state == "published" and body:
                customer_parts.append(body)
                thread_lines.append(f"Customer: {body[:400]}")

            elif t_type == "message" and state == "published":
                uid = (thread.get("createdBy") or {}).get("id")
                if uid and uid != BOT_USER_ID and body:
                    agent_reply_parts.setdefault(uid, []).append(body)
                    thread_lines.append(f"Support Agent: {body[:400]}")

        customer_context = self._truncate("\n\n".join(customer_parts))

        # Label each message separately so the AI understands they are distinct
        # replies sent at different points in the conversation — not one combined message.
        agent_replies = {}
        for uid, parts in agent_reply_parts.items():
            if len(parts) == 1:
                agent_replies[uid] = self._truncate(parts[0])
            else:
                labeled = "\n\n".join(
                    f"[Reply {i + 1} of {len(parts)}]\n{part}"
                    for i, part in enumerate(parts)
                )
                agent_replies[uid] = self._truncate(labeled)

        full_thread = "\n\n".join(thread_lines)
        return customer_context, agent_replies, full_thread

    # ── Private helpers ───────────────────────────────────────────────────────

    def _auth(self, extra: dict | None = None) -> dict:
        params: dict = {"api_key": self._api_key}
        if extra:
            params.update(extra)
        return params

    def _fetch_mailbox(self, mailbox_id: int, cutoff: datetime) -> list[dict]:
        convs: list[dict] = []
        page = 1

        while True:
            try:
                resp = self._session.get(
                    f"{self._base}/conversations",
                    params=self._auth({
                        "status":    "closed",
                        "mailboxId": mailbox_id,
                        "pageSize":  50,
                        "page":      page,
                        "sortField": "createdAt",
                        "sortOrder": "desc",
                    }),
                    timeout=30,
                )
                resp.raise_for_status()
            except Exception as e:
                log.error("Mailbox %d page %d failed: %s", mailbox_id, page, e)
                break

            batch = resp.json().get("_embedded", {}).get("conversations", [])
            if not batch:
                break

            hit_cutoff = False
            for conv in batch:
                if self._is_before_cutoff(conv, cutoff):
                    hit_cutoff = True
                    break
                conv["_mailbox_id"] = mailbox_id
                convs.append(conv)

            if hit_cutoff or len(batch) < 50 or len(convs) >= MAX_CONVS_PER_MAILBOX:
                break
            page += 1

        return convs

    @staticmethod
    def _is_before_cutoff(conv: dict, cutoff: datetime) -> bool:
        ts = conv.get("createdAt") or conv.get("updatedAt") or ""
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")) < cutoff
        except ValueError:
            return False

    @staticmethod
    def _clean_body(html: str) -> str:
        stripper = _HTMLStripper()
        stripper.feed(html)
        return stripper.get_text()

    @staticmethod
    def _truncate(text: str) -> str:
        return text[:MAX_MESSAGE_LENGTH] + "…" if len(text) > MAX_MESSAGE_LENGTH else text
