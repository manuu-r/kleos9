"""Separate static origin: uvicorn kleos.preview_api:app --no-access-log.

Access logs must be disabled because asset paths contain short-lived scoped
capabilities. No user-supplied code is executed by this process.
"""
from .companion_preview import create_preview_app
from .config import get_settings

app = create_preview_app(get_settings())
