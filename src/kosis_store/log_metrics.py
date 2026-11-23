"""Metric definitions over the user log (plans/T3-36.md, T3-49, docs/log-schema.md 6·14): the one place they are written.

Every metric reads two common tables, so the filter is written once:
- `turns`: request_logs in the KST date range of the filter, for all users / members / guests, harness and team
  traffic left out unless asked for.
- `last_feedback`: the last feedback event of each answer (POL-007: a second press replaces the first), ordered by
  (client_ts, received_at, event_id) over all time, joined to `turns`. The date, member flag and internal flag are the
  question's, so "all" = members + guests.

PostgreSQL only (DISTINCT ON, percentile_cont). Dates are KST; the range is turned into UTC times [start, end) so the
created_at index is used. Numbers are computed here; screens and BI tools only read the results.
Per-step latency and tokens per model are Prometheus queries (docs/log-schema.md 10), not SQL here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

KST = ZoneInfo("Asia/Seoul")
MAX_DAYS = 90  # FEAT-043: the screen looks back 90 days at most
TOP_TABLES = 20
User = Literal["all", "member", "guest"]

_TURNS = """turns AS (
    SELECT * FROM request_logs
    WHERE created_at >= :start AND created_at < :end
      AND (:include_internal OR NOT is_internal)
      AND (:user = 'all' OR is_member = (:user = 'member'))
)"""
_LAST_FEEDBACK = """fb AS (
    SELECT DISTINCT ON (request_id) request_id, props->>'value' AS value, props->>'choice' AS choice, reason
    FROM ui_events WHERE event_type = 'feedback' AND request_id IS NOT NULL
    ORDER BY request_id, client_ts DESC, received_at DESC, event_id DESC
), last_feedback AS (
    SELECT t.*, fb.value, fb.choice, fb.reason AS feedback_reason FROM fb JOIN turns t USING (request_id)
)"""
_DAY = "(created_at AT TIME ZONE 'Asia/Seoul')::date"
# Response times only for turns that ran to the end: an aborted turn stops when the user leaves (too short) and an
# error turn stops at the exception, so both would pull the percentiles down.
_FINISHED = "outcome NOT IN ('aborted', 'error')"


@dataclass(frozen=True)
class MetricFilter:
    """The common filter of the metric screen (FEAT-043): KST dates [start_day, end_day], users, internal traffic.

    include_internal: team and eval harness traffic (is_internal) together. Off by default; on, harness numbers mix
    into top tables and lists (their question text is never stored).
    """

    start_day: date
    end_day: date
    user: User = "all"
    include_internal: bool = False

    def check(self, today: date | None = None) -> MetricFilter:
        """Raise ValueError unless the range is a past range inside the last MAX_DAYS days (the screen's rule: the
        last complete day is yesterday). Returns self. Side effects: none."""
        today = today or datetime.now(KST).date()
        if self.user not in ("all", "member", "guest"):
            raise ValueError(f"user must be all, member or guest, not {self.user!r}")
        if self.start_day > self.end_day:
            raise ValueError("start_day is after end_day")
        if self.end_day >= today:
            raise ValueError("end_day must be yesterday or earlier (today is not complete)")
        if self.start_day < today - timedelta(days=MAX_DAYS):
            raise ValueError(f"start_day must be within the last {MAX_DAYS} days")
        return self

    def params(self) -> dict:
        start = datetime.combine(self.start_day, time(0), tzinfo=KST)
        end = datetime.combine(self.end_day, time(0), tzinfo=KST) + timedelta(days=1)
        return {"start": start, "end": end, "user": self.user, "include_internal": self.include_internal}


# name -> (SQL over the common tables, may it be split by day). With by_day the first column is `day` (KST date).
# {d} becomes "<day> AS day, " and {g} "GROUP BY day ORDER BY day" (both empty otherwise): one body, both shapes.
METRICS: dict[str, tuple[str, bool]] = {
    # -- summary tab (FEAT-044)
    "question_count": ("SELECT {d}count(*) AS n FROM turns {g}", True),
    # answered / refused / clarify / error (failed counted as error); aborted is not in the denominator, so the four
    # shares add up to 1. Leaving early is the abort rate of the usage tab.
    "outcome_ratio": ("""
        SELECT CASE WHEN outcome = 'failed' THEN 'error' ELSE outcome END AS outcome, count(*) AS n,
               round(count(*)::numeric / sum(count(*)) OVER (), 4) AS share
        FROM turns WHERE outcome <> 'aborted' GROUP BY 1 ORDER BY 1""", False),
    "outcome_share": ("""
        SELECT outcome, count(*) AS n, round(count(*)::numeric / sum(count(*)) OVER (), 4) AS share
        FROM turns GROUP BY outcome ORDER BY outcome""", False),  # all six stored outcomes (diagnosis)
    "thumbs_up_ratio": ("""
        SELECT {d}count(*) FILTER (WHERE value = 'up') AS up, count(*) FILTER (WHERE value = 'down') AS down,
               round(count(*) FILTER (WHERE value = 'up')::numeric / nullif(count(*), 0), 4) AS ratio
        FROM last_feedback {g}""", True),
    "latency": (f"""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
        FROM turns WHERE {_FINISHED} AND latency_ms IS NOT NULL""", False),
    # -- answer quality tab (FEAT-045); the list of thumbs-down answers is down_answers()
    "refusal_reasons": ("""
        SELECT reason_code, count(*) AS n FROM turns WHERE outcome = 'refused'
        GROUP BY reason_code ORDER BY n DESC, reason_code""", False),
    "down_reasons": ("""
        SELECT choice, count(*) AS n FROM last_feedback WHERE value = 'down' GROUP BY choice ORDER BY n DESC, choice""",
                     False),
    "up_reasons": ("""
        SELECT choice, count(*) AS n FROM last_feedback WHERE value = 'up' GROUP BY choice ORDER BY n DESC, choice""",
                   False),
    "failed_stages": ("""
        SELECT CASE WHEN failed_stage IN ('search', 'stat', 'chart') THEN failed_stage ELSE 'other' END AS failed_stage,
               count(*) AS n FROM turns WHERE failed_stage IS NOT NULL GROUP BY 1 ORDER BY n DESC, 1""", False),
    # -- usage tab (FEAT-046)
    "top_tables": (f"""
        SELECT picked_key, max(survey_name) AS survey_name, count(*) AS n FROM turns
        WHERE outcome = 'answered' AND picked_key IS NOT NULL
        GROUP BY picked_key ORDER BY n DESC, picked_key LIMIT {TOP_TABLES}""", False),
    "turns_per_conversation": ("""
        SELECT CASE WHEN turns >= 4 THEN '4+' ELSE turns::text END AS turns, count(*) AS n
        FROM (SELECT conversation_id, count(DISTINCT turn_index) AS turns FROM turns GROUP BY conversation_id) c
        GROUP BY 1 ORDER BY 1""", False),
    # answers with numbers whose source was clicked / answers with numbers
    "citation_click_rate": ("""
        SELECT count(DISTINCT t.request_id) FILTER (WHERE c.request_id IS NOT NULL) AS clicked,
               count(DISTINCT t.request_id) AS with_numbers,
               round(count(DISTINCT t.request_id) FILTER (WHERE c.request_id IS NOT NULL)::numeric
                     / nullif(count(DISTINCT t.request_id), 0), 4) AS rate
        FROM turns t LEFT JOIN ui_events c ON c.request_id = t.request_id AND c.event_type = 'citation_click'
        WHERE t.has_numbers""", False),
    "abort_rate": ("""
        SELECT {d}count(*) FILTER (WHERE outcome = 'aborted') AS aborted, count(*) AS questions,
               round(count(*) FILTER (WHERE outcome = 'aborted')::numeric / nullif(count(*), 0), 4) AS rate
        FROM turns {g}""", True),
    # -- performance and cost tab (FEAT-047; steps and tokens per model are Prometheus)
    "llm_usage": ("""
        SELECT round(avg(llm_calls), 2) AS calls, round(avg(input_tokens), 2) AS input_tokens,
               round(avg(output_tokens), 2) AS output_tokens
        FROM turns""", False),
    "first_token": (f"""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY first_token_ms) AS p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY first_token_ms) AS p95
        FROM turns WHERE {_FINISHED} AND first_token_ms IS NOT NULL""", False),  # not on a tab (close to latency today)
}
TABS = {
    "summary": ("question_count", "outcome_ratio", "thumbs_up_ratio", "latency"),
    "quality": ("refusal_reasons", "down_reasons", "up_reasons", "failed_stages"),  # + down_answers()
    "usage": ("top_tables", "turns_per_conversation", "citation_click_rate", "abort_rate"),
    "cost": ("llm_usage",),
}

_DOWN_ANSWERS = """
    SELECT created_at, question, picked_key, survey_name, outcome, choice, feedback_reason AS reason, request_id,
           count(*) OVER () AS total
    FROM last_feedback WHERE value = 'down'
    ORDER BY created_at DESC, request_id LIMIT :limit OFFSET :offset"""


def _sql(body: str) -> str:
    return f"WITH {_TURNS}, {_LAST_FEEDBACK} {body}"


def query(session: Session, name: str, flt: MetricFilter, *, by_day: bool = False) -> list[dict]:
    """Rows of one metric under the filter; with by_day (question_count, thumbs_up_ratio, abort_rate) one row per KST
    day, first column `day`. Side effects: reads the database."""
    body, daily = METRICS[name]
    if by_day and not daily:
        raise ValueError(f"{name} has no daily series")
    body = body.replace("{d}", f"{_DAY} AS day, " if by_day else "")
    body = body.replace("{g}", "GROUP BY day ORDER BY day" if by_day else "")
    return [dict(row) for row in session.execute(text(_sql(body)), flt.params()).mappings()]


def down_answers(session: Session, flt: MetricFilter, *, page: int = 1, size: int = 20) -> dict:
    """The thumbs-down answers, newest first: date, masked question (empty after the retention period), table, survey,
    outcome, choice code, masked reason, request_id (Jaeger). Returns {"total", "rows"}. Side effects: reads."""
    if page < 1 or not 1 <= size <= 100:
        raise ValueError("page starts at 1 and size is 1..100")
    rows = [dict(r) for r in session.execute(text(_sql(_DOWN_ANSWERS)),
                                              {**flt.params(), "limit": size, "offset": (page - 1) * size}).mappings()]
    total = rows[0]["total"] if rows else 0
    for r in rows:
        del r["total"]
    return {"total": total, "rows": rows}


def tab(session: Session, name: str, flt: MetricFilter) -> dict[str, list[dict]]:
    """All metrics of a screen tab (summary, quality, usage, cost) under one filter. Side effects: reads."""
    return {m: query(session, m, flt) for m in TABS[name]}


def day_range(day: date) -> dict[str, datetime]:
    """The timezone-aware bounds [start, end) of a KST date. Side effects: none."""
    start = datetime.combine(day, time(0), tzinfo=KST)
    return {"start": start, "end": start + timedelta(days=1)}


def metric(session: Session, name: str, day: date) -> list[dict]:
    """One metric for one KST date, all users, internal traffic left out (T3-36 shape). Side effects: reads."""
    return query(session, name, MetricFilter(day, day))


# Retention: the text goes, the row stays. Returns the rows it changed.
PURGE_QUESTIONS = "UPDATE request_logs SET question = NULL WHERE question IS NOT NULL AND created_at < :before"
# T3-48: the feedback reason of a screen event is the other free text column
PURGE_FEEDBACK_REASONS = "UPDATE ui_events SET reason = NULL WHERE reason IS NOT NULL AND received_at < :before"


def purge_questions(session: Session, before: datetime) -> int:
    """Set the question text of rows older than `before` to NULL; returns how many. Side effects: updates rows."""
    return session.execute(text(PURGE_QUESTIONS), {"before": before}).rowcount


def purge_feedback_reasons(session: Session, before: datetime) -> int:
    """Set the feedback reason of screen events received before `before` to NULL; returns how many (T3-48).
    Side effects: updates rows."""
    return session.execute(text(PURGE_FEEDBACK_REASONS), {"before": before}).rowcount
