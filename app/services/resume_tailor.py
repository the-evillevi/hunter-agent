"""Bounded generator-critic orchestration for job-specific resume variants."""

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlmodel import Session

from app.config import PROJECT_ROOT, load_config
from app.models.job import Job
from app.models.resume import (
    ResumeItemDetail,
    ResumeProfile,
    ResumeSectionDetail,
    ResumeTailorRun,
    ResumeTailorRunItem,
    SectionType,
)
from app.models.tailoring import GeneratedResumeDraft, ResumeCritique
from app.services.ai.completion import (
    CompletionProvider,
    CompletionRequest,
    CompletionResponse,
)
from app.services.ai.errors import AIResponseError
from app.services.ai.factory import create_cloud_completion_provider
from app.services.ai.ollama import OllamaCompletionProvider
from app.services.prompt_guard import (
    GuardedSection,
    build_guarded_payload,
    guard_untrusted_text,
)
from app.services.resume_crud import (
    add_item,
    add_section,
    create_resume_profile,
    get_resume_detail,
)
from app.services.resume_scoring import (
    ResumeItemScorer,
    ScoringResult,
    load_versioned_prompt,
)


class NoTailorableContentError(ValueError):
    """The base resume has no items relevant enough to tailor."""


DEFAULT_RELEVANCE_THRESHOLD = 40

# The deleted bespoke client used a 30s per-item timeout; keep local
# scoring snappy so a wedged Ollama degrades to fallbacks in seconds,
# not minutes, while the request is held open.
SCORING_TIMEOUT_SECONDS = 30.0
UNSCORED_SECTION_TYPES = {SectionType.basics}
MAX_CONCURRENT_SCORING_REQUESTS = 3
GENERATOR_PROMPT_PATH = PROJECT_ROOT / "app" / "prompts" / "resume_generator.txt"
CRITIC_PROMPT_PATH = PROJECT_ROOT / "app" / "prompts" / "resume_critic.txt"

ScoredItems = list[tuple[ResumeItemDetail, ScoringResult | None]]
SectionSelection = tuple[ResumeSectionDetail, ScoredItems, ScoredItems]


