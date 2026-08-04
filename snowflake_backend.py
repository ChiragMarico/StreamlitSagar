"""Snowflake persistence adapter with a safe local-mode fallback."""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime, timezone
from typing import Any


APP_DATABASE = os.getenv("DN_APP_DATABASE", "DEBIT_NOTE_APP")
APP_SCHEMA = os.getenv("DN_APP_SCHEMA", "CORE")
PDF_STAGE = f"@{APP_DATABASE}.{APP_SCHEMA}.PDF_STAGE"
REPORT_STAGE = f"@{APP_DATABASE}.{APP_SCHEMA}.REPORT_STAGE"


def active_session():
    """Return Snowflake's active session, or None during local development."""
    try:
        from snowflake.snowpark.context import get_active_session
        return get_active_session()
    except Exception:
        return None


def is_snowflake() -> bool:
    return active_session() is not None


def current_user() -> str:
    session = active_session()
    if session is None:
        return "local-user"
    return str(session.sql("SELECT CURRENT_USER()").collect()[0][0])


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def existing_hashes(hashes: list[str]) -> set[str]:
    session = active_session()
    if session is None or not hashes:
        return set()
    placeholders = ",".join("?" for _ in hashes)
    rows = session.sql(
        f"SELECT FILE_HASH FROM {APP_DATABASE}.{APP_SCHEMA}.PROCESSED_REGISTRY "
        f"WHERE FILE_HASH IN ({placeholders})", params=hashes
    ).collect()
    return {str(row[0]) for row in rows}


def save_run(
    run_id: str,
    pdfs: dict[str, bytes],
    records: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    report: bytes | None = None,
) -> None:
    """Persist one successful app run atomically enough for audit/replay."""
    session = active_session()
    if session is None:
        return
    actor = current_user()
    started = datetime.now(timezone.utc)
    total = sum(float(record.get("Total Amount") or 0) for record in records)
    session.sql(
        f"INSERT INTO {APP_DATABASE}.{APP_SCHEMA}.PROCESSING_RUNS "
        "(RUN_ID, STARTED_AT, COMPLETED_AT, STATUS, USER_NAME, PDF_COUNT, RECORD_COUNT, "
        "EXCEPTION_COUNT, TOTAL_AMOUNT) SELECT ?, ?, ?, 'COMPLETED', ?, ?, ?, ?, ?",
        params=[run_id, started, datetime.now(timezone.utc), actor, len(pdfs), len(records), len(issues), total],
    ).collect()
    for name, data in pdfs.items():
        digest = file_hash(data)
        safe_name = name.replace("/", "_").replace("\\", "_")
        stage_path = f"{PDF_STAGE}/{run_id}/{safe_name}"
        session.file.put_stream(io.BytesIO(data), stage_path, auto_compress=False, overwrite=False)
        related = [record for record in records if record.get("PDF Name") == name]
        first = related[0] if related else {}
        session.sql(
            f"INSERT INTO {APP_DATABASE}.{APP_SCHEMA}.PROCESSED_REGISTRY "
            "(FILE_HASH, PDF_NAME, VENDOR_NAME, INVOICE_REF_NO, RUN_ID, STAGE_PATH, PROCESSED_AT, USER_NAME) "
            "SELECT ?, ?, ?, ?, ?, ?, ?, ?",
            params=[digest, name, first.get("Vendor Name"), first.get("InvoiceRefNo."), run_id,
                    stage_path, datetime.now(timezone.utc), actor],
        ).collect()
    if records:
        payload = [(run_id, json.dumps(record, default=str)) for record in records]
        session.create_dataframe(payload, schema=["RUN_ID", "RECORD_JSON"]).write.mode("append").save_as_table(
            f"{APP_DATABASE}.{APP_SCHEMA}.EXTRACTED_RECORDS"
        )
    if issues:
        payload = [(run_id, json.dumps(issue, default=str)) for issue in issues]
        session.create_dataframe(payload, schema=["RUN_ID", "ISSUE_JSON"]).write.mode("append").save_as_table(
            f"{APP_DATABASE}.{APP_SCHEMA}.PROCESSING_EXCEPTIONS"
        )
    if report:
        session.file.put_stream(io.BytesIO(report), f"{REPORT_STAGE}/{run_id}/Debit_Note_Extraction.xlsx",
                                auto_compress=False, overwrite=True)


def recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    session = active_session()
    if session is None:
        return []
    rows = session.sql(
        f"SELECT RUN_ID, COMPLETED_AT, USER_NAME, PDF_COUNT, RECORD_COUNT, EXCEPTION_COUNT, TOTAL_AMOUNT "
        f"FROM {APP_DATABASE}.{APP_SCHEMA}.PROCESSING_RUNS ORDER BY COMPLETED_AT DESC LIMIT ?",
        params=[limit],
    ).collect()
    columns = ["Run ID", "Completed", "User", "PDFs", "Records", "Exceptions", "Total Amount"]
    return [dict(zip(columns, row)) for row in rows]
