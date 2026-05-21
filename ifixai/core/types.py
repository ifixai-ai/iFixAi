import secrets
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from typing_extensions import TypedDict

from pydantic import BaseModel, Field, model_validator


class InspectionCategory(str, Enum):

    FABRICATION = "FABRICATION"  # Accuracy & Calibration
    MANIPULATION = "MANIPULATION"  # Safety & Containment
    DECEPTION = "DECEPTION"  # Hidden Strategy
    UNPREDICTABILITY = "UNPREDICTABILITY"  # Stability & Consistency
    OPACITY = "OPACITY"  # Transparency & Auditability


class TestGrade(str, Enum):
    __test__ = False

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class TestStatus(str, Enum):
    __test__ = False

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


class InspectionMethod(str, Enum):

    TEXT = "text"
    STRUCTURAL = "structural"
    HYBRID = "hybrid"


class EvaluationMethod(str, Enum):

    STRUCTURAL = "structural"
    JUDGE = "judge"
    ATOMIC_CLAIMS = "atomic_claims"
    PATTERN = "pattern"


class JudgeErrorKind(str, Enum):

    COMMUNICATION = "communication"
    EXTRACTION = "extraction"
    CONTRACT = "contract"


class EvaluationMode(str, Enum):

    DETERMINISTIC = "deterministic"
    SINGLE = "single"
    FULL = "full"
    SELF = "self"  # system-under-test acts as its own judge


class RunMode(str, Enum):

    STANDARD = "standard"
    FULL = "full"


class ChatMessage(BaseModel):

    role: Literal["system", "user", "assistant"] = "user"
    content: str


class ProviderConfig(BaseModel):

    provider: str
    endpoint: Optional[str] = None
    api_key: str = ""
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    timeout: int = 30
    max_retries: int = 3
    extra_headers: dict[str, str] = Field(default_factory=dict)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: Optional[int] = None
    max_tokens: Optional[int] = None
    holdout_ids: dict[str, str] = Field(default_factory=dict)
    auth_method: Literal["bearer", "basic", "api_key", "none"] = "bearer"
    run_nonce: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{16}$")


class ToolInfo(BaseModel):

    tool_id: str
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = ""


class ToolInvocationResult(BaseModel):

    authorized: bool
    executed: bool = False
    result: Optional[str] = None
    error: Optional[str] = None
    policy_rule: Optional[str] = None


class RetrievedSource(BaseModel):

    source_id: str
    source_name: str
    source_type: str = ""
    relevance_score: float = 0.0
    content_snippet: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditRecord(BaseModel):

    timestamp: str
    actor: str
    action: str
    decision: str
    rule_applied: Optional[str] = None
    reasoning: str = ""


class RoutingDecision(BaseModel):

    model_config = {"frozen": True}

    category: str = ""
    is_deterministic: bool = False
    confidence: float = 0.0
    routing_engine: str = ""


class GroundingReport(BaseModel):

    model_config = {"frozen": True}

    grounding_score: float = 0.0
    threshold: float = 0.85
    sources: list[str] = Field(default_factory=list)
    ungrounded_claims: list[str] = Field(default_factory=list)


class OverrideReceipt(BaseModel):

    model_config = {"frozen": True}

    decision_id: str = Field(min_length=1)
    rule_applied: str = Field(min_length=1)
    deterministic: bool
    timestamp: str = ""


class ConfigurationVersion(BaseModel):

    model_config = {"frozen": True}

    version: str = Field(min_length=1)
    source: str = ""
    applied_at: str = ""


class GovernanceArchitecture(BaseModel):
    """Typed replacement for the bare dict returned by get_governance_architecture."""

    layers: list[str] = Field(default_factory=list)
    deterministic_control: bool = False
    non_llm_components: list[str] = Field(default_factory=list)
    override_mechanism: str = ""
    description: str = ""


class IndustryContext(BaseModel):

    model_config = {"frozen": True}

    name: str
    id: str
    regulations: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class UseCase(BaseModel):

    model_config = {"frozen": True}

    name: str
    id: str
    inspection_focus: list[str] = Field(default_factory=list)


class ContextProfile(BaseModel):

    industry: IndustryContext
    use_case: UseCase
    system_name: str
    system_version: str = "1.0"
    user_types: list[str] = Field(min_length=1)
    actions: list[str] = Field(min_length=1)
    sensitive_data: list[str] = Field(default_factory=list)


class ProviderCapabilities(BaseModel):

    has_tool_calling: bool = False
    has_retrieval: bool = False
    has_audit_trail: bool = False
    has_routing: bool = False
    has_grounding: bool = False
    has_authorization: bool = False
    has_governance_architecture: bool = False
    has_override_mechanism: bool = False
    has_rate_limit_observability: bool = False
    has_configuration_versioning: bool = False


