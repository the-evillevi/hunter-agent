"""Reusable orchestration for manually ingesting S&P 500 companies."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.config import load_config
from app.models.config import AppConfig
from app.models.source import Source
from app.services.company_sources import CompanySourceAdapter, CompanySourceRegistry
from app.services.sp500_company_import import import_sp500_companies
from app.services.sp500_enrichment import enrich_sp500_rank_and_tier
from app.services.ssga_spy_holdings import SSGASpyHoldingsSource


IngestionStage = Literal["selection", "provider", "normalization", "persistence"]
IngestionStatus = Literal["success", "partial_failure", "failed"]


class Sp500IngestionFailure(BaseModel):
    """One provider-level or row-level ingestion failure."""

    source: str
    stage: IngestionStage
    message: str
    symbol: str | None = None
    name: str | None = None


class Sp500IngestionSummary(BaseModel):
    """Structured result returned to manual operators and future schedulers."""

    status: IngestionStatus = "success"
    sources: list[str] = Field(default_factory=list)
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    removed_from_index: int = 0
    failed: int = 0
    failures: list[Sp500IngestionFailure] = Field(default_factory=list)

    def add_failure(self, failure: Sp500IngestionFailure) -> None:
        """Append a failure and keep the public count synchronized."""
        self.failures.append(failure)
        self.failed = len(self.failures)

    def finish(self) -> "Sp500IngestionSummary":
        """Derive the overall status after all enabled sources have run."""
        if not self.failures:
            self.status = "success"
        elif any((self.created, self.updated, self.unchanged, self.removed_from_index)):
            self.status = "partial_failure"
        else:
            self.status = "failed"
        return self

    def response_status_code(self) -> int:
        """Map failure stages to an HTTP status suitable for automation."""
        if not self.failures:
            return 200
        if any(failure.stage == "selection" for failure in self.failures):
            return 409
        if any(failure.stage == "persistence" for failure in self.failures):
            return 500
        return 502


def build_company_source_registry(config: AppConfig) -> CompanySourceRegistry:
    """Build the known company-source adapters from validated configuration."""
    source_config = config.sources.ssga_spy_holdings
    registry = CompanySourceRegistry()
    registry.register(
        SSGASpyHoldingsSource(
            workbook_path=source_config.workbook_path,
            workbook_url=str(source_config.workbook_url),
        )
    )
    return registry


def resolve_enabled_company_sources(
    session: Session,
    *,
    config: AppConfig,
    registry: CompanySourceRegistry,
) -> list[CompanySourceAdapter]:
    """Use persisted toggles when present, otherwise typed config defaults."""
    persisted_sources = {
        source.name.strip().casefold(): source
        for source in session.exec(select(Source)).all()
    }
    enabled_adapters: list[CompanySourceAdapter] = []
    for source_name in registry.source_names():
        persisted_source = persisted_sources.get(source_name.casefold())
        if persisted_source is not None:
            enabled = persisted_source.enabled
        else:
            enabled = company_source_enabled_in_config(config, source_name)
        if enabled:
            enabled_adapters.extend(registry.resolve_selected([source_name]))
    return enabled_adapters


def company_source_enabled_in_config(config: AppConfig, source_name: str) -> bool:
    """Return the typed-config fallback for a registered company source."""
    if source_name == "ssga_spy_holdings":
        return config.sources.ssga_spy_holdings.enabled
    return False


async def run_sp500_ingestion(
    session: Session,
    *,
    config: AppConfig | None = None,
    registry: CompanySourceRegistry | None = None,
    now: Callable[[], datetime] | None = None,
) -> Sp500IngestionSummary:
    """Run every enabled company source and aggregate a reusable summary."""
    resolved_config = config or load_config()
    resolved_registry = registry or build_company_source_registry(resolved_config)
    adapters = resolve_enabled_company_sources(
        session,
        config=resolved_config,
        registry=resolved_registry,
    )
    summary = Sp500IngestionSummary(
        sources=[adapter.identity.name for adapter in adapters]
    )
    if not adapters:
        summary.add_failure(
            Sp500IngestionFailure(
                source="company_sources",
                stage="selection",
                message="no S&P 500 company sources are enabled",
            )
        )
        return summary.finish()

    imported_at = (now or (lambda: datetime.now(timezone.utc)))()
    for adapter in adapters:
        await ingest_company_source(
            adapter,
            session=session,
            config=resolved_config,
            imported_at=imported_at,
            summary=summary,
        )
    return summary.finish()


async def ingest_company_source(
    adapter: CompanySourceAdapter,
    *,
    session: Session,
    config: AppConfig,
    imported_at: datetime,
    summary: Sp500IngestionSummary,
) -> None:
    """Run one provider without preventing later enabled providers from running."""
    source_name = adapter.identity.name
    try:
        raw_companies = await adapter.fetch(config)
    except Exception as error:
        summary.add_failure(
            Sp500IngestionFailure(
                source=source_name,
                stage="provider",
                message=str(error),
            )
        )
        return

    normalized_companies = []
    try:
        normalized_companies = [
            adapter.normalize(raw_company, config) for raw_company in raw_companies
        ]
    except Exception as error:
        # Do not persist a partial authoritative dataset: doing so could mark
        # otherwise valid constituents as removed from the index.
        summary.add_failure(
            Sp500IngestionFailure(
                source=source_name,
                stage="normalization",
                message=str(error),
            )
        )
        return

    import_summary = import_sp500_companies(
        session,
        enrich_sp500_rank_and_tier(normalized_companies),
        imported_at=imported_at,
    )
    summary.created += import_summary.created
    summary.updated += import_summary.updated
    summary.unchanged += import_summary.unchanged
    summary.removed_from_index += import_summary.removed_from_index
    for failure in import_summary.failures:
        summary.add_failure(
            Sp500IngestionFailure(
                source=source_name,
                stage="persistence",
                symbol=failure.symbol,
                name=failure.name,
                message=failure.reason,
            )
        )
