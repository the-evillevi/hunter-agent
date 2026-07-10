"""Shared helper for reading URL-encoded form fields in HTMX routes.

Several routes hand-roll ``parse_qs`` with subtly different options; the
important one is ``keep_blank_values``: without it an emptied input is
indistinguishable from a missing one, which breaks "clear this field"
submissions. New routes should read fields through here.
"""

from urllib.parse import parse_qs

from fastapi import Request


async def form_field(request: Request, name: str) -> str | None:
    """Return one form field's value, preserving blank submissions."""
    body = (await request.body()).decode()
    return parse_qs(body, keep_blank_values=True).get(name, [None])[0]
