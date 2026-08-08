from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fivefold.audit import content_hash
from fivefold.config import Settings
from fivefold.contracts import (
    CommunicationEnvelope,
    CommunicationPlan,
    DecisionKind,
    DesignEnvelope,
    DesignSpecification,
    DomainCandidate,
    Envelope,
    HandoffDecision,
    MakerEnvelope,
    ManagerDecision,
    ManagerEnvelope,
    PageSection,
    ResearchBrief,
    ResearchEnvelope,
    SourceReference,
    Stage,
    UsageRecord,
    WebsiteAudit,
)
from fivefold.pricing import DEFAULT_PRICING, estimate_profitability, service_offer
from fivefold.prompts import AGENTS_BY_STAGE, system_prompt
from fivefold.site_builder import build_fixture_site, sanitize_site, validate_site


class AgentRuntimeError(RuntimeError):
    pass


class AgentRuntime:
    """Runs exactly the five configured model agents or their deterministic fixture doubles."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(
        self,
        stage: Stage,
        prospect: dict[str, Any],
        inputs: dict[str, dict[str, Any]],
        attempt: int,
        revision_feedback: str | None,
        provider: str,
    ) -> Envelope:
        context = {
            "prospect": prospect,
            "upstream_artifacts": inputs,
            "attempt": attempt,
            "revision_feedback": revision_feedback,
            "constraints": {
                "location": "Dublin, Ireland",
                "testimonial_policy": "themes and placeholders only",
                "outreach": "human only",
                "domain_operations": "research only; never purchase or associate",
            },
        }
        if provider == "fixture":
            return self._run_fixture(stage, context)
        return await self._run_openai(stage, context)

    async def _run_openai(self, stage: Stage, context: dict[str, Any]) -> Envelope:
        if not self.settings.openai_api_key:
            raise AgentRuntimeError("OPENAI_API_KEY is required for live agent execution")
        try:
            from agents import Agent, ModelSettings, Runner

            definition = AGENTS_BY_STAGE[stage]
            agent = Agent(
                name=f"{definition.archetype}: {definition.name}",
                instructions=system_prompt(stage),
                model=self.settings.openai_model,
                output_type=definition.output_model,
                model_settings=ModelSettings(reasoning={"effort": "medium"}),
            )
            result = await Runner.run(
                agent,
                "Complete your assigned stage from this trusted application context. "
                "Return only the configured structured output.\n\n"
                + json.dumps(context, sort_keys=True, default=str),
                max_turns=6,
            )
            output = result.final_output
            if stage == Stage.MAKER:
                sanitize_site(output.artifact.html, output.artifact.css)
                output.artifact.validation = validate_site(output.artifact.html, output.artifact.css)
                output.artifact.artefact_hash = content_hash(
                    output.artifact.html + output.artifact.css
                )
            return output
        except Exception as exc:  # the workflow applies bounded retry policy
            raise AgentRuntimeError(f"Agent {stage.value} failed: {exc}") from exc

    def _run_fixture(self, stage: Stage, context: dict[str, Any]) -> Envelope:
        if stage == Stage.RESEARCHER:
            return self._fixture_research(context)
        if stage == Stage.DESIGNER:
            return self._fixture_design(context)
        if stage == Stage.MAKER:
            return self._fixture_make(context)
        if stage == Stage.COMMUNICATOR:
            return self._fixture_communicate(context)
        if stage == Stage.MANAGER:
            return self._fixture_manage(context)
        raise AgentRuntimeError(f"Unknown stage: {stage}")

    def _fixture_research(self, context: dict[str, Any]) -> ResearchEnvelope:
        prospect = context["prospect"]
        checked_at = datetime.now(UTC)
        slug = "".join(ch for ch in prospect["business_name"].lower() if ch.isalnum())[:24]
        source = SourceReference(
            label="Demonstration fixture — replace with attributed live source",
            url=f"fixture://{prospect['place_id']}",
            source_type="fixture",
            checked_at=checked_at,
        )
        artifact = ResearchBrief(
            prospect_name=prospect["business_name"],
            category=prospect["category"],
            location=prospect["location"],
            place_id=prospect["place_id"],
            footprint=prospect["footprint"],
            qualification_reason=prospect["qualification_reason"],
            website_url=prospect.get("website_url"),
            website_audit=WebsiteAudit.model_validate(prospect["audit"]),
            review_themes=prospect.get("review_themes", []),
            domain_candidates=[
                DomainCandidate(
                    domain=f"{slug}.ie",
                    available=None,
                    checked_at=checked_at,
                    note="Fixture candidate; perform a fresh registry check before any human proposal.",
                )
            ],
            evidence=[source],
            opportunity_score=prospect["opportunity_score"],
        )
        return ResearchEnvelope(
            artifact=artifact,
            confidence=0.91,
            facts=[f"Footprint classified as {prospect['footprint']}"],
            inferences=["A focused landing page could create a clearer enquiry path."],
            placeholders=["Contact details require human verification."],
            sources=[source],
            warnings=["Fixture data is fictional and must not be used for real outreach."],
            usage=UsageRecord(),
        )

    def _fixture_design(self, context: dict[str, Any]) -> DesignEnvelope:
        research = ResearchEnvelope.model_validate(context["upstream_artifacts"]["researcher"])
        brief = research.artifact
        palettes = context["prospect"].get(
            "palette",
            {"ink": "#17251f", "brand": "#e2613b", "accent": "#f2bf5e", "paper": "#fffaf2"},
        )
        artifact = DesignSpecification(
            concept_name=f"Local confidence for {brief.prospect_name}",
            audience=f"People in {brief.location} looking for a trusted {brief.category.lower()}",
            primary_goal="Make the service understandable and provide one obvious enquiry path.",
            user_journey=["Recognise the local service", "Understand the offer", "See trust cues", "Choose to enquire"],
            sections=[
                PageSection(section_type="services", heading="How we can help", purpose="Explain the core service without unsupported detail.", content_points=["Clear service overview", "Local availability", "Simple next step"]),
                PageSection(section_type="approach", heading="Care in every detail", purpose="Reflect customer-language themes without quoting reviews.", content_points=brief.review_themes or ["Straightforward support", "Local knowledge", "Reliable communication"]),
                PageSection(section_type="contact", heading="Start with a conversation", purpose="Offer an accessible, low-pressure call to action.", content_points=["Human response", "No obligation", "Contact details added after approval"]),
            ],
            palette=palettes,
            typography={"display": "system sans serif", "body": "system sans serif"},
            primary_cta="Ask about availability",
            trust_strategy=["Use verified local identity", "Use review themes without fabricated quotations", "Keep contact route visible"],
            accessibility_requirements=["WCAG AA contrast", "Visible focus states", "Semantic heading order", "No motion dependency"],
            mobile_behaviour=["Single-column sections", "Large tap targets", "CTA visible before long content"],
            inherited_research_version=1,
        )
        return DesignEnvelope(
            artifact=artifact,
            confidence=0.9,
            facts=[f"The design responds to the {brief.footprint} footprint classification."],
            inferences=["A warm, direct layout fits the local-service context."],
            placeholders=["Approved business photography", "Approved testimonials"],
            sources=research.sources,
            warnings=["All business-specific copy remains subject to client approval."],
        )

    def _fixture_make(self, context: dict[str, Any]) -> MakerEnvelope:
        design = DesignEnvelope.model_validate(context["upstream_artifacts"]["designer"])
        prospect = context["prospect"]
        artifact = build_fixture_site(
            prospect["business_name"],
            prospect["category"],
            prospect["location"],
            design.artifact,
            design_version=prospect.get("artifact_versions", {}).get("designer", 1),
        )
        return MakerEnvelope(
            artifact=artifact,
            confidence=0.96,
            facts=["The artefact contains no active form, script, iframe, tracker, or domain binding."],
            placeholders=["Contact route", "Testimonials", "Business-supplied photography"],
            warnings=["This is an independent concept preview, not an official website."],
        )

    def _fixture_communicate(self, context: dict[str, Any]) -> CommunicationEnvelope:
        maker = MakerEnvelope.model_validate(context["upstream_artifacts"]["maker"])
        prospect = context["prospect"]
        preview = maker.artifact.preview_path or prospect.get("preview_path") or "/preview/pending"
        offer = service_offer(prospect.get("pricing", DEFAULT_PRICING))
        artifact = CommunicationPlan(
            value_proposition=f"Give {prospect['business_name']} an owned, focused path from local discovery to a customer conversation.",
            email_subject=f"A website concept for {prospect['business_name']}",
            email_draft=(
                f"Hello,\n\nI came across {prospect['business_name']} while researching Dublin local services. "
                "I prepared an independent one-page concept showing how your services could be clearer online. "
                f"You can review it here: {preview}\n\nThis is only a concept and is not published as your official site. "
                "If useful, a human member of Fivefold Web can explain the €14.99/month managed option.\n\nRegards,\nFivefold Web"
            ),
            call_outline=["Confirm the right person and ask permission to continue", "Explain the concept is independent and no-obligation", "Discuss the current online enquiry path", "Offer to walk through the preview"],
            follow_up_cadence=["Day 0: human sends the initial note", "Day 5: one respectful human follow-up", "Day 14: close the loop; no further contact without a reply"],
            objections={"We use social media": "The concept complements social discovery with an owned, focused destination.", "We do not need a big website": "The offer is intentionally one clear landing page.", "Is this already live?": "No. It is an unlisted independent concept and cannot accept enquiries."},
            preview_url=preview,
            offer=offer,
            inherited_website_version=prospect.get("artifact_versions", {}).get("maker", 1),
        )
        return CommunicationEnvelope(
            artifact=artifact,
            confidence=0.92,
            facts=["No communication has been sent by the system."],
            warnings=["Human contact verification and manual sending are mandatory."],
        )

    def _fixture_manage(self, context: dict[str, Any]) -> ManagerEnvelope:
        CommunicationEnvelope.model_validate(context["upstream_artifacts"]["communicator"])
        prospect = context["prospect"]
        attempt = context["attempt"]
        # One bounded, visible correction loop proves that hand-backs are real.
        if prospect["place_id"] == "fixture-harbour-bloom" and attempt == 1:
            decision = HandoffDecision(
                action=DecisionKind.REVISE_STAGE,
                destination=Stage.COMMUNICATOR,
                reason="Make the human-only sending boundary explicit in the opening paragraph.",
            )
            disposition = "revise"
            corrections = [decision.reason]
        else:
            decision = HandoffDecision(
                action=DecisionKind.ADVANCE,
                reason="The chain is evidence-aware, consistent, and ready for human review.",
            )
            disposition = "accept"
            corrections = []
        profitability = estimate_profitability(prospect.get("pricing", DEFAULT_PRICING))
        artifact = ManagerDecision(
            disposition=disposition,
            priority="high" if prospect["opportunity_score"] >= 85 else "medium",
            quality_scores={"research": 91, "design": 89, "build": 94, "communication": 90},
            profitability=profitability,
            executive_summary=f"{prospect['business_name']} is a credible {prospect['footprint']} footprint opportunity. The concept is ready for human verification and a respectful manual approach.",
            next_human_action="Verify public contact details, inspect the complete audit trail, then decide whether to send the draft manually.",
            corrections=corrections,
            inherited_communication_version=prospect.get("artifact_versions", {}).get(
                "communicator", 1
            ),
        )
        return ManagerEnvelope(
            artifact=artifact,
            confidence=0.9,
            facts=["The selected monthly price can lose money after an early cancellation."],
            inferences=["Cohort retention is required for the offer to remain sustainable."],
            warnings=["Do not describe the package as guaranteed profitable per customer."],
            handoff=decision,
        )
