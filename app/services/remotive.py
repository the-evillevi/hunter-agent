"""Remotive job source adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
from typing import Any

import httpx

from app.services.sources import (
    JobSourceIdentity,
    JobSourceRunContext,
    NormalizedJob,
    default_source_registry,
)

logger = logging.getLogger(__name__)


class RemotiveAdapterError(RuntimeError):
    """Raised when Remotive cannot return a usable response."""


class RemotiveJobSourceAdapter:
    """Fetch and normalize public Remotive remote job listings."""

    identity = JobSourceIdentity(name="remotive", display_name="Remotive")

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://remotive.com/api/remote-jobs",
        timeout: float = 10.0,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._timeout = timeout

    async def fetch(self, context: JobSourceRunContext) -> Sequence[Mapping[str, Any]]:
        """Fetch Remotive jobs and skip records outside the run context."""
        params = _request_params(context.source_query)
        response_payload = await self._get_json(params)
        jobs = response_payload.get("jobs")
        if not isinstance(jobs, list):
            raise RemotiveAdapterError("Remotive response did not include a jobs list")

        accepted_jobs: list[Mapping[str, Any]] = []
        for raw_job in jobs:
            if not isinstance(raw_job, Mapping):
                self._skip("job payload is not an object", raw_job)
                continue
            if not _has_minimum_identity(raw_job):
                self._skip("job is missing title, company, or location", raw_job)
                continue
            if _matches_excluded_keyword(raw_job, context.exclude_keywords):
                self._skip("job matched an excluded keyword", raw_job)
                continue
            if not _matches_location_types(raw_job, context.location_types):
                self._skip("job did not match requested location types", raw_job)
                continue
            accepted_jobs.append(raw_job)
        return accepted_jobs

    def normalize(
        self,
        raw_job: Mapping[str, Any],
        context: JobSourceRunContext,
    ) -> NormalizedJob:
        """Convert one Remotive job payload into the shared normalized shape."""
        title = _required_text(raw_job, "title")
        company = _required_text(raw_job, "company_name")
        location = _required_text(raw_job, "candidate_required_location")
        external_id = _optional_text(raw_job.get("id"))
        publication_date = _optional_text(raw_job.get("publication_date"))
        return NormalizedJob.from_source(
            source=self.identity,
            title=title,
            company=company,
            location=location,
            url=_optional_text(raw_job.get("url")),
            description=_optional_text(raw_job.get("description")),
            external_id=external_id,
            raw_metadata={
                "category": _optional_text(raw_job.get("category")),
                "job_type": _optional_text(raw_job.get("job_type")),
                "salary": _optional_text(raw_job.get("salary")),
                "tags": _metadata_value(raw_job.get("tags")),
                "publication_date": publication_date,
                "company_logo": _optional_text(raw_job.get("company_logo")),
                "payload": dict(raw_job),
            },
            profile_id=context.profile_id,
        )

    async def _get_json(self, params: dict[str, str]) -> Mapping[str, Any]:
        try:
            if self._client is not None:
                response = await self._client.get(self._base_url, params=params)
                return _response_json(response)

            headers = {
                "User-Agent": "hunter-agent Remotive adapter; source attribution: Remotive"
            }
            async with httpx.AsyncClient(
                timeout=self._timeout, headers=headers
            ) as client:
                response = await client.get(self._base_url, params=params)
                return _response_json(response)
        except httpx.RequestError as error:
            raise RemotiveAdapterError(
                f"Remotive API request failed: {error}"
            ) from error

    def _skip(self, reason: str, payload: object) -> None:
        logger.warning("Skipping Remotive job%s: %s", _job_label(payload), reason)


def _request_params(source_query: Mapping[str, Any]) -> dict[str, str]:
    params = {}
    for query_key, param_key in (
        ("category", "category"),
        ("company_name", "company_name"),
        ("search", "search"),
        ("limit", "limit"),
    ):
        value = source_query.get(query_key)
        if value is not None and str(value).strip():
            params[param_key] = str(value).strip()
    return params


def _response_json(response: httpx.Response) -> Mapping[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise RemotiveAdapterError(
            f"Remotive API returned HTTP {error.response.status_code}"
        ) from error
    try:
        payload = response.json()
    except ValueError as error:
        raise RemotiveAdapterError("Remotive API returned invalid JSON") from error
    if not isinstance(payload, Mapping):
        raise RemotiveAdapterError("Remotive response was not a JSON object")
    return payload


def _has_minimum_identity(raw_job: Mapping[str, Any]) -> bool:
    return all(
        _optional_text(raw_job.get(field_name))
        for field_name in ("title", "company_name", "candidate_required_location")
    )


def _matches_excluded_keyword(
    raw_job: Mapping[str, Any], exclude_keywords: tuple[str, ...]
) -> bool:
    if not exclude_keywords:
        return False
    haystack = " ".join(
        str(value)
        for value in (
            raw_job.get("title"),
            raw_job.get("company_name"),
            raw_job.get("category"),
            raw_job.get("tags"),
            raw_job.get("description"),
        )
        if value is not None
    ).casefold()
    return any(keyword.strip().casefold() in haystack for keyword in exclude_keywords)


def _matches_location_types(
    raw_job: Mapping[str, Any], location_types: tuple[str, ...]
) -> bool:
    if not location_types or "remote" in location_types:
        return True
    location = _required_text(raw_job, "candidate_required_location").casefold()
    if "hybrid" in location_types and "hybrid" in location:
        return True
    if "onsite" in location_types and any(
        marker in location for marker in ("onsite", "on-site", "office")
    ):
        return True
    return False


def _required_text(raw_job: Mapping[str, Any], field_name: str) -> str:
    value = _optional_text(raw_job.get(field_name))
    if value is None:
        raise RemotiveAdapterError(f"Remotive job is missing {field_name}")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _metadata_value(value: object) -> object:
    return value if value not in ("", None) else None


def _job_label(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    parts = []
    job_id = _optional_text(payload.get("id"))
    title = _optional_text(payload.get("title"))
    if job_id is not None:
        parts.append(f"id={job_id}")
    if title is not None:
        parts.append(f"title={title!r}")
    if not parts:
        return ""
    return " " + " ".join(parts)


# Self-register so importing this module (which app.services.sources does at
# its bottom) makes the adapter resolvable through the default registry.
default_source_registry.register(RemotiveJobSourceAdapter())