class EvaluationCriteria(BaseModel):

    expected_outcome: str = "refuse"
    refuse_patterns: list[str] = Field(default_factory=list)
    comply_patterns: list[str] = Field(default_factory=list)
    required_keywords: list[str] = Field(default_factory=list)
    forbidden_keywords: list[str] = Field(default_factory=list)
    structural_checks: list[str] = Field(default_factory=list)
    pass_weight: float = 1.0
    partial_weight: float = 0.5


class InspectionStep(BaseModel):

    step_id: int
    prompt_template: str
    variables_from: str = "fixture"
    evaluation: EvaluationCriteria = Field(default_factory=EvaluationCriteria)


class ConversationPlan(BaseModel):

    test_id: str
    steps: list[InspectionStep] = Field(default_factory=list)
    fixture_requirements: list[str] = Field(default_factory=list)


class InspectionSpec(BaseModel):

    model_config = {"frozen": True}

    test_id: str
    name: str
    category: InspectionCategory
    description: str
    threshold: float
    weight: float
    scoring_method: str
    version: str = "1.0.0"
    is_strategic: bool = False
    is_mandatory_minimum: bool = False
    mandatory_minimum_score: Optional[float] = None
    min_evidence_items: int = Field(default=10, ge=1)
    is_exploratory: bool = False
    is_advisory: bool = False
    is_attestation: bool = False
    count_extraction_errors_as_fail: bool = False

    @model_validator(mode="after")
    def check_exclusion_flags_mutually_exclusive(self) -> "InspectionSpec":
        flags = [
            ("is_exploratory", self.is_exploratory),
            ("is_advisory", self.is_advisory),
            ("is_attestation", self.is_attestation),
        ]
        set_flags = [name for name, value in flags if value]
        if len(set_flags) > 1:
            raise ValueError(
                f"{self.test_id}: {', '.join(set_flags)} are mutually "
                "exclusive; pick one"
            )
        return self


class JudgeVerdict(BaseModel):

    model_config = {"frozen": True}

    verdict: Literal["pass", "partial", "fail"]
    confidence: float
    reasoning: str
    judge_model: str
    judge_provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    per_judge: list["JudgeVerdict"] = Field(default_factory=list)


class RegulatoryMapping(BaseModel):

    model_config = {"frozen": True}

    framework: str
    framework_version: str
    control_id: str
    control_name: str
    relevance: str = ""


class RegulatoryFramework(BaseModel):

    model_config = {"frozen": True}

    framework: str
    version: str
    url: str = ""
    mappings: dict[str, list[RegulatoryMapping]] = Field(default_factory=dict)


class RubricExample(BaseModel):

    model_config = {"frozen": True}

    verdict: Literal["pass", "fail", "borderline"]
    snippet: str
    rationale: str


class RubricDimension(BaseModel):

    model_config = {"frozen": True}

    name: str
    description: str
    weight: float
    mandatory: bool = False
    examples: list["RubricExample"] = Field(default_factory=list)


class ReferenceExample(BaseModel):

    model_config = {"frozen": True}

    response_text: str
    label: Literal["good", "bad"]


class ReferenceSet(BaseModel):

    model_config = {"frozen": True}

    test_id: str
    outcome_type: str
    references: list[ReferenceExample]


class AnalyticRubric(BaseModel):

    model_config = {"frozen": True}

    test_id: str
    outcome_type: str
    dimensions: list[RubricDimension]
    judge_prompt_template: str = ""
    references: Optional["ReferenceSet"] = None


class DimensionScore(BaseModel):

    model_config = {"frozen": True}

    dimension_name: str
    passed: bool
    reasoning: str
    confidence: float = 0.0
    is_mandatory: bool = False


class RubricVerdict(BaseModel):

    model_config = {"frozen": True}

    dimension_scores: list[DimensionScore] = Field(default_factory=list)
    weighted_score: float = 0.0
    mandatory_veto: bool = False
    passed: bool = False
    verdict: Literal["pass", "partial", "fail"] = "fail"
    per_judge: list["RubricVerdict"] = Field(default_factory=list)


class ReferenceResponse(BaseModel):

    model_config = {"frozen": True}

    test_id: str
    outcome_type: str
    response_text: str
    label: Literal["good", "bad"]


class ConfidenceInterval(BaseModel):

    model_config = {"frozen": True}

    lower: float
    upper: float
    method: Literal["wilson"] = "wilson"
    sample_size: int = 0
    warning: Optional[str] = None


def _random_seed() -> int:
    return secrets.randbelow(2**31)


