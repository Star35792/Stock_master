"""Serialize app-owned DuckDB jobs in the single API process."""

from threading import Lock

database_job_lock = Lock()
