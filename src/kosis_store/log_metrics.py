"""Metric definitions over request_logs (plans/T3-36.md, docs/log-schema.md): the one place they are written.

PostgreSQL only (percentile_cont, jsonb_array_elements). Every metric takes :day, a date in KST: rows are stored in
UTC and turned into a KST date here. Team and harness traffic (is_internal) is left out. The metrics that need the
feedback table (KOS-158) or the screen events table (KOS-170) are in docs/log-schema.md until those tables exist.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

_DAY = "NOT is_internal AND (created_at AT TIME ZONE 'Asia/Seoul')::date = :day"

METRICS: dict[str, str] = {
    "question_count": f"SELECT count(*) AS n FROM request_logs WHERE {_DAY}",
    "outcome_share": f"""
        SELECT outcome, count(*) AS n, round(count(*)::numeric / sum(count(*)) OVER (), 4) AS share
        FROM request_logs WHERE {_DAY} GROUP BY outcome ORDER BY outcome""",
    "latency": f"""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
        FROM request_logs WHERE {_DAY} AND latency_ms IS NOT NULL""",
    "refusal_reasons": f"""
        SELECT reason_code, count(*) AS n FROM request_logs
        WHERE {_DAY} AND outcome = 'refused' GROUP BY reason_code ORDER BY n DESC, reason_code""",
    "failed_stages": f"""
        SELECT failed_stage, count(*) AS n FROM request_logs
        WHERE {_DAY} AND failed_stage IS NOT NULL GROUP BY failed_stage ORDER BY n DESC, failed_stage""",
    "top_tables": f"""
        SELECT picked_key, max(survey_name) AS survey_name, count(*) AS n FROM request_logs
        WHERE {_DAY} AND outcome = 'answered' AND picked_key IS NOT NULL
        GROUP BY picked_key ORDER BY n DESC, picked_key LIMIT 20""",
    "turns_per_conversation": f"""
        SELECT CASE WHEN turns >= 4 THEN '4+' ELSE turns::text END AS turns, count(*) AS n
        FROM (SELECT conversation_id, count(DISTINCT turn) AS turns FROM request_logs
              WHERE {_DAY} GROUP BY conversation_id) c
        GROUP BY 1 ORDER BY 1""",
    "stage_latency": f"""
        SELECT span->>'name' AS name,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY (span->>'ms')::numeric) AS p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY (span->>'ms')::numeric) AS p95
        FROM request_logs, jsonb_array_elements(spans) AS span
        WHERE {_DAY} AND span ? 'ms' GROUP BY 1 ORDER BY 1""",
    "first_token": f"""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY first_token_ms) AS p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY first_token_ms) AS p95
        FROM request_logs WHERE {_DAY} AND first_token_ms IS NOT NULL""",
    # cost = tokens x the unit price of llm_model; the price list is configuration, not a column
    "llm_usage": f"""
        SELECT round(avg(llm_calls), 2) AS calls, round(avg(input_tokens), 2) AS input_tokens,
               round(avg(output_tokens), 2) AS output_tokens
        FROM request_logs WHERE {_DAY}""",
}

# Retention: the text goes, the row stays. Returns the rows it changed.
PURGE_QUESTIONS = "UPDATE request_logs SET question = NULL WHERE question IS NOT NULL AND created_at < :before"


def metric(session: Session, name: str, day: date) -> list[dict]:
    """Rows of one metric for a KST date. Side effects: reads the database."""
    return [dict(row) for row in session.execute(text(METRICS[name]), {"day": day}).mappings()]


def purge_questions(session: Session, before: datetime) -> int:
    """Set the question text of rows older than `before` to NULL; returns how many. Side effects: updates rows."""
    return session.execute(text(PURGE_QUESTIONS), {"before": before}).rowcount