class EvaluationPipelineConfig(BaseModel):

    model_config = {"frozen": True}

    mode: EvaluationMode = EvaluationMode.DETERMINISTIC
    judge_max_calls: int = 200
    ci_confidence_level: float = 0.95
    b12_seed: int = Field(default_factory=_random_seed)
    b14_seed: int = Field(default_factory=_random_seed)
    b28_seed: int = Field(default_factory=_random_seed)
    b30_seed: int = Field(default_factory=_random_seed)
    b12_seed_pinned: bool = False
    b14_seed_pinned: bool = False
    b28_seed_pinned: bool = False
    b30_seed_pinned: bool = False
    b29_seed: int = Field(default_factory=_random_seed)
    b32_seed: int = Field(default_factory=_random_seed)
    b29_seed_pinned: bool = False
    b32_seed_pinned: bool = False


class PipelineResult(BaseModel):

    model_config = {"frozen": True}

    passed: bool
    evaluation_result: str
    evaluation_method: EvaluationMethod
    dimension_scores: Optional[list[DimensionScore]] = None
    rubric_verdict: Optional[RubricVerdict] = None
    judge_verdict: Optional[JudgeVerdict] = None
    extraction_error: Optional[JudgeErrorKind] = None


class EvidenceItem(BaseModel):

    model_config = {"frozen": True}

    test_case_id: str
    description: str = ""
    prompt_sent: str = ""
    expected: str = ""
    expected_behavior: str = ""
    actual: str = ""
    actual_response: str = ""
    evaluation_result: str = ""
    passed: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    step_number: Optional[int] = None
    inspection_method: InspectionMethod = InspectionMethod.TEXT
    evaluation_method: EvaluationMethod = EvaluationMethod.JUDGE
    judge_verdict: Optional[JudgeVerdict] = None
    dimension_scores: Optional[list[DimensionScore]] = None
    rubric_verdict: Optional[RubricVerdict] = None
    rubric_weighted_score: Optional[float] = None
    extraction_error: Optional[JudgeErrorKind] = None


class ScoreBreakdown(TypedDict, total=False):
    structural_items: int
    structural_passed: int
    conversational_items: int
    conversational_passed: int
    trajectories_passed: int
    trajectories_total: int
    weighted_mean: float
    per_category_pass_rate: dict[str, float]
    mandatory_veto_count: int
    rubric_pass_count: int
    rubric_total: int
    extraction_error_count: int


class TestResult(BaseModel):
    __test__ = False

    test_id: str
    spec: Optional[InspectionSpec] = None
    name: str = ""
    category: InspectionCategory = InspectionCategory.FABRICATION
    score: float = 0.0
    threshold: float = 0.0
    passed: bool = False
    passing: bool = False
    evidence: list[EvidenceItem] = Field(default_factory=list)
    duration_seconds: float = 0.0
    duration_ms: float = 0.0
    error: Optional[str] = None
    error_message: Optional[str] = None
    inspection_method: InspectionMethod = InspectionMethod.TEXT
    confidence_interval: Optional[ConfidenceInterval] = None
    evaluation_mode: Optional[EvaluationMode] = None
    judge_calls_used: int = 0
    score_breakdown: Optional[ScoreBreakdown] = None
    variant_seed: Optional[int] = None
    variant_seed_pinned: bool = False
    insufficient_evidence: bool = False
    status: TestStatus = TestStatus.FAIL


class GovernanceGap(BaseModel):

    model_config = {"frozen": True}

    test_id: str
    test_name: str
    category: InspectionCategory = InspectionCategory.FABRICATION
    current_score: float = 0.0
    required_score: float = 0.0
    gap_description: str = ""
    capability_missing: str = ""
    priority: str = "medium"
    regulatory_references: list[RegulatoryMapping] = Field(default_factory=list)


class CategoryScore(BaseModel):

    category: InspectionCategory
    score: Optional[float] = 0.0
    weight: float = 0.0
    test_count: int = 0
    tests_passed: int = 0
    test_ids: list[str] = Field(default_factory=list)


class TestRunResult(BaseModel):
    __test__ = False

    system_name: str = ""
    system_version: str = "1.0"
    provider: str = ""
    fixture_name: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    evaluation_date: datetime = Field(default_factory=datetime.utcnow)
    specification_version: str = "3.0"

    overall_score: Optional[float] = 0.0
    overall_score_before_cap: Optional[float] = None
    grade: TestGrade = TestGrade.F
    strategic_score: float = 0.0

    test_results: list[TestResult] = Field(default_factory=list)
    category_scores: list[CategoryScore] = Field(default_factory=list)
    mandatory_minimum_status: dict[str, TestStatus] = Field(default_factory=dict)
    mandatory_minimums_passed: bool = False
    mandatory_minimums_inconclusive: list[str] = Field(default_factory=list)
    mandatory_minimum_violations: list[str] = Field(default_factory=list)
    score_capped: bool = False

    passed: bool = False

    gaps: list[GovernanceGap] = Field(default_factory=list)
    run_mode: str = "full"
    provider_capabilities: Optional[ProviderCapabilities] = None
    regulatory_frameworks: list[str] = Field(default_factory=list)
    judge_stats: Optional[dict[str, Any]] = None
    warnings: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    self_judged: bool = False


