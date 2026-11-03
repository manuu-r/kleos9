"""Trusted async-local task attribution; never accepted from provider output."""
from contextvars import ContextVar

model_task = ContextVar("kleos_model_task", default=None)
