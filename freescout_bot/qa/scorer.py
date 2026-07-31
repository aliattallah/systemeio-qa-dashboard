from __future__ import annotations

import json
import logging

from openai import OpenAI

from freescout_bot.qa.models import QAScore, ResolutionVerdict

log = logging.getLogger(__name__)

# Condensed internal SOP — steps only, no routing/tagging info.
# Updated whenever internal procedures change.
_SOP = """\
=== SYSTEME.IO INTERNAL SUPPORT PROCEDURES ===
Use these to judge Accuracy and Completeness. Do NOT penalize an agent for following the
correct procedure, even if it seems counterintuitive from a generic support perspective.

UNIVERSAL RULE — CLARIFYING QUESTIONS:
Asking for account email, URL, card digits, verification codes, or credentials BEFORE
resolving is ALWAYS the correct first step. Never penalize for this.

ESCALATION RULE:
Correctly escalating to a specialist team (Deliverability, Payment/Billing, Developers,
Migration, etc.) = full marks on Accuracy. Agents must NOT try to solve issues that
belong to specialist teams.

── REFUND POLICY ────────────────────────────────────────────────────────────────────
Monthly plan — first payment : refundable within 4 days of purchase
Monthly plan — renewal       : refundable within 4 days of renewal date
Annual plan  — first payment : refundable within 14 days of purchase
Annual plan  — renewal       : refundable within 7 days of renewal date
$7 courses                   : 30-day money-back guarantee
Data recovery service        : refund only if developers have not started work yet

── SUBSCRIPTION CANCELLATION ────────────────────────────────────────────────────────
1. Confirm the customer wants to cancel
2. Explain how to self-cancel step-by-step (do NOT only give a link)
3. Ask casually for the cancellation reason (curiosity, not retention pressure)
4. Inform the customer that the subscription stays active until cancellation is fully processed

── SUBSCRIPTION UPGRADE ─────────────────────────────────────────────────────────────
Stripe/card : customer upgrades in account settings; remaining unused days from the old
              plan become free trial days on the new plan (no immediate extra charge)
PayPal      : full amount charged immediately; offer two options:
              (a) cancel and wait for billing cycle end, then subscribe to new plan
              (b) custom order form granting remaining days as free trial on new plan

── SUBSCRIPTION DOWNGRADE ───────────────────────────────────────────────────────────
Monthly     : advise to cancel → wait for renewal date (account reverts to free) → take new plan
Annual ≤365 days remaining : offer custom order form for remaining period on lower plan
Annual >365 days remaining : consult team leader before offering anything
Note: if the user cancelled on their side already, COF is not possible; they must wait for billing date.

── SUBSCRIPTION FROM WRONG ACCOUNT ──────────────────────────────────────────────────
Monthly, within 4 days  : refund + repurchase from correct account
Monthly, after 4 days   : no refund; offer custom order form for remaining period on correct account
Annual, within 14 days  : refund + repurchase from correct account
Annual, after 14 days   : no refund; offer custom order form for remaining period on correct account

── ACCOUNT EMAIL CHANGE / 2FA DEACTIVATION ──────────────────────────────────────────
Document verification is REQUIRED before proceeding — asking for documents is correct procedure:
  Card users     : last 4 digits, expiry date, last payment date/amount, cardholder name
  PayPal users   : PayPal email, payment ID, last payment date/amount
  Freemium users : utility bill (electricity/water/gas/internet) AND tax notice
Documents must match the account profile; if unclear, request additional proof or clarification.

── DOMAIN AUTHENTICATION / INTEGRATION — correct order of operations ─────────────────
1. Confirm what the customer needs: authentication, integration, or both
2. Two valid approaches — BOTH are acceptable correct procedure:
   (a) Request DNS management credentials or delegation invitation first, then handle DNS changes
   (b) Explain the required DNS changes (CNAMEs, DMARC, etc.) first, then offer to handle
       them if the customer shares credentials or grants access
3. Once credentials/access are received: add/verify all DNS records and confirm propagation
4. Inform the customer when complete
CRITICAL: Do NOT penalize an agent for choosing approach (a) OR approach (b). Both are
standard workflows within the team. An agent who explains DNS steps and offers to handle
them is following correct procedure, as is one who asks for credentials first.

── ACCOUNT DELETION ─────────────────────────────────────────────────────────────────
MAIN ACCOUNT (has subscription, funnels, courses, emails, payment history):
Multi-step verification process. After explaining all consequences (loss of data, funnels,
courses, emails, subscribers, etc.), the agent MUST ask for a NEW explicit confirmation
from the customer. This confirmation request is REQUIRED correct procedure.

STUDENT / EMPTY ACCOUNT (free account with no content — no funnels, no courses, no emails):
No consequences explanation or explicit confirmation step is required. The agent may proceed
to forward the deletion request to the technical team directly. Do NOT apply the main account
deletion procedure to empty student accounts — that would be unnecessary friction.

── DELIVERABILITY — EMAIL GOING TO SPAM ─────────────────────────────────────────────
1. Offer a deliverability audit (sending test emails to analyze inbox placement)
2. Obtain explicit customer authorization before proceeding
3. Move request to Deliverability mailbox and set status to Active

── DELIVERABILITY — BOUNCE LIST REMOVAL ─────────────────────────────────────────────
1. Confirm which contact to remove
2. Verify email validity using an email verifier (e.g. hunter.io)
3. If valid → share with Deliverability team for removal
4. If invalid → inform customer the address is not valid; ask them to double-check

── DELIVERABILITY — DOMAIN AUTH STILL PENDING ───────────────────────────────────────
1. Check all DNS records (CNAMEs and DMARC) using DNS Checker
2. Verify the DMARC record email address is valid
3. If records are correct but still pending → escalate to Deliverability team

── AFFILIATE ATTRIBUTION (adding or changing) ───────────────────────────────────────
1. Check whether the correct affiliate is already attached
2. If within refund period → refund + ask customer to repurchase using the correct affiliate link
3. If outside refund period → customer cancels subscription, waits for account to revert to free
   plan, then resubscribes using the correct affiliate link
4. Once done, customer contacts support again to confirm affiliation

── MIGRATION REQUESTS ────────────────────────────────────────────────────────────────
1. Verify customer has an Unlimited or Annual plan
2. Move request to Migration mailbox; set status to Open

── CERTIFICATE REQUESTS ─────────────────────────────────────────────────────────────
1. Verify the customer completed the course
2. Use the correct certificate template (Canva)
3. Enter the customer's name (ask if not provided)
4. Download as PDF with "Flatten PDF" option selected

── ACCOUNT BLOCKED — SUSPICIOUS ACTIVITY ────────────────────────────────────────────
1. Check the block reason and review page content against Terms of Service
2. If content is acceptable → approve and unblock
3. If more info needed → keep blocked; request clarification from customer

── ACCOUNT BLOCKED — INACTIVITY (180+ days) ─────────────────────────────────────────
Agent can unblock; once unblocked, account is removed from the deletion queue.
Notify Deliverability team after unblocking.

── FEATURE REQUESTS ─────────────────────────────────────────────────────────────────
1. Check roadmap.systeme.io for the requested feature
2. If listed → inform customer and guide them to vote
3. If not listed → guide customer to submit a suggestion on the roadmap
"""

