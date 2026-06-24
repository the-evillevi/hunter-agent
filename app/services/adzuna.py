"""Adzuna job source adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
from typing import Any

import httpx

from app.config import load_config
from app.models.config import AdzunaSourceConfig, is_placeholder
from app.services.sources import (
    JobSourceIdentity,
    JobSourceRunContext,
    NormalizedJob,
)

logger = logging.getLogger(__name__)


class AdzunaAdapterError(RuntimeError):
    """Raised when Adzuna cannot return a usable response."""


class AdzunaJobSourceAdapter:
    """Fetch and normalize authenticated Adzuna job listings."""

    identity = JobSourceIdentity(name="adzuna", display_name="Adzuna")

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        settings: AdzunaSourceConfig | None = None,
        base_url: str = "https://api.adzuna.com/v1/api/jobs",
        timeout: float = 10.0,
    ) -> None:
        self._client = client
        self._settings = settings
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def fetch(self, context: JobSourceRunContext) -> Sequence[Mapping[str, Any]]:
        """Fetch Adzuna pages and skip unusable records inside each page."""
        settings = self._settings_for_fetch()
        self._require_credentials(settings)
        accepted_jobs: list[Mapping[str, Any]] = []
        for page in range(1, settings.max_pages + 1):
            response_payload = await self._get_json(
                page, _request_params(context, settings), settings
            )
            results = response_payload.get("results")
            if not isinstance(results, list):
                raise AdzunaAdapterError("Adzuna response did not include a results list")
            if not results:
                break

            for raw_job in results:
                if not isinstance(raw_job, Mapping):
                    self._skip("job payload is not an object", raw_job)
                    continue
                if not _has_minimum_identity(raw_job):
                    self._skip(
                        "job is missing title, company, location, or stable link",
                        raw_job,
                    )
                    continue
                accepted_jobs.append(raw_job)

            if len(results) < settings.results_per_page:
                break
        return accepted_jobs

    def normalize(
        self,
        raw_job: Mapping[str, Any],
        context: JobSourceRunContext,
    ) -> NormalizedJob:
        """Convert one Adzuna job payload into the shared normalized shape."""
        title = _required_text(raw_job.get("title"), "title")
        company = _required_nested_text(raw_job, "company", "display_name")
        location = _required_nested_text(raw_job, "location", "display_name")
        external_id = _optional_text(raw_job.get("id"))
        return NormalizedJob.from_source(
            source=self.identity,
            title=title,
            company=company,
            location=location,
            url=_optional_text(raw_job.get("redirect_url")),
            description=_optional_text(raw_job.get("description")),
            external_id=external_id,
            raw_metadata={
                "created": _optional_text(raw_job.get("created")),
                "salary_min": _metadata_value(raw_job.get("salary_min")),
                "salary_max": _metadata_value(raw_job.get("salary_max")),
                "salary_is_predicted": _metadata_value(
                    raw_job.get("salary_is_predicted")
                ),
                "category": _metadata_value(raw_job.get("category")),
                "contract_time": _optional_text(raw_job.get("contract_time")),
                "contract_type": _optional_text(raw_job.get("contract_type")),
                "latitude": _metadata_value(raw_job.get("latitude")),
                "longitude": _metadata_value(raw_job.get("longitude")),
                "location_area": _location_area(raw_job),
                "payload": dict(raw_job),
            },
            profile_id=context.profile_id,
        )

    async def _get_json(
        self,
        page: int,
        params: dict[str, str],
        settings: AdzunaSourceConfig,
    ) -> Mapping[str, Any]:
        try:
            url = f"{self._base_url}/{settings.country}/search/{page}"
            if self._client is not None:
                response = await self._client.get(url, params=params)
                return _response_json(response)

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
                return _response_json(response)
        except httpx.RequestError as error:
            raise AdzunaAdapterError("Adzuna API request failed") from error

    def _settings_for_fetch(self) -> AdzunaSourceConfig:
        return self._settings or load_config().sources.adzuna

    def _require_credentials(self, settings: AdzunaSourceConfig) -> None:
        if (
            not settings.app_id.strip()
            or not settings.app_key.strip()
            or is_placeholder(settings.app_id)
            or is_placeholder(settings.app_key)
        ):
            raise AdzunaAdapterError("Adzuna credentials are missing")

    def _skip(self, reason: str, payload: object) -> None:
        logger.warning("Skipping Adzuna job%s: %s", _job_label(payload), reason)


def _request_params(
    context: JobSourceRunContext,
    settings: AdzunaSourceConfig,
) -> dict[str, str]:
    params = {
        "app_id": settings.app_id,
        "app_key": settings.app_key,
        "results_per_page": str(settings.results_per_page),
        "content-type": "application/json",
    }
    for query_key, param_key in (
        ("what", "what"),
        ("where", "where"),
        ("category", "category"),
    ):
        value = context.source_query.get(query_key)
        if value is not None and str(value).strip():
            params[param_key] = str(value).strip()
    if context.exclude_keywords:
        params["what_exclude"] = " ".join(
            keyword.strip() for keyword in context.exclude_keywords if keyword.strip()
        )
    if context.salary_min is not None and context.salary_min > 0:
        params["salary_min"] = str(context.salary_min)
    if context.source_query.get("full_time") is True:
        params["full_time"] = "1"
    if context.source_query.get("permanent") is True:
        params["permanent"] = "1"
    return params


def _response_json(response: httpx.Response) -> Mapping[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise AdzunaAdapterError(
            f"Adzuna API returned HTTP {error.response.status_code}"
        ) from error
    try:
        payload = response.json()
    except ValueError as error:
        raise AdzunaAdapterError("Adzuna API returned invalid JSON") from error
    if not isinstance(payload, Mapping):
        raise AdzunaAdapterError("Adzuna response was not a JSON object")
    return payload


def _has_minimum_identity(raw_job: Mapping[str, Any]) -> bool:
    return (
        _optional_text(raw_job.get("title")) is not None
        and _nested_text(raw_job, "company", "display_name") is not None
        and _nested_text(raw_job, "location", "display_name") is not None
        and (
            _optional_text(raw_job.get("id")) is not None
            or _optional_text(raw_job.get("redirect_url")) is not None
        )
    )


def _required_text(value: object, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise AdzunaAdapterError(f"Adzuna job is missing {field_name}")
    return text


def _required_nested_text(
    raw_job: Mapping[str, Any],
    object_name: str,
    field_name: str,
) -> str:
    text = _nested_text(raw_job, object_name, field_name)
    if text is None:
        raise AdzunaAdapterError(f"Adzuna job is missing {object_name}.{field_name}")
    return text


def _nested_text(
    raw_job: Mapping[str, Any],
    object_name: str,
    field_name: str,
) -> str | None:
    value = raw_job.get(object_name)
    if not isinstance(value, Mapping):
        return None
    return _optional_text(value.get(field_name))


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _metadata_value(value: object) -> object:
    return value if value not in ("", None) else None


def _location_area(raw_job: Mapping[str, Any]) -> object:
    location = raw_job.get("location")
    if not isinstance(location, Mapping):
        return None
    return _metadata_value(location.get("area"))


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