class ResumeTailor:
    """Score locally, draft and critique in the cloud, then persist one variant.

    The generator and critic come from configuration ([ai.generator] /
    [ai.critic]); any provider behind the completion protocol can fill
    either role, so swapping the pairing needs no code changes.
    """

    def __init__(
        self,
        *,
        scorer: ResumeItemScorer | None = None,
        generator: CompletionProvider | None = None,
        critic: CompletionProvider | None = None,
        threshold: int = DEFAULT_RELEVANCE_THRESHOLD,
    ) -> None:
        if not 0 <= threshold <= 100:
            raise ValueError("threshold must be between 0 and 100")
        self.scorer = scorer
        self.generator = generator
        self.critic = critic
        self.threshold = threshold
        self.generator_prompt_version, self._generator_instructions = (
            load_versioned_prompt(GENERATOR_PROMPT_PATH)
        )
        self.critic_prompt_version, self._critic_instructions = load_versioned_prompt(
            CRITIC_PROMPT_PATH
        )

    async def tailor_to_job(
        self,
        session: Session,
        *,
        base_resume_id: int,
        job_id: int,
    ) -> ResumeProfile:
        """Run draft, structured critique, and no more than one revision."""
        base = get_resume_detail(session, base_resume_id)
        if base is None:
            raise LookupError(f"Resume profile {base_resume_id} was not found")
        job = session.get(Job, job_id)
        if job is None:
            raise LookupError(f"Job {job_id} was not found")

        self._resolve_providers()
        guarded_job = self._guard_job(job)
        run_started = time.perf_counter()
        scored_sections = await self._score_sections(base.sections, guarded_job)
        selections = [
            (section, scored_items, self._select_items(section, scored_items))
            for section, scored_items in scored_sections
        ]
        if not self._has_tailorable_content(selections):
            raise NoTailorableContentError(
                "no resume items scored at or above the relevance threshold "
                f"({self.threshold}); nothing to tailor for this job"
            )
        candidate_json = self._candidate_json(selections)

        draft_response = await self.generator.complete(
            CompletionRequest(
                system_prompt=self._generator_instructions,
                prompt=self._generator_prompt(candidate_json, guarded_job),
                response_schema=GeneratedResumeDraft.model_json_schema(),
            )
        )
        draft = self._parse_draft(draft_response, selections)
        final_generator_response = draft_response

        critique_response = await self.critic.complete(
            CompletionRequest(
                system_prompt=self._critic_instructions,
                prompt=self._critic_prompt(candidate_json, draft, guarded_job),
                response_schema=ResumeCritique.model_json_schema(),
            )
        )
        critique = self._parse_critique(critique_response)

        final_draft = draft
        if critique.needs_revision:
            revision_response = await self.generator.complete(
                CompletionRequest(
                    system_prompt=self._generator_instructions,
                    prompt=self._revision_prompt(
                        candidate_json, draft, critique, guarded_job
                    ),
                    response_schema=GeneratedResumeDraft.model_json_schema(),
                )
            )
            final_draft = self._parse_draft(revision_response, selections)
            final_generator_response = revision_response

        return self._persist_variant(
            session,
            base_resume_id=base_resume_id,
            job=job,
            selections=selections,
            draft=final_draft,
            critique=critique,
            guarded_job=guarded_job,
            generator_response=final_generator_response,
            critic_response=critique_response,
            run_started=run_started,
        )

    @staticmethod
    def _has_tailorable_content(selections: list[SectionSelection]) -> bool:
        """True when at least one non-basics item survived scoring.

        Without this check an empty candidate set would send the generator
        a request it cannot legally answer (the draft schema requires
        items), burning cloud tokens and blaming a healthy provider.
        """
        return any(
            kept_items
            for section, _scored, kept_items in selections
            if section.section_type not in UNSCORED_SECTION_TYPES
        )

    def _resolve_providers(self) -> None:
        if (
            self.scorer is not None
            and self.generator is not None
            and self.critic is not None
        ):
            return
        config = load_config()
        if self.scorer is None:
            self.scorer = ResumeItemScorer(
                OllamaCompletionProvider(
                    config.ollama,
                    "scorer",
                    timeout_seconds=SCORING_TIMEOUT_SECONDS,
                )
            )
        if self.generator is None:
            self.generator = create_cloud_completion_provider(config.ai, "generator")
        if self.critic is None:
            self.critic = create_cloud_completion_provider(config.ai, "critic")

    @staticmethod
    def _guard_job(job: Job) -> tuple[GuardedSection, GuardedSection]:
        return (
            guard_untrusted_text(job.title or "", label="job_title"),
            guard_untrusted_text(job.description or "", label="job_description"),
        )

    async def _score_sections(
        self,
        sections: list[ResumeSectionDetail],
        guarded_job: tuple[GuardedSection, GuardedSection],
    ) -> list[tuple[ResumeSectionDetail, ScoredItems]]:
        scorable_items = [
            item
            for section in sections
            if section.section_type not in UNSCORED_SECTION_TYPES
            for item in section.items
        ]
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SCORING_REQUESTS)

        async def score(item: ResumeItemDetail) -> ScoringResult:
            async with semaphore:
                return await self.scorer.score_item(
                    item_content=json.dumps(item.content, ensure_ascii=False),
                    guarded_job=guarded_job,
                )

        results = await asyncio.gather(*(score(item) for item in scorable_items))
        results_by_item_id = dict(zip((item.id for item in scorable_items), results))
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
        scored_items: ScoredItems,
    ) -> ScoredItems:
        if section.section_type in UNSCORED_SECTION_TYPES:
            return scored_items
        kept = [
            (item, result)
            for item, result in scored_items
            if result is not None and result.score >= self.threshold
        ]
        kept.sort(key=lambda pair: pair[1].score, reverse=True)
        return kept

    @staticmethod
    def _candidate_json(selections: list[SectionSelection]) -> str:
        sections: list[dict[str, Any]] = []
        for section, scored_items, kept_items in selections:
            if not scored_items:
                continue
            eligible_item_ids = {item.id for item, _result in kept_items}
            sections.append(
                {
                    "section_type": section.section_type,
                    "title": section.title,
                    "items": [
                        {
                            "source_item_id": item.id,
                            "content": item.content,
                            "relevance_score": result.score if result else None,
                            "eligible_for_tailoring": item.id in eligible_item_ids,
                        }
                        for item, result in scored_items
                    ],
                }
            )
        candidate = {"sections": sections}
        return json.dumps(candidate, ensure_ascii=False)

    def _generator_prompt(
        self,
        candidate_json: str,
        guarded_job: tuple[GuardedSection, GuardedSection],
    ) -> str:
        # prompt_guard owns the fence format and the closing trusted
        # reminder; the tailor only supplies the trusted instructions.
        instructions = (
            "Tailor this trusted master resume. Only emit items whose "
            "eligible_for_tailoring value is true.\n"
            f"TRUSTED_RESUME_JSON:{candidate_json}"
        )
        return build_guarded_payload(instructions, guarded_job).render_prompt()

    def _critic_prompt(
        self,
        candidate_json: str,
        draft: GeneratedResumeDraft,
        guarded_job: tuple[GuardedSection, GuardedSection],
    ) -> str:
        # The draft was written under the influence of untrusted job
        # text, so it rides fenced like the job itself — otherwise a
        # hostile listing could smuggle instructions to the critic
        # through generated bullet content.
        guarded_draft = guard_untrusted_text(
            draft.model_dump_json(), label="generator_draft"
        )
        instructions = (
            f"TRUSTED_SOURCE_JSON:{candidate_json}\n"
            "Review the fenced generator_draft against the fenced job. "
            "Return feedback only; do not write replacement resume content."
        )
        return build_guarded_payload(
            instructions, (guarded_draft, *guarded_job)
        ).render_prompt()

    def _revision_prompt(
        self,
        candidate_json: str,
        draft: GeneratedResumeDraft,
        critique: ResumeCritique,
        guarded_job: tuple[GuardedSection, GuardedSection],
    ) -> str:
        # The critique rides as its own fenced untrusted section: the
        # critic consumed untrusted job text, so its output is untrusted.
        guarded_critique = guard_untrusted_text(
            critique.model_dump_json(),
            label="critic_feedback",
        )
        guarded_draft = guard_untrusted_text(
            draft.model_dump_json(), label="previous_draft"
        )
        instructions = (
            f"TRUSTED_SOURCE_JSON:{candidate_json}\n"
            "Revise the fenced previous_draft once, following the fenced "
            "critic_feedback. Use only source evidence and preserve "
            "source_item_id values."
        )
        return build_guarded_payload(
            instructions, (guarded_draft, guarded_critique, *guarded_job)
        ).render_prompt()

    def _parse_draft(
        self,
        response: CompletionResponse,
        selections: list[SectionSelection],
    ) -> GeneratedResumeDraft:
        try:
            draft = GeneratedResumeDraft.model_validate_json(response.text)
            self._validate_draft_sources(draft, selections)
        except (ValidationError, ValueError) as error:
            raise AIResponseError(
                f"Generator returned an invalid resume draft: {error}",
                provider=response.provider,
                model=response.model,
            ) from error
        return draft

    @staticmethod
    def _parse_critique(response: CompletionResponse) -> ResumeCritique:
        try:
            return ResumeCritique.model_validate_json(response.text)
        except (ValidationError, ValueError) as error:
            raise AIResponseError(
                f"Critic returned invalid structured feedback: {error}",
                provider=response.provider,
                model=response.model,
            ) from error

    @staticmethod
    def _validate_draft_sources(
        draft: GeneratedResumeDraft,
        selections: list[SectionSelection],
    ) -> None:
        allowed: dict[int, tuple[SectionType, dict[str, Any]]] = {}
        required_basics: set[int] = set()
        for section, _scored_items, kept_items in selections:
            for item, _result in kept_items:
                allowed[item.id] = (section.section_type, item.content)
                if section.section_type == SectionType.basics:
                    required_basics.add(item.id)

        # Duplicate section types are allowed: a master resume may hold
        # e.g. two experience sections, and drafts mirror that shape.
        seen: set[int] = set()
        for section in draft.sections:
            for item in section.items:
                if item.source_item_id in seen:
                    raise ValueError(f"duplicate source_item_id {item.source_item_id}")
                source = allowed.get(item.source_item_id)
                if source is None or source[0] != section.section_type:
                    raise ValueError(
                        f"source_item_id {item.source_item_id} is not allowed in "
                        f"{section.section_type}"
                    )
                if (
                    section.section_type == SectionType.basics
                    and item.content_dict() != source[1]
                ):
                    raise ValueError("generator modified a basics/contact item")
                seen.add(item.source_item_id)
        if not required_basics.issubset(seen):
            raise ValueError("generator omitted required basics/contact items")

    def _persist_variant(
        self,
        session: Session,
        *,
        base_resume_id: int,
        job: Job,
        selections: list[SectionSelection],
        draft: GeneratedResumeDraft,
        critique: ResumeCritique,
        guarded_job: tuple[GuardedSection, GuardedSection],
        generator_response: CompletionResponse,
        critic_response: CompletionResponse,
        run_started: float,
    ) -> ResumeProfile:
        source_items = {
            item.id: (item, result)
            for _section, _scored_items, kept_items in selections
            for item, result in kept_items
        }
        final_item_ids = {
            item.source_item_id for section in draft.sections for item in section.items
        }
        try:
            variant = create_resume_profile(
                session,
                name=self._variant_name(job),
                base_resume_id=base_resume_id,
                job_id=job.id,
            )
            for section_idx, generated_section in enumerate(draft.sections):
                new_section = add_section(
                    session,
                    profile_id=variant.id,
                    section_type=generated_section.section_type,
                    title=generated_section.title,
                    order_idx=section_idx,
                )
                for item_idx, generated_item in enumerate(generated_section.items):
                    _source_item, result = source_items[generated_item.source_item_id]
                    add_item(
                        session,
                        section_id=new_section.id,
                        content=generated_item.content_dict(),
                        order_idx=item_idx,
                        relevance_score=result.score if result else None,
                        score_reasoning=result.reasoning if result else None,
                        score_is_fallback=result.is_fallback if result else False,
                    )

            run = ResumeTailorRun(
                source_profile_id=base_resume_id,
                output_profile_id=variant.id,
                job_id=job.id,
                model=self.scorer.model_name,
                prompt_version=self.scorer.prompt_version,
                generator_provider=generator_response.provider,
                generator_model=generator_response.model,
                critic_provider=critic_response.provider,
                critic_model=critic_response.model,
                generator_prompt_version=self.generator_prompt_version,
                critic_prompt_version=self.critic_prompt_version,
                critique_summary=critique.audit_summary(),
                guard_diagnostics=self._guard_diagnostics(guarded_job),
                duration_ms=0,
            )
            session.add(run)
            session.flush()
            self._record_run_items(session, run, selections, final_item_ids)
            run.duration_ms = int((time.perf_counter() - run_started) * 1000)
            session.add(run)
            session.commit()
        except Exception:
            session.rollback()
            raise

        session.refresh(variant)
        return variant

    @staticmethod
    def _record_run_items(
        session: Session,
        run: ResumeTailorRun,
        selections: list[SectionSelection],
        final_item_ids: set[int],
    ) -> None:
        for section, scored_items, _kept_items in selections:
            if section.section_type in UNSCORED_SECTION_TYPES:
                continue
            for item, result in scored_items:
                if result is None:
                    continue
                session.add(
                    ResumeTailorRunItem(
                        run_id=run.id,
                        section_type=section.section_type,
                        item_content=json.dumps(item.content, ensure_ascii=False),
                        score=result.score,
                        reasoning=result.reasoning,
                        is_fallback=result.is_fallback,
                        kept=item.id in final_item_ids,
                    )
                )

    @staticmethod
    def _guard_diagnostics(
        guarded_job: tuple[GuardedSection, GuardedSection],
    ) -> str:
        diagnostics: dict[str, Any] = {
            "sections": [
                {
                    "label": section.label,
                    "truncated": section.truncated,
                    "original_length": section.original_length,
                    "flags": [flag.model_dump() for flag in section.flags],
                }
                for section in guarded_job
            ]
        }
        return json.dumps(diagnostics, ensure_ascii=False)

    @staticmethod
    def _variant_name(job: Job) -> str:
        job_slug = job.title.lower().replace(" ", "-")[:40]
        return f"tailored-{job_slug}-{datetime.now():%Y%m%d-%H%M%S}"