_SCORING_PROMPT = """\
You are a strict but fair QA evaluator for Systeme.io customer support.
Systeme.io is an all-in-one online business platform (funnels, email marketing, courses, affiliate programs).

EVALUATION RULES:
- The agent may reply in ANY language (French, Spanish, Japanese, Arabic, etc.). Evaluate the reply in whatever language it was written — do NOT penalize for non-English.
- Your feedback and all notes MUST always be written in English, regardless of the reply's language.
- Be calibrated: 10/10 is rare and exceptional. 7-8 is solid. 5-6 is average. Below 5 means real problems. Do not let scores drift upward — apply the same bar to every ticket.
- A concise, accurate reply can score as high as a long one if it fully addresses the issue. Length is not a proxy for quality.
- The conversation thread below is provided FOR CONTEXT ONLY — it may contain replies from other support team members. You MUST evaluate ONLY the reply in the "AGENT REPLY TO EVALUATE" section. Do NOT penalize the evaluated agent for gaps left by other agents, and do NOT credit them for work done by others.
- The conversation thread shows the FULL history including customer messages sent AFTER the evaluated agent's reply. Do NOT penalize the agent for not addressing concerns or questions that the customer raised AFTER their reply was sent. Evaluate each reply only against what the customer had asked up to that point.
- If the agent sent multiple replies (labeled [Reply 1 of N], [Reply 2 of N], etc.), evaluate each one in the context of where it appeared in the conversation — they are separate messages sent at different times, not one combined response.
- If the ticket required an escalation (e.g., refund approval, bug report, account deletion) and the agent correctly escalated rather than trying to solve it themselves, treat that as full marks on Accuracy.
- If the customer's message is abusive, off-topic, spam, or nonsensical, evaluate only whether the agent handled it professionally; set completeness and topic_variety to null and explain in the note.
- If a customer message contains only a quoted email header with no actual content (e.g. "Le ... a écrit :", "On ... wrote:", or similar), treat this as MISSING CONTEXT — the customer's actual reply was not captured. Do NOT penalize the agent for anything that could be explained by the invisible customer message. Note the missing context in your feedback.

SCORING CRITERIA (all 0-10):

1. Accuracy — Is the information correct, and does it address the issue the agent was responsible for?
   10 = Fully correct, issue fully resolved or clearly explained
   7  = Mostly correct, minor gaps or a small factual slip that doesn't block resolution
   4  = Partially correct but missing key information or contains a notable error
   0  = Wrong, misleading, or no real attempt to address the issue

2. Clarity — Is the response easy to understand and well-structured?
   10 = Clear, logical structure, easy to follow step-by-step
   7  = Mostly clear, minor ambiguity or slight disorganization
   4  = Hard to follow, confusing phrasing, or poor structure
   0  = Incomprehensible or incoherent

3. Tone & Empathy — Is the tone professional and does it show understanding?
   10 = Warm, professional, acknowledges the customer's situation
   5  = Neutral or slightly cold/robotic, but not rude
   0  = Rude, dismissive, or inappropriately casual

4. Completeness — Are all parts of the question answered within THIS agent's replies?
   10 = Every point this agent was responsible for is fully addressed; proactively covers likely follow-ups
   5  = Main issue addressed, but secondary questions left partially answered
   0  = Core question left unanswered
   NOTE: If the agent asks for missing information (account email, URL, card digits, credentials)
   before resolving — this IS the complete and correct response. Do NOT penalize.

5. Topic Variety — Does this ticket raise MULTIPLE distinct topics/questions, and did the agent address each one?
   10 = Multiple distinct topics; agent correctly identified and addressed each one
   7  = Multiple topics; agent addressed the main one well but gave a lighter answer to the rest
   4  = Multiple topics; agent only addressed one, ignoring the others
   N/A = Ticket only raises a single topic — set topic_variety to null

TOPIC CLASSIFICATION — tag the single most relevant primary topic:
- Account       : account settings, email change, 2FA, password reset, account deletion, account blocking/unblocking
- Domain        : domain authentication, domain integration (connecting custom domain to website/funnel), DNS records (CNAME, DMARC, SPF for domain setup), SSL certificates, subdomain setup, connecting integrated domain to a funnel/website, setting homepage after domain integration
- Technical/Bug : platform bugs, errors, feature malfunctions, 403 errors, page loading issues
- Billing       : subscription payments, invoices, VAT, card updates, upgrades, downgrades, cancellations, custom order forms
- Refund/Cancellation : refund requests
- Email Marketing : email campaigns, broadcasts, automations, email deliverability, spam issues, bounce lists, DMARC/DKIM/SPF for email sending performance (NOT domain setup)
- Funnels       : funnel builder, landing pages, funnel steps, order forms, page design (NOT domain setup)
- Courses       : online courses, students, memberships, certificates
- Affiliates    : affiliate program, commissions, affiliate links, attribution
- Onboarding    : getting started, initial platform questions, migration
- Other         : anything that does not clearly fit the above

IMPORTANT TOPIC DISTINCTIONS:
- Domain authentication/integration, DNS setup, SSL, subdomain, connecting domain to funnel → Domain (NOT Account, NOT Email Marketing)
- Email deliverability, DMARC/SPF for email sending performance, bounce lists, spam → Email Marketing
- Funnel builder, page design → Funnels (not Domain)

IMPORTANT: total_score must be the simple average of all NON-NULL criteria scores only.
If topic_variety is null, average only accuracy + clarity + tone + completeness.
If topic_variety has a value, average all five.

---
""" + _SOP + """
---
Return ONLY a valid JSON object — no other text:
{{
  "topic": "<category tag>",
  "accuracy": <integer 0-10>,
  "accuracy_note": "<one sentence justification>",
  "clarity": <integer 0-10>,
  "clarity_note": "<one sentence justification>",
  "tone": <integer 0-10>,
  "tone_note": "<one sentence justification>",
  "completeness": <integer 0-10 or null>,
  "completeness_note": "<one sentence justification or 'N/A'>",
  "topic_variety": <integer 0-10 or null>,
  "topic_variety_note": "<one sentence justification or 'N/A — single topic'>",
  "total_score": <float, average of applicable criteria>,
  "feedback": "<2-3 sentences: what worked, what to improve, any edge-case notes>"
}}

---
FULL CONVERSATION THREAD (background context only — may include other support agents' replies):
{conversation_thread}

---
AGENT REPLY TO EVALUATE (score ONLY this):
{agent_reply}"""

