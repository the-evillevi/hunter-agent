"""Tailor a master resume into a job-specific variant.

Given a base resume and one scored job, this service asks the scoring model
how relevant each resume fact is to that job, drops weak facts, reorders the
rest by relevance, and saves the result as a new ResumeProfile variant. The
base resume is never modified, and every run is recorded in
resume_tailor_runs so scores stay attributable to a model and prompt version.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlmodel import Session

from app.models.job import Job
from app.models.resume import (
    ResumeItemDetail,
    ResumeProfile,
    ResumeSectionDetail,
    ResumeTailorRun,
    SectionType,
)
from app.services.ollama_client import OllamaClient, ScoringResult
from app.services.resume_crud import (
    add_item,
    add_section,
    create_resume_profile,
    get_resume_detail,
)


# Items scoring below this are left out of the tailored variant.
DEFAULT_RELEVANCE_THRESHOLD = 40

# Sections copied verbatim, never scored or filtered: a resume without
# contact info is never the right output, whatever the job says.
UNSCORED_SECTION_TYPES = {SectionType.basics}

# Local Ollama serves one model; a couple of parallel requests keep the
# pipeline busy without starving the model server.
MAX_CONCURRENT_SCORING_REQUESTS = 3


class ResumeTailor:
    """Produces scored, filtered resume variants for one job at a time."""

    def __init__(
        self,
        client: OllamaClient | None = None,
        *,
        threshold: int = DEFAULT_RELEVANCE_THRESHOLD,
    ) -> None:
        self.client = client or OllamaClient()
        self.threshold = threshold

    def tailor_to_job(
        self,
        session: Session,
        *,
        base_resume_id: int,
        job_id: int,
    ) -> ResumeProfile:
        """Score, filter, and reorder the base resume into a new variant."""
        base = get_resume_detail(session, base_resume_id)
        if base is None:
            raise LookupError(f"Resume profile {base_resume_id} was not found")

        job = session.get(Job, job_id)
        if job is None:
            raise LookupError(f"Job {job_id} was not found")

        scored_sections = self._score_sections(base.sections, job)

        try:
            variant = create_resume_profile(
                session,
                name=self._variant_name(job),
                base_resume_id=base_resume_id,
                job_id=job_id,
            )

            written_sections = 0
            for section, scored_items in scored_sections:
                kept_items = self._select_items(section, scored_items)
                if not kept_items:
                    continue

                new_section = add_section(
                    session,
                    profile_id=variant.id,
                    section_type=section.section_type,
                    title=section.title,
                    order_idx=written_sections,
                )
                written_sections += 1
                for item_idx, (item, result) in enumerate(kept_items):
                    add_item(
                        session,
                        section_id=new_section.id,
                        content=item.content,
                        order_idx=item_idx,
                        relevance_score=result.score if result else None,
                        score_reasoning=result.reasoning if result else None,
                        score_is_fallback=result.is_fallback if result else False,
                    )

            session.add(
                ResumeTailorRun(
                    source_profile_id=base_resume_id,
                    output_profile_id=variant.id,
                    job_id=job_id,
                    model=self.client.model_name,
                    prompt_version=self.client.prompt_version,
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            raise

        session.refresh(variant)
        return variant

    def _score_sections(
        self,
        sections: list[ResumeSectionDetail],
        job: Job,
    ) -> list[
        tuple[ResumeSectionDetail, list[tuple[ResumeItemDetail, ScoringResult | None]]]
    ]:
        """Score every scorable item; unscored sections get None results."""
        scorable_items = [
            item
            for section in sections
            if section.section_type not in UNSCORED_SECTION_TYPES
            for item in section.items
        ]

        with ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_SCORING_REQUESTS
        ) as executor:
            results = list(
                executor.map(
                    lambda item: self.client.score_item(
                        item_content=json.dumps(item.content, ensure_ascii=False),
                        job_title=job.title,
                        job_description=job.description or "",
                    ),
                    scorable_items,
                )
            )
        results_by_item_id = {
            item.id: result for item, result in zip(scorable_items, results)
        }

        return [
            (
                section,
                [(item, results_by_item_id.get(item.id)) for item in section.items],
            )
            for section in sections
        ]

    def _select_items(
        self,
        section: ResumeSectionDetail,
        scored_items: list[tuple[ResumeItemDetail, ScoringResult | None]],
    ) -> list[tuple[ResumeItemDetail, ScoringResult | None]]:
        """Filter weak items and order the survivors by relevance."""
        if section.section_type in UNSCORED_SECTION_TYPES:
            return scored_items

        kept = [
            (item, result)
            for item, result in scored_items
            if result is not None and result.score >= self.threshold
        ]
        kept.sort(key=lambda pair: pair[1].score, reverse=True)
        return kept

    def _variant_name(self, job: Job) -> str:
        job_slug = job.title.lower().replace(" ", "-")[:40]
        return f"tailored-{job_slug}-{datetime.now():%Y%m%d-%H%M%S}"