class TestDelta(BaseModel):
    __test__ = False

    model_config = {"frozen": True}

    test_id: str
    test_name: str = ""
    baseline_score: float = 0.0
    enhanced_score: float = 0.0
    delta: float = 0.0
    status_change: str = "unchanged"
    gap_closed: bool = False


class ComparisonReport(BaseModel):

    baseline: Optional[TestRunResult] = None
    enhanced: Optional[TestRunResult] = None
    baseline_system: str = ""
    enhanced_system: str = ""
    baseline_overall: float = 0.0
    enhanced_overall: float = 0.0
    overall_delta: float = 0.0
    grade_change: str = ""
    baseline_grade: TestGrade = TestGrade.F
    enhanced_grade: TestGrade = TestGrade.F
    test_deltas: list[TestDelta] = Field(default_factory=list)
    gaps_closed: list[str] = Field(default_factory=list)
    gaps_opened: list[str] = Field(default_factory=list)
    gaps_remaining: list[str] = Field(default_factory=list)
    fixture_mismatch: bool = False


class FixtureMetadata(BaseModel):

    name: str
    version: str = "1.0"
    domain: str = ""
    description: str = ""
    timestamp: Optional[str] = None


class Role(BaseModel):

    name: str
    role_id: str = ""
    description: str = ""
    level: str = ""
    department: str = ""


class User(BaseModel):

    user_id: str
    name: str
    roles: list[str] = Field(default_factory=list)


class Tool(BaseModel):

    tool_id: str
    name: str
    category: str = ""
    risk_level: str = "medium"
    description: str = ""
    tool_behavior: str = ""


class Permission(BaseModel):

    role: str
    tools: list[str] = Field(default_factory=list)


class DataSource(BaseModel):

    source_id: str
    name: str
    classification: str = "internal"
    source_types: list[str] = Field(default_factory=list)


class Policy(BaseModel):

    confidence_threshold: float = 0.85
    citation_required: bool = True
    override_allowed_roles: list[str] = Field(default_factory=list)
    audit_logging: str = "required"


class Regulation(BaseModel):

    name: str
    required_tests: list[str] = Field(default_factory=list)


class TestCase(BaseModel):
    __test__ = False

    test_id: str
    test: str
    scenario: str = ""
    expected_result: str = ""
    user_role: str = ""
    tool_id: str = ""
    reasoning: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExpectedClaim(BaseModel):

    model_config = {"frozen": True}

    claim: str
    supported: bool
    source_id: Optional[str] = None
    evidence: Optional[str] = None
    reason: Optional[str] = None

    @model_validator(mode="after")
    def check_fields(self) -> "ExpectedClaim":
        if self.supported and (not self.source_id or not self.evidence):
            raise ValueError("supported=True requires source_id and evidence")
        if not self.supported and not self.reason:
            raise ValueError("supported=False requires reason")
        return self


class Fixture(BaseModel):

    metadata: FixtureMetadata
    roles: list[Role] = Field(default_factory=list)
    users: list[User] = Field(default_factory=list)
    tools: list[Tool] = Field(default_factory=list)
    permissions: list[Permission] = Field(default_factory=list)
    data_sources: list[DataSource] = Field(default_factory=list)
    policies: Policy = Field(default_factory=Policy)
    regulations: list[Regulation] = Field(default_factory=list)
    test_cases: list[TestCase] = Field(default_factory=list)

    escalation_triggers: list[str] = Field(default_factory=list)
    high_risk_actions: list[str] = Field(default_factory=list)
    sensitive_data_classes: list[str] = Field(default_factory=list)
    system_purpose: str = ""
    primary_objective_template: str = ""

    # Optional inline governance bundle. When supplied via a top-level
    # `governance:` block on the diagnostic YAML, the loader hydrates this
    # field with a ready-to-use `GovernanceFixture` so the CLI does not
    # have to load a separate file. Stored as `Any` to avoid a circular
    # import; the loader and CLI always handle the concrete type.
    governance: Any | None = None
    # How `governance` was populated: "explicit" when the YAML carried a
    # full block, "synth" when synthesized from tools+permissions via the
    # `synthesize: true` marker, or `None` when no governance is present.
    governance_source: Optional[Literal["explicit", "synth"]] = None

    expected_claims: list["ExpectedClaim"] = Field(default_factory=list)

    def test_cases_for_test(self, test_id: str) -> list[TestCase]:
        return [tc for tc in self.test_cases if tc.test == test_id]

    def has_coverage_for(self, test_id: str) -> bool:
        return any(tc.test == test_id for tc in self.test_cases)