_RESOLUTION_PROMPT = """\
You are reviewing a customer support conversation to determine whether the customer's issue was ultimately resolved.

Read the full conversation and return ONLY a valid JSON object:
{{
  "verdict": "<Resolved|Partially Resolved|Unresolved|Escalated|Unclear>",
  "reason": "<one sentence explanation>"
}}

Verdict definitions:
- Resolved: the customer's issue was fully addressed and the conversation ended cleanly
- Partially Resolved: the main issue was addressed but the customer had outstanding concerns
- Unresolved: the customer's issue was not addressed or they expressed ongoing frustration
- Escalated: the ticket was correctly handed off to another team or department
- Unclear: not enough information in the conversation to determine the outcome

---
Conversation:
{full_thread}"""


class QAScorer:
    """Scores support conversations and validates customer ratings using OpenAI."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model  = model

    def score(self, conversation_thread: str, agent_reply: str) -> QAScore:
        data = self._call(_SCORING_PROMPT.format(
            conversation_thread=conversation_thread,
            agent_reply=agent_reply,
        ))

        accuracy     = int(data.get("accuracy",     0))
        clarity      = int(data.get("clarity",      0))
        tone         = int(data.get("tone",         0))
        completeness = data.get("completeness")
        completeness = int(completeness) if completeness is not None else None
        topic_variety = data.get("topic_variety")
        topic_variety = int(topic_variety) if topic_variety is not None else None

        # Compute total_score ourselves — average of applicable criteria
        scores = [accuracy, clarity, tone]
        if completeness is not None:
            scores.append(completeness)
        if topic_variety is not None:
            scores.append(topic_variety)
        total_score = round(sum(scores) / len(scores), 2)

        _VALID_TOPICS = {
            "Billing", "Technical/Bug", "Onboarding", "Funnels",
            "Email Marketing", "Courses", "Affiliates",
            "Refund/Cancellation", "Account", "Domain", "Other",
        }
        raw_topic = str(data.get("topic", "Other"))
        topic = raw_topic if raw_topic in _VALID_TOPICS else "Other"

        return QAScore(
            accuracy=           accuracy,
            clarity=            clarity,
            tone=               tone,
            completeness=       completeness,
            topic_variety=      topic_variety,
            topic=              topic,
            accuracy_note=      str(data.get("accuracy_note",      "")),
            clarity_note=       str(data.get("clarity_note",       "")),
            tone_note=          str(data.get("tone_note",          "")),
            completeness_note=  str(data.get("completeness_note",  "")),
            topic_variety_note= str(data.get("topic_variety_note", "N/A — single topic")),
            total_score=        total_score,
            feedback=           str(data.get("feedback", "")),
        )

    def assess_resolution(self, full_thread: str) -> ResolutionVerdict:
        data = self._call(_RESOLUTION_PROMPT.format(full_thread=full_thread))
        return ResolutionVerdict(
            verdict=str(data.get("verdict", "Unclear")),
            reason= str(data.get("reason",  "")),
        )

    def _call(self, prompt: str) -> dict:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return json.loads(resp.choices[0].message.content)
