"""Profile aggregates, mutations, and source-run construction."""

from dataclasses import dataclass
from datetime import datetime
import json

from pydantic import ValidationError
from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models.company import Company
from app.models.job import Job
from app.models.profile import (
    AdzunaProfileQuery,
    Keyword,
    KeywordKind,
    LocationType,
    Profile,
    ProfileKeyword,
    ProfileLocationType,
    ProfileSourceQuery,
    RemotiveProfileQuery,
)
from app.models.source import Source
from app.services.sources import (
    JobSourceRunContext,
    SourceNotFoundError,
    get_source,
    normalize_source_name,
)

type SourceProfileQuery = AdzunaProfileQuery | RemotiveProfileQuery

# One query schema per source; new adapters register their schema here so the
# service and routes stay source-agnostic.
SOURCE_QUERY_SCHEMAS: dict[str, type[SourceProfileQuery]] = {
    "adzuna": AdzunaProfileQuery,
    "remotive": RemotiveProfileQuery,
}


class ProfileError(ValueError):
    """Base error for bounded profile validation failures."""


class ProfileNotFoundError(ProfileError):
    """Raised when a profile, source, or source query does not exist."""


class ProfileConflictError(ProfileError):
    """Raised when a change collides with existing data, such as a duplicate."""


@dataclass(frozen=True)
class ProfileQueryView:
    row: ProfileSourceQuery
    source_name: str
    query: SourceProfileQuery
    company_name: str | None


@dataclass(frozen=True)
class ProfileDetail:
    profile: Profile
    location_types: tuple[LocationType, ...]
    keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    source_queries: tuple[ProfileQueryView, ...]


@dataclass(frozen=True)
class ValidatedSourceQuery:
    query: SourceProfileQuery
    company: Company | None


def list_profiles(session: Session) -> list[ProfileDetail]:
    rows = session.exec(select(Profile).order_by(func.lower(Profile.role_name))).all()
    return [_profile_detail(session, profile) for profile in rows]


def get_profile(session: Session, profile_id: int) -> ProfileDetail:
    return _profile_detail(session, _get_profile_row(session, profile_id))


