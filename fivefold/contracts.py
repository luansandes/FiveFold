from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stage(StrEnum):
    RESEARCHER = "researcher"
    DESIGNER = "designer"
    MAKER = "maker"
    COMMUNICATOR = "communicator"
    MANAGER = "manager"


STAGE_ORDER = [
    Stage.RESEARCHER,
    Stage.DESIGNER,
    Stage.MAKER,
    Stage.COMMUNICATOR,
    Stage.MANAGER,
]


class DecisionKind(StrEnum):
    ADVANCE = "advance"
    REVISE_PREVIOUS = "revise_previous"
    REVISE_STAGE = "revise_stage"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    REJECT = "reject"


class HandoffDecision(StrictModel):
    action: DecisionKind = DecisionKind.ADVANCE
    destination: Stage | None = None
    reason: str = "Artefact satisfies the stage contract."


class SourceReference(StrictModel):
    label: str
    url: str
    source_type: str = "public"
    checked_at: datetime
    attribution: str | None = None


class UsageRecord(StrictModel):
    model: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_eur: float = 0.0


class WebsiteAudit(StrictModel):
    reachable: bool = False
    https: bool = False
    mobile_meta: bool = False
    clear_cta: bool = False
    contact_visible: bool = False
    lead_form: bool = False
    findings: list[str] = Field(default_factory=list)
    score: int = Field(default=0, ge=0, le=100)


class DomainCandidate(StrictModel):
    domain: str
    available: bool | None = None
    estimated_annual_cost_eur: float = 23.99
    provider: str = "E-Live reference price"
    checked_at: datetime
    note: str = "Estimate only. No reservation or purchase was performed."


class ResearchBrief(StrictModel):
    prospect_name: str
    category: str
    location: str = "Dublin, Ireland"
    place_id: str | None = None
    footprint: Literal["absent", "social_only", "weak", "adequate"]
    qualification_reason: str
    website_url: str | None = None
    contact_status: Literal["unverified", "independently_verified"] = "unverified"
    contact_summary: str = "Human verification required before outreach."
    website_audit: WebsiteAudit
    review_themes: list[str] = Field(default_factory=list)
    domain_candidates: list[DomainCandidate] = Field(default_factory=list)
    evidence: list[SourceReference] = Field(default_factory=list)
    opportunity_score: int = Field(ge=0, le=100)


class PageSection(StrictModel):
    section_type: str
    heading: str
    purpose: str
    content_points: list[str]


class VisualPalette(StrictModel):
    primary: str
    secondary: str
    accent: str
    background: str
    text: str


class TypographyTokens(StrictModel):
    heading_family: str
    body_family: str
    base_size_px: int = Field(ge=14, le=22)
    line_height: float = Field(ge=1.2, le=2.0)


class DesignSpecification(StrictModel):
    concept_name: str
    audience: str
    primary_goal: str
    user_journey: list[str]
    sections: list[PageSection]
    palette: VisualPalette
    typography: TypographyTokens
    primary_cta: str
    trust_strategy: list[str]
    accessibility_requirements: list[str]
    mobile_behaviour: list[str]
    testimonial_rule: str = "Placeholders only until the business approves supplied testimonials."
    inherited_research_version: int = 1


class ValidationChecks(StrictModel):
    no_scripts: bool
    no_iframes: bool
    no_active_forms: bool
    has_main: bool
    has_heading: bool
    has_viewport: bool
    responsive_css: bool
    no_javascript_urls: bool


class ValidationReport(StrictModel):
    passed: bool
    checks: ValidationChecks
    warnings: list[str] = Field(default_factory=list)


class LocalBusinessStructuredData(StrictModel):
    schema_context: Literal["https://schema.org"] = "https://schema.org"
    schema_type: Literal["LocalBusiness"] = "LocalBusiness"
    name: str
    description: str
    area_served: str
    address: str | None = None
    telephone: str | None = None
    url: str | None = None


class WebsiteArtifact(StrictModel):
    title: str
    html: str
    css: str
    meta_description: str
    structured_data: LocalBusinessStructuredData
    content_manifest: list[str]
    validation: ValidationReport
    artefact_hash: str
    inherited_design_version: int = 1
    preview_path: str | None = None


class ServiceOffer(StrictModel):
    monthly_eur: float = 14.99
    annual_eur: float = 149.99
    three_year_eur: float = 439.99
    vat_included: bool = False
    monthly_commitment: str = "Cancel anytime"
    includes: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)


class ObjectionResponse(StrictModel):
    objection: str
    response: str


class CommunicationPlan(StrictModel):
    value_proposition: str
    email_subject: str
    email_draft: str
    call_outline: list[str]
    follow_up_cadence: list[str]
    objections: list[ObjectionResponse]
    preview_url: str
    offer: ServiceOffer
    human_action_notice: str = "A human must verify contact details and send every message."
    inherited_website_version: int = 1


class ProfitabilityEstimate(StrictModel):
    annual_revenue_eur: float
    estimated_annual_cost_eur: float
    contribution_eur: float
    gross_margin_percent: float
    early_cancellation_risk_eur: float
    assumptions: list[str]


class QualityScores(StrictModel):
    evidence_fidelity: int = Field(ge=0, le=100)
    upstream_inheritance: int = Field(ge=0, le=100)
    design_specificity: int = Field(ge=0, le=100)
    preview_quality: int = Field(ge=0, le=100)
    offer_consistency: int = Field(ge=0, le=100)
    risk_compliance: int = Field(ge=0, le=100)


class ManagerDecision(StrictModel):
    disposition: Literal["accept", "reject", "human_review", "revise"]
    priority: Literal["high", "medium", "low"]
    quality_scores: QualityScores
    profitability: ProfitabilityEstimate
    executive_summary: str
    next_human_action: str
    corrections: list[str] = Field(default_factory=list)
    inherited_communication_version: int = 1


class BaseEnvelope(StrictModel):
    confidence: float = Field(ge=0, le=1)
    facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    placeholders: list[str] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: UsageRecord = Field(default_factory=UsageRecord)
    handoff: HandoffDecision = Field(default_factory=HandoffDecision)


class ResearchEnvelope(BaseEnvelope):
    artifact: ResearchBrief


class DesignEnvelope(BaseEnvelope):
    artifact: DesignSpecification


class MakerEnvelope(BaseEnvelope):
    artifact: WebsiteArtifact


class CommunicationEnvelope(BaseEnvelope):
    artifact: CommunicationPlan


class ManagerEnvelope(BaseEnvelope):
    artifact: ManagerDecision


Envelope = ResearchEnvelope | DesignEnvelope | MakerEnvelope | CommunicationEnvelope | ManagerEnvelope


class ResearchRunRequest(StrictModel):
    location: str = "Dublin, Ireland"
    categories: list[str] = Field(default_factory=lambda: ["plumbers"])


class HumanStatusRequest(StrictModel):
    status: Literal["unverified", "verified", "contacted", "replied", "won", "lost"]
    note: str = ""


class AgentDefinition(BaseModel):
    stage: Stage
    name: str
    archetype: str
    personality: str
    expertise: list[str]
    instructions: str
    output_model: type[BaseModel]

    model_config = {"arbitrary_types_allowed": True}