def create_profile(
    session: Session,
    *,
    role_name: str,
    salary_min: int,
    match_threshold: int,
    active: bool,
    location_types: list[str],
    keywords: list[str],
    exclude_keywords: list[str],
) -> ProfileDetail:
    role_name = _required_text(role_name, "role name")
    _validate_profile_values(salary_min, match_threshold)
    normalized_locations = _normalize_locations(location_types)
    included, excluded = _normalize_keyword_groups(keywords, exclude_keywords)
    profile = Profile(
        role_name=role_name,
        salary_min=salary_min,
        match_threshold=match_threshold,
        active=active,
    )
    session.add(profile)
    try:
        session.flush()
        _replace_profile_relations(
            session,
            profile.id,
            normalized_locations,
            included,
            excluded,
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ProfileConflictError(f'profile "{role_name}" already exists') from error
    return get_profile(session, profile.id)


def update_profile(
    session: Session,
    profile_id: int,
    *,
    role_name: str,
    salary_min: int,
    match_threshold: int,
    active: bool,
    location_types: list[str],
    keywords: list[str],
    exclude_keywords: list[str],
) -> ProfileDetail:
    profile = _get_profile_row(session, profile_id)
    role_name = _required_text(role_name, "role name")
    _validate_profile_values(salary_min, match_threshold)
    locations = _normalize_locations(location_types)
    included, excluded = _normalize_keyword_groups(keywords, exclude_keywords)
    profile.role_name = role_name
    profile.salary_min = salary_min
    profile.match_threshold = match_threshold
    profile.active = active
    profile.updated_at = datetime.now()
    session.add(profile)
    try:
        _replace_profile_relations(session, profile_id, locations, included, excluded)
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ProfileConflictError(f'profile "{role_name}" already exists') from error
    return get_profile(session, profile_id)


def delete_profile(session: Session, profile_id: int) -> None:
    profile = _get_profile_row(session, profile_id)
    job_count = session.exec(
        select(func.count(Job.id)).where(Job.profile_id == profile_id)
    ).one()
    if job_count:
        raise ProfileConflictError(
            "profile is referenced by jobs; deactivate it instead"
        )
    session.exec(
        delete(ProfileSourceQuery).where(ProfileSourceQuery.profile_id == profile_id)
    )
    session.exec(
        delete(ProfileLocationType).where(ProfileLocationType.profile_id == profile_id)
    )
    session.exec(delete(ProfileKeyword).where(ProfileKeyword.profile_id == profile_id))
    session.delete(profile)
    session.commit()


def create_source_query(
    session: Session,
    *,
    profile_id: int,
    source_id: int,
    raw_query: dict,
) -> ProfileDetail:
    _get_profile_row(session, profile_id)
    source = _get_source(session, source_id)
    query_json = _validated_query_json(session, source, raw_query)
    _ensure_unique_query(session, profile_id, source_id, query_json)
    row = ProfileSourceQuery(
        profile_id=profile_id,
        source_id=source_id,
        query_json=query_json,
    )
    session.add(row)
    session.commit()
    return get_profile(session, profile_id)


def update_source_query(
    session: Session,
    *,
    profile_id: int,
    query_id: int,
    raw_query: dict,
) -> ProfileDetail:
    row = session.get(ProfileSourceQuery, query_id)
    if row is None or row.profile_id != profile_id:
        raise ProfileNotFoundError(f"source query {query_id} was not found")
    source = _get_source(session, row.source_id)
    query_json = _validated_query_json(session, source, raw_query)
    _ensure_unique_query(
        session, profile_id, row.source_id, query_json, exclude_id=query_id
    )
    row.query_json = query_json
    row.updated_at = datetime.now()
    session.add(row)
    session.commit()
    return get_profile(session, profile_id)


def delete_source_query(
    session: Session,
    *,
    profile_id: int,
    query_id: int,
) -> ProfileDetail:
    row = session.get(ProfileSourceQuery, query_id)
    if row is None or row.profile_id != profile_id:
        raise ProfileNotFoundError(f"source query {query_id} was not found")
    session.delete(row)
    session.commit()
    return get_profile(session, profile_id)


def list_profile_runs_for_source(
    session: Session,
    source_name: str,
) -> list[JobSourceRunContext]:
    source = session.exec(
        select(Source).where(
            func.lower(Source.name) == normalize_source_name(source_name)
        )
    ).first()
    if source is None:
        raise ProfileNotFoundError(f'source "{source_name}" was not found')
    rows = session.exec(
        select(ProfileSourceQuery, Profile)
        .join(Profile, Profile.id == ProfileSourceQuery.profile_id)
        .where(ProfileSourceQuery.source_id == source.id, Profile.active.is_(True))
        .order_by(func.lower(Profile.role_name), ProfileSourceQuery.id)
    ).all()
    contexts = []
    for query_row, profile in rows:
        detail = _profile_detail(session, profile)
        validated = _validate_source_query_json(session, source, query_row.query_json)
        source_query = validated.query.model_dump(mode="json", exclude_none=True)
        if validated.company is not None:
            source_query["company_name"] = validated.company.name
        contexts.append(
            JobSourceRunContext(
                profile_id=profile.id,
                keywords=detail.keywords,
                exclude_keywords=detail.exclude_keywords,
                location_types=tuple(value.value for value in detail.location_types),
                salary_min=profile.salary_min,
                match_threshold=profile.match_threshold,
                company_name=(
                    validated.company.name if validated.company is not None else None
                ),
                source_query=source_query,
            )
        )
    return contexts


def _profile_detail(session: Session, profile: Profile) -> ProfileDetail:
    locations = session.exec(
        select(ProfileLocationType.location_type)
        .where(ProfileLocationType.profile_id == profile.id)
        .order_by(ProfileLocationType.location_type)
    ).all()
    keyword_rows = session.exec(
        select(Keyword.name, ProfileKeyword.kind)
        .join(ProfileKeyword, ProfileKeyword.keyword_id == Keyword.id)
        .where(ProfileKeyword.profile_id == profile.id)
        .order_by(func.lower(Keyword.name))
    ).all()
    query_rows = session.exec(
        select(ProfileSourceQuery, Source)
        .join(Source, Source.id == ProfileSourceQuery.source_id)
        .where(ProfileSourceQuery.profile_id == profile.id)
        .order_by(func.lower(Source.name), ProfileSourceQuery.id)
    ).all()
    queries = []
    for row, source in query_rows:
        validated = _validate_source_query_json(session, source, row.query_json)
        queries.append(
            ProfileQueryView(
                row,
                source.name,
                validated.query,
                validated.company.name if validated.company is not None else None,
            )
        )
    return ProfileDetail(
        profile=profile,
        location_types=tuple(LocationType(value) for value in locations),
        keywords=tuple(
            name for name, kind in keyword_rows if kind == KeywordKind.include
        ),
        exclude_keywords=tuple(
            name for name, kind in keyword_rows if kind == KeywordKind.exclude
        ),
        source_queries=tuple(queries),
    )


def _replace_profile_relations(
    session: Session,
    profile_id: int,
    locations: tuple[LocationType, ...],
    included: tuple[str, ...],
    excluded: tuple[str, ...],
) -> None:
    session.exec(
        delete(ProfileLocationType).where(ProfileLocationType.profile_id == profile_id)
    )
    session.exec(delete(ProfileKeyword).where(ProfileKeyword.profile_id == profile_id))
    session.add_all(
        ProfileLocationType(profile_id=profile_id, location_type=value)
        for value in locations
    )
    for kind, names in (
        (KeywordKind.include, included),
        (KeywordKind.exclude, excluded),
    ):
        for name in names:
            keyword = session.exec(
                select(Keyword).where(func.lower(Keyword.name) == name.lower())
            ).first()
            if keyword is None:
                keyword = Keyword(name=name)
                session.add(keyword)
                session.flush()
            session.add(
                ProfileKeyword(
                    profile_id=profile_id,
                    keyword_id=keyword.id,
                    kind=kind,
                )
            )


def _normalize_keyword_groups(
    included: list[str], excluded: list[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    normalized_included = _normalize_terms(included, "keywords", require=True)
    normalized_excluded = _normalize_terms(excluded, "excluded keywords")
    overlap = {value.lower() for value in normalized_included} & {
        value.lower() for value in normalized_excluded
    }
    if overlap:
        raise ProfileError("a keyword cannot be both included and excluded")
    return normalized_included, normalized_excluded


def _normalize_terms(
    values: list[str], field_name: str, *, require: bool = False
) -> tuple[str, ...]:
    normalized = []
    seen = set()
    for value in values:
        value = value.strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            raise ProfileError(f"{field_name} contain a duplicate value")
        seen.add(key)
        normalized.append(value)
    if require and not normalized:
        raise ProfileError("at least one keyword is required")
    return tuple(normalized)


def _normalize_locations(values: list[str]) -> tuple[LocationType, ...]:
    try:
        locations = tuple(dict.fromkeys(LocationType(value) for value in values))
    except ValueError as error:
        raise ProfileError("invalid location type") from error
    if not locations:
        raise ProfileError("at least one location type is required")
    return locations


def _validate_profile_values(salary_min: int, match_threshold: int) -> None:
    if salary_min < 0:
        raise ProfileError("salary minimum must be at least zero")
    if not 1 <= match_threshold <= 100:
        raise ProfileError("match threshold must be between 1 and 100")


def _required_text(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise ProfileError(f"{label} is required")
    return value


def _get_profile_row(session: Session, profile_id: int) -> Profile:
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise ProfileNotFoundError(f"profile {profile_id} was not found")
    return profile


def _get_source(session: Session, source_id: int) -> Source:
    try:
        return get_source(session, source_id)
    except SourceNotFoundError as error:
        raise ProfileNotFoundError(str(error)) from error


def _query_schema_for(source: Source) -> type[SourceProfileQuery]:
    schema = SOURCE_QUERY_SCHEMAS.get(normalize_source_name(source.name))
    if schema is None:
        raise ProfileError(f'no query schema is registered for source "{source.name}"')
    return schema


def _parse_source_query(source: Source, query_json: str) -> SourceProfileQuery:
    schema = _query_schema_for(source)
    try:
        raw_query = json.loads(query_json)
    except json.JSONDecodeError as error:
        raise ProfileError("source query contains malformed JSON") from error
    try:
        return schema.model_validate(raw_query)
    except ValidationError as error:
        raise ProfileError(_format_query_validation_error(source, error)) from error


def _validate_source_query_json(
    session: Session,
    source: Source,
    query_json: str,
) -> ValidatedSourceQuery:
    query = _parse_source_query(source, query_json)
    return ValidatedSourceQuery(
        query=query, company=_resolve_query_company(session, query)
    )


def _validated_query_json(session: Session, source: Source, raw_query: dict) -> str:
    schema = _query_schema_for(source)
    try:
        query = schema.model_validate(raw_query)
    except ValidationError as error:
        raise ProfileError(_format_query_validation_error(source, error)) from error
    _resolve_query_company(session, query)
    return query.model_dump_json(exclude_none=True)


def _resolve_query_company(
    session: Session,
    query: SourceProfileQuery,
) -> Company | None:
    if not isinstance(query, RemotiveProfileQuery):
        return None
    if query.company_id is None:
        return None
    company = session.get(Company, query.company_id)
    if company is None:
        raise ProfileError(f"company {query.company_id} was not found")
    return company


def _format_query_validation_error(source: Source, error: ValidationError) -> str:
    messages = []
    source_name = source.name.lower()
    for issue in error.errors():
        field = ".".join(str(part) for part in issue["loc"]) or "query"
        if field == "category" and source_name == "remotive":
            messages.append("category must be one of the supported Remotive categories")
        elif field == "category":
            messages.append("category must not be blank")
        elif field == "search":
            messages.append("search must not be blank")
        elif field == "what":
            messages.append("what is required")
        elif field == "where":
            messages.append("where must not be blank")
        elif field == "limit":
            messages.append("limit must be between 1 and 10")
        elif field == "company_id":
            messages.append("company must be a persisted company")
        elif field == "schema_version":
            messages.append("schema version must be 1")
        else:
            messages.append(f"{field} is not valid")
    return "; ".join(dict.fromkeys(messages))


def _ensure_unique_query(
    session: Session,
    profile_id: int,
    source_id: int,
    query_json: str,
    *,
    exclude_id: int | None = None,
) -> None:
    rows = session.exec(
        select(ProfileSourceQuery).where(
            ProfileSourceQuery.profile_id == profile_id,
            ProfileSourceQuery.source_id == source_id,
        )
    ).all()
    canonical = json.dumps(json.loads(query_json), sort_keys=True)
    for row in rows:
        if (
            row.id != exclude_id
            and json.dumps(json.loads(row.query_json), sort_keys=True) == canonical
        ):
            raise ProfileConflictError("duplicate source query")
