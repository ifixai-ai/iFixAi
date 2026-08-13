import asyncio
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, NoReturn

from ifixai.core.types import (
    ActionConfirmationRequest,
    ActivationAuthorityReport,
    AuditRecord,
    ChatMessage,
    ConfidenceReport,
    ConfigurationVersion,
    ConfirmationGateReport,
    CorpusScreeningReport,
    DelegationHandoffClaim,
    DelegationScopeReport,
    DeploymentGateReport,
    DetectionAuditWindow,
    FallbackRoutingReport,
    GovernanceArchitecture,
    GroundingReport,
    IdentityAttestationReport,
    OutcomeMetricFeed,
    OutcomeReconciliationReport,
    OverrideReceipt,
    Permission,
    ProviderCapabilities,
    ProviderConfig,
    ResponseIdentityClaim,
    RetrievedSource,
    Role,
    RoutingDecision,
    StandingActivationRequest,
    ToolInfo,
    ToolInvocationResult,
    TrainingRecordCandidate,
)


class ProviderCapability(str, Enum):
    TOOL_CALLING = "tool_calling"
    RETRIEVAL = "retrieval"
    AUDIT_TRAIL = "audit_trail"
    ROUTING = "routing"
    GROUNDING = "grounding"
    AUTHORIZATION = "authorization"
    GOVERNANCE_ARCHITECTURE = "governance_architecture"
    OVERRIDE_MECHANISM = "override_mechanism"
    RATE_LIMIT_OBSERVABILITY = "rate_limit_observability"
    CONFIGURATION_VERSIONING = "configuration_versioning"
    CONFIDENCE_SCORING = "confidence_scoring"
    HUMAN_ROUTING = "human_routing"
    OUTCOME_RECONCILIATION = "outcome_reconciliation"
    DEPLOYMENT_GATE = "deployment_gate"
    CONFIRMATION_GATE = "confirmation_gate"
    ACTIVATION_REVALIDATION = "activation_revalidation"
    CORPUS_SCREENING = "corpus_screening"
    IDENTITY_ATTESTATION = "identity_attestation"
    DELEGATION_ATTENUATION = "delegation_attenuation"


_logger = logging.getLogger(__name__)

_CAPABILITY_INSPECTION_EXPECTED_ERRORS: tuple[type[BaseException], ...] = (
    NotImplementedError,
    AttributeError,
    ConnectionError,
    OSError,
    asyncio.TimeoutError,
    ValueError,
    TypeError,
    RuntimeError,
)


class ProviderError(Exception):
    def __init__(
        self,
        provider: str = "",
        endpoint: str = "",
        details: str = "",
    ) -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.details = details
        super().__init__(f"[{provider}] {details} (endpoint: {endpoint})")


class ProviderConnectionError(ProviderError):
    pass


class ProviderAuthError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


_FATAL_ERROR_MARKERS: tuple[str, ...] = (
    "401",
    "403",
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "unauthorized",
    "permission denied",
    "permissiondenied",
    "authentication",
    "limit exceeded",
    "quota",
    "insufficient_quota",
    "billing",
    "payment required",
    "credit",
)

# The subset meaning the account itself is spent, not merely throttled. A 429
# carrying any of these never clears on retry, so the run must stop and say so
# rather than burn the whole budget on calls that cannot succeed.
_QUOTA_EXHAUSTED_MARKERS: tuple[str, ...] = (
    "quota",
    "insufficient_quota",
    "insufficient credits",
    "billing",
    "payment required",
    "credit",
)


def is_fatal_provider_error(exc: BaseException) -> bool:
    """True when an error means the credential is rejected / out of quota."""
    if isinstance(exc, ProviderAuthError):
        return True
    # Transient by construction, so never a credential problem — and they must
    # be excluded by type, not by text. The markers below are matched as bare
    # substrings, and a truncation detail carries a character count: a reply cut
    # off at 403 characters reads as HTTP 403 and aborts the whole run.
    if isinstance(exc, TRANSIENT_PROVIDER_ERRORS):
        return False
    text = str(exc).lower()
    # Every provider maps HTTP 429 to ProviderRateLimitError whether it is a
    # per-minute ceiling that clears in seconds or an exhausted account that
    # never will, so the body is the only thing that separates them. Match the
    # narrow account-dead markers, not the generic ones: "rate limit exceeded"
    # contains "limit exceeded" and must stay retryable.
    if isinstance(exc, ProviderRateLimitError):
        return any(marker in text for marker in _QUOTA_EXHAUSTED_MARKERS)
    return any(marker in text for marker in _FATAL_ERROR_MARKERS)


OPTIONAL_REQUEST_PARAMS: tuple[str, ...] = ("response_format", "reasoning")


def drop_rejected_optional_params(kwargs: dict[str, Any], detail: str) -> list[str]:
    """Remove any optional tuning param named in `detail`; return what was removed.

    Vendor extensions such as OpenRouter's ``reasoning`` ride inside ``extra_body``
    because the OpenAI SDK's ``create()`` has a closed signature, so both nesting
    levels are searched. Mutates `kwargs` in place — the caller is retrying the
    very request being trimmed.
    """
    low = detail.lower()
    extra_body = kwargs.get("extra_body")
    removed: list[str] = []
    for name in OPTIONAL_REQUEST_PARAMS:
        if name not in low:
            continue
        if name in kwargs:
            kwargs.pop(name)
            removed.append(name)
        elif isinstance(extra_body, dict) and name in extra_body:
            extra_body.pop(name)
            removed.append(name)
    if isinstance(extra_body, dict) and not extra_body:
        kwargs.pop("extra_body", None)
    return removed


async def create_chat_completion_json_fallback(client: Any, **kwargs: Any) -> Any:
    """Call ``client.chat.completions.create(**kwargs)``, retrying once without
    whichever optional tuning param the provider rejected.

    ``response_format`` (JSON mode) and ``reasoning`` (thinking suppression) are
    both best-effort: dropping either still yields a gradeable reply, because
    json-repair parses free text. Any other BadRequestError (bad model, context
    overflow) propagates unchanged so the root cause is not lost. Callers already
    import the ``openai`` SDK; the import is local so ``base.py`` stays usable
    without the optional extra.
    """
    import openai

    try:
        return await client.chat.completions.create(**kwargs)
    except openai.BadRequestError as exc:
        if not drop_rejected_optional_params(kwargs, str(exc)):
            raise
        return await client.chat.completions.create(**kwargs)


def friendly_provider_message(detail: str) -> str | None:
    """Map a raw provider error string to one actionable sentence, or None."""
    low = detail.lower()
    if "limit exceeded" in low or "quota" in low or "insufficient_quota" in low:
        return (
            "API key is out of quota / hit its usage limit. "
            "Top it up, raise the limit, or use a different key."
        )
    if (
        "401" in low
        or "invalid api key" in low
        or "incorrect api key" in low
        or "unauthorized" in low
        or "authentication" in low
    ):
        return (
            "API key was rejected (authentication failed). "
            "Check the key value and that it matches the chosen provider."
        )
    if "403" in low or "permission" in low or "forbidden" in low:
        return (
            "Access was forbidden (403). The key may lack permission for this "
            "model, or have exceeded a usage/billing limit."
        )
    if "429" in low or "rate limit" in low or "rate_limit" in low:
        return (
            "Rate limited by the provider. Lower --concurrency or wait a moment "
            "and retry."
        )
    if (
        "502" in low
        or "503" in low
        or "504" in low
        or "overloaded" in low
        or "no available model provider" in low
    ):
        return (
            "The gateway or the chosen model was temporarily unavailable (5xx). "
            "Retry, or add fallback judge models (see docs/cli.md)."
        )
    if "billing" in low or "payment" in low or "credit" in low:
        return "Provider reports a billing/credit problem on the account."
    return None


class ProviderEmptyContentError(ProviderResponseError):
    """Provider returned a successful response with no text content.

    Distinct from ``ProviderResponseError`` (other response-shape failures)
    so the harness can route empty SUT output to ``TestStatus.INCONCLUSIVE``
    rather than ``TestStatus.ERROR``. The SUT completed its call; it simply
    produced no scoreable output (safety filter, refusal-as-empty, or
    upstream API truncation). This is *unscorable*, not *misconfigured*.

    Subclasses ``ProviderResponseError`` so existing ``except`` clauses keep
    working.
    """

    pass


# finish_reason values meaning the provider stopped mid-generation rather than
# because the model was done.
TRUNCATED_FINISH_REASONS: frozenset[str] = frozenset({"length", "max_tokens"})


class ProviderTruncatedError(ProviderResponseError):
    """Provider returned text but stopped mid-generation.

    A cut-off reply is not an answer: grading it manufactures a failure out of
    an upstream cutoff. Deliberately NOT a ``ProviderEmptyContentError``, whose
    handler voids the whole inspection. This lands on the generic
    ``ProviderError`` branch, which drops the single probe as unscorable and
    leaves the rest of the inspection intact.
    """

    pass


class ProviderOverloadedError(ProviderResponseError):
    """The gateway or the model behind it was unavailable for this call.

    Covers the whole retryable band of the OpenRouter error contract — 408
    request timeout, 500 internal error, 502 (chosen model down / invalid
    upstream response), 503 (no provider meets the routing requirements), 504.
    None of these is a statement about the credential or the prompt: the same
    call to the same model can succeed a second later, and a *different* model
    will usually succeed immediately. Transient by construction, so it degrades
    a probe or advances the judge fallback chain rather than aborting the run.

    Deliberately distinct from ``ProviderResponseError`` (a malformed or
    contract-breaking reply from a gateway that *did* answer).
    """

    pass


RETRYABLE_HTTP_STATUS_CODES: frozenset[int] = frozenset({408, 500, 502, 503, 504})


# Upstream conditions that clear on their own. None of them means the provider
# is gone, so they are never fatal and never abort a run — a caller degrades the
# affected probe instead. Declared after the classes it names so the tuple can
# be built at import time.
TRANSIENT_PROVIDER_ERRORS: tuple[type[BaseException], ...] = (
    ProviderTruncatedError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderConnectionError,
    ProviderOverloadedError,
)


def raise_if_truncated(
    provider: str, endpoint: str, finish_reason: str, content: str
) -> None:
    """Reject a reply the provider cut short.

    Call this BEFORE the empty-content check. A reasoning model that spends its
    whole budget thinking returns zero characters with finish_reason=length:
    still a cutoff, but checking emptiness first reports it as a dead judge and
    fail-fast aborts the run.
    """
    if finish_reason.lower() in TRUNCATED_FINISH_REASONS:
        raise ProviderTruncatedError(
            provider=provider,
            endpoint=endpoint,
            details=(
                f"Response truncated mid-generation "
                f"(finish_reason={finish_reason}, {len(content)} chars)"
            ),
        )


def raise_if_choice_errored(
    provider: str, endpoint: str, choice: Any, content: str
) -> None:
    """Reject a reply the upstream aborted part-way through.

    A gateway that is rate-limited mid-generation returns the text produced so
    far with ``finish_reason="error"``, an embedded error object and zero billed
    tokens. The partial text looks like an ordinary short answer, so without
    this it is graded as one: an upstream 429 becomes a model failure.
    """
    if (getattr(choice, "finish_reason", "") or "").lower() != "error":
        return
    err = getattr(choice, "error", None) or {}
    code = err.get("code") if isinstance(err, dict) else None
    message = err.get("message", "") if isinstance(err, dict) else str(err)
    detail = (
        f"Upstream aborted the generation (finish_reason=error, code={code}, "
        f"{len(content)} chars returned): {message}"
    )
    if code == 429 or "rate" in str(message).lower():
        raise ProviderRateLimitError(
            provider=provider, endpoint=endpoint, details=detail
        )
    if code in RETRYABLE_HTTP_STATUS_CODES:
        raise ProviderOverloadedError(
            provider=provider, endpoint=endpoint, details=detail
        )
    raise ProviderTruncatedError(provider=provider, endpoint=endpoint, details=detail)


def raise_for_http_status(provider: str, endpoint: str, exc: Exception) -> NoReturn:
    """Translate an OpenAI-SDK HTTP error into the matching provider exception.

    Splits the gateway's retryable band (408/5xx, per the OpenRouter error
    contract) away from genuine request/credential faults, so a model that is
    momentarily down degrades one probe instead of reading as a dead provider
    and aborting the run. Always raises.
    """
    status = getattr(exc, "status_code", None)
    details = f"HTTP {status}: {exc}" if status is not None else str(exc)
    if status in RETRYABLE_HTTP_STATUS_CODES:
        raise ProviderOverloadedError(
            provider=provider, endpoint=endpoint, details=details
        ) from exc
    raise ProviderResponseError(
        provider=provider, endpoint=endpoint, details=details
    ) from exc


class ChatProvider(ABC):
    surfaces_rate_limit_errors: bool = True
    replay_protected: bool = True

    @abstractmethod
    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str: ...

    async def aclose(self) -> None:
        """Release any long-lived network resources held by the provider.

        Default no-op so callers (e.g. orchestrator teardown) can invoke
        aclose() on any ChatProvider without type-checking which concrete
        subclass holds an HTTP/SDK client pool.
        """
        return None

    async def list_tools(
        self,
        config: ProviderConfig,
    ) -> list[ToolInfo] | None:
        return None

    async def invoke_tool(
        self,
        tool_id: str,
        user_role: str,
        params: dict,
        config: ProviderConfig,
    ) -> ToolInvocationResult | None:
        return None

    async def retrieve_sources(
        self,
        query: str,
        config: ProviderConfig,
    ) -> list[RetrievedSource] | None:
        return None

    async def get_audit_trail(
        self,
        request_id: str,
        config: ProviderConfig,
    ) -> list[AuditRecord] | None:
        return None

    async def get_routing_decision(
        self,
        config: ProviderConfig,
    ) -> RoutingDecision | None:
        return None

    async def get_grounding_report(
        self,
        config: ProviderConfig,
    ) -> GroundingReport | None:
        return None

    async def authorize_tool(
        self,
        tool_id: str,
        user_role: str,
        config: ProviderConfig,
    ) -> ToolInvocationResult | None:
        return None

    async def get_governance_architecture(
        self,
        config: ProviderConfig,
    ) -> GovernanceArchitecture | None:
        return None

    async def apply_override(
        self,
        request_id: str,
        config: ProviderConfig,
        intent: str | None = None,
        policy_test_id: str | None = None,
    ) -> OverrideReceipt | None:
        return None

    async def get_configuration_version(
        self,
        config: ProviderConfig,
    ) -> ConfigurationVersion | None:
        return None

    async def get_confidence(
        self,
        query: str,
        config: ProviderConfig,
    ) -> ConfidenceReport | None:
        """Return the system's self-reported confidence + abstain/uncertain output
        path for `query` (C02). Default None: a confidence-blind provider exposes
        no such surface, and C02 routes that to INCONCLUSIVE (no self-report
        fallback), the structural-path contract.
        """
        return None

    async def route_to_human(
        self,
        observation: str,
        confidence: float,
        config: ProviderConfig,
    ) -> FallbackRoutingReport | None:
        """Decide whether `observation` (with the supplied capability `confidence`)
        is handed to a human / manual fallback path or auto-completed (C05).

        `confidence` is the runner-SUPPLIED trigger, not a self-report: C05 scores
        whether the routing gate fires when uncertainty is present, not whether the
        system can estimate its own confidence. Default None: a routing-blind
        provider exposes no such surface, and C05 routes that to INCONCLUSIVE (no
        fallback), the structural-path contract shared with get_confidence.
        """
        return None

    async def reconcile_outcome(
        self,
        feed: OutcomeMetricFeed,
        config: ProviderConfig,
    ) -> OutcomeReconciliationReport | None:
        """Reconcile `feed`'s realised actuals against its declared operational
        target and decide whether to surface adverse drift (C11).

        The runner SUPPLIES the outcome/KPI feed (declared target + realised
        series, optionally a true-objective series); the system reconciles
        realised-vs-DECLARED and returns whether it surfaced adverse drift, reported
        only conformance, or flagged single-metric blindness. C11 scores whether the
        system tracks the declared goal — not whether each individual output is
        valid. Default None: an outcome-blind provider exposes no such surface, and
        C11 routes that to INCONCLUSIVE (no self-report fallback), the structural-
        path contract shared with get_confidence / route_to_human.
        """
        return None

    async def evaluate_deployment_gate(
        self,
        window: DetectionAuditWindow,
        config: ProviderConfig,
    ) -> DeploymentGateReport | None:
        """Reconcile `window`'s MEASURED detection performance (true-positive rate and
        false-positive burden, from an audited window with planted ground truth) against
        the DECLARED detection spec, and decide whether the perception detector may be
        scaled / kept in production (X04).

        The runner SUPPLIES the audit window (declared TPR floor + FP ceiling, measured
        scanner true-positives over planted positives, false-positives over total
        scans); the system reconciles measured-vs-DECLARED and returns whether it blocks
        deployment, allows it, or flags insufficient evidence (no planted ground truth →
        no measurable TPR → not approvable). X04 scores whether a failing detector is
        deterministically blocked — not the sensor's intrinsic accuracy. Default None: a
        gate-blind provider exposes no such surface, and X04 routes that to INCONCLUSIVE
        (no self-report fallback), the structural-path contract shared with
        get_confidence / route_to_human / reconcile_outcome.
        """
        return None

    async def evaluate_confirmation_gate(
        self,
        request: ActionConfirmationRequest,
        config: ProviderConfig,
    ) -> ConfirmationGateReport | None:
        """Decide whether a pending high-stakes / sensitive action may auto-execute or must be
        held for a real human confirmation (X11).

        The runner SUPPLIES the action descriptor (the declared policy sensitivity plus the
        configured confirmation setup — gate presence, enforcement, and confirming authority);
        the system reconciles the classification against the setup and returns whether it requires
        human confirmation, allows the action to proceed, or escalates an unclassified action for
        human classification. X11 scores whether a high-stakes action with no enforced human gate
        (or a bot-only appeal path) is deterministically blocked — not whether the action itself is
        correct. Default None: a gate-blind provider exposes no such surface, and X11 routes that to
        INCONCLUSIVE (no self-report fallback), the structural-path contract shared with
        get_confidence / route_to_human / reconcile_outcome / evaluate_deployment_gate.
        """
        return None

    async def revalidate_activation_authority(
        self,
        request: StandingActivationRequest,
        config: ProviderConfig,
    ) -> ActivationAuthorityReport | None:
        """Re-validate the authority behind one pending activation of a standing automation
        against CURRENT state, and decide whether it may execute (M02).

        The runner SUPPLIES the activation descriptor (activation kind, whether the granting
        record resolves, and the current state of the four authority legs — grantor still in
        the authorizing role, consent still active, policy version still matching, TTL not yet
        passed); the system re-checks the grant rather than replaying it and returns whether it
        blocks the activation, allows it, or escalates an unverifiable grant — together with the
        machine-readable staleness reasons that justify a block. M02 scores whether a standing
        grant that has gone stale stops the next activation — not whether the automation's work
        is itself correct. Default None: a plane with no re-validation surface exposes none, and
        M02 routes that to INCONCLUSIVE (no self-report fallback), the structural-path contract
        shared with get_confidence / route_to_human / reconcile_outcome /
        evaluate_deployment_gate / evaluate_confirmation_gate.
        """
        return None

    async def screen_training_corpus(
        self,
        request: TrainingRecordCandidate,
        config: ProviderConfig,
    ) -> CorpusScreeningReport | None:
        """Screen one candidate record before it enters a fine-tuning / distillation corpus,
        and decide whether it may be used to train a future model (M03).

        The runner SUPPLIES the candidate descriptor (record origin, whether a provenance
        manifest is attached and whether its attestation resolves, whether the body carries an
        instruction-bearing payload or a seeded trigger canary, and the pre-computed
        near-duplicate cluster statistic against the corpus baseline); the deployment's corpus
        gate returns whether it quarantines the record, admits it, or escalates one whose
        provenance cannot be verified — together with the machine-readable contamination
        reasons that justify a quarantine. M03 scores whether a poisoned or unattributable
        record is stopped BEFORE training, not whether the deployer's detectors are good (that
        is X04). Default None: a deployment with no corpus-screening surface exposes none, and
        M03 routes that to INCONCLUSIVE (no self-report fallback), the structural-path contract
        shared with get_confidence / route_to_human / reconcile_outcome /
        evaluate_deployment_gate / evaluate_confirmation_gate /
        revalidate_activation_authority.
        """
        return None

    async def attest_response_identity(
        self,
        request: ResponseIdentityClaim,
        config: ProviderConfig,
    ) -> IdentityAttestationReport | None:
        """Attribute one served response to a resolved model identity and decide whether an upstream
        substitution left the declared safety profile (M06).

        The runner SUPPLIES the per-response claim (the declared model id / build / safety tier for the
        request class, the served id / build / tier, whether provider attestation metadata is attached
        and resolves, and whether an interleaved behavioural fingerprint probe was conclusive); the
        deployment's identity gate returns whether it blocks or flags the substitution, allows the
        response, or escalates one it cannot attribute at all — together with the machine-readable
        substitution reasons that justify a block AND the identity it wrote to the audit record. M06
        scores whether a silent substitution (provider fallback, router reroute, re-quantized build,
        removed safety layer) is detected and logged per response, not whether the deployer's
        fingerprint probes are good (that is X04). Default None: a deployment with no
        identity-attestation surface exposes none, and M06 routes that to INCONCLUSIVE (no self-report
        fallback), the structural-path contract shared with get_confidence / route_to_human /
        reconcile_outcome / evaluate_deployment_gate / evaluate_confirmation_gate /
        revalidate_activation_authority / screen_training_corpus.
        """
        return None

    async def attenuate_delegation_scope(
        self,
        request: DelegationHandoffClaim,
        config: ProviderConfig,
    ) -> DelegationScopeReport | None:
        """Decide one pending delegation hop to an EXTERNAL agent and report the scope it issues (M07).

        The runner SUPPLIES the per-hop claim (whether the hop crosses an organizational boundary; the
        delegator's own scope, the sub-task's least-privilege requirement and the scope the outgoing hop
        would carry; the extra scope the counterparty's returned payload asked for; the constraints
        bound to the data versus those the hop propagates; the expected counterparty id and the state of
        its attestation); the deployment's delegation gate returns whether it blocks the hop, allows it,
        or escalates a counterparty it cannot identify at all — together with the machine-readable
        scope-violation reasons that justify a block AND the capability set it actually attaches to the
        hop. M07 scores whether least privilege is enforced per delegation hop against a counterparty the
        deployer does not control — the cross-organization confused deputy — not whether the counterparty
        then behaves (which is unobservable, and precisely why the scope must be right before it leaves).
        Default None: a deployment with no delegation-attenuation surface exposes none, and M07 routes
        that to INCONCLUSIVE (no self-report fallback), the structural-path contract shared with
        get_confidence / route_to_human / reconcile_outcome / evaluate_deployment_gate /
        evaluate_confirmation_gate / revalidate_activation_authority / screen_training_corpus /
        attest_response_identity.
        """
        return None

    async def get_roles(
        self,
        config: ProviderConfig,
    ) -> list[Role] | None:
        return None

    async def get_permission_matrix(
        self,
        config: ProviderConfig,
    ) -> list[Permission] | None:
        return None


async def detect_capabilities(
    provider: ChatProvider,
    config: ProviderConfig,
) -> ProviderCapabilities:
    caps = {
        "has_tool_calling": False,
        "has_retrieval": False,
        "has_audit_trail": False,
        "has_routing": False,
        "has_grounding": False,
        "has_authorization": False,
        "has_governance_architecture": False,
        "has_override_mechanism": False,
        "has_rate_limit_observability": False,
        "has_configuration_versioning": False,
        "has_confidence_scoring": False,
        "has_human_routing": False,
        "has_outcome_reconciliation": False,
        "has_deployment_gate": False,
        "has_confirmation_gate": False,
        "has_activation_revalidation": False,
        "has_corpus_screening": False,
        "has_identity_attestation": False,
        "has_delegation_attenuation": False,
    }

    provider_name = type(provider).__name__

    try:
        result = await provider.list_tools(config)
        caps["has_tool_calling"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection list_tools failed for %s", provider_name
        )

    try:
        result = await provider.retrieve_sources("test", config)
        caps["has_retrieval"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection retrieve_sources failed for %s", provider_name
        )

    try:
        result = await provider.get_audit_trail("test", config)
        caps["has_audit_trail"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection get_audit_trail failed for %s", provider_name
        )

    try:
        result = await provider.get_routing_decision(config)
        caps["has_routing"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection get_routing_decision failed for %s", provider_name
        )

    try:
        result = await provider.get_grounding_report(config)
        caps["has_grounding"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection get_grounding_report failed for %s", provider_name
        )

    try:
        result = await provider.authorize_tool("_test", "_test", config)
        caps["has_authorization"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection authorize_tool failed for %s", provider_name
        )

    try:
        result = await provider.get_governance_architecture(config)
        caps["has_governance_architecture"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection get_governance_architecture failed for %s",
            provider_name,
        )

    try:
        result = await provider.apply_override("_capability_inspection", config)
        caps["has_override_mechanism"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection apply_override failed for %s", provider_name
        )

    try:
        result = await provider.get_configuration_version(config)
        caps["has_configuration_versioning"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection get_configuration_version failed for %s",
            provider_name,
        )

    try:
        result = await provider.get_confidence("_capability_inspection", config)
        caps["has_confidence_scoring"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection get_confidence failed for %s", provider_name
        )

    try:
        result = await provider.route_to_human("_capability_inspection", 0.0, config)
        caps["has_human_routing"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection route_to_human failed for %s", provider_name
        )

    try:
        probe_feed = OutcomeMetricFeed(
            metric_name="_capability_inspection",
            declared_target=0.0,
            realised_series=[0.0],
            higher_is_better=True,
        )
        result = await provider.reconcile_outcome(probe_feed, config)
        caps["has_outcome_reconciliation"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection reconcile_outcome failed for %s", provider_name
        )

    try:
        probe_window = DetectionAuditWindow(
            detector_name="_capability_inspection",
            total_scans=1,
            planted_positive_count=1,
            scanner_true_positives=0,
            false_positives=0,
            declared_tpr_floor=0.0,
            declared_fp_ceiling=1.0,
        )
        result = await provider.evaluate_deployment_gate(probe_window, config)
        caps["has_deployment_gate"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection evaluate_deployment_gate failed for %s",
            provider_name,
        )

    try:
        probe_request = ActionConfirmationRequest(
            action_name="_capability_inspection",
            policy_sensitivity="routine",
            confirmation_gate_present=False,
            auto_execution_blocked=False,
            confirmation_authority="none",
        )
        result = await provider.evaluate_confirmation_gate(probe_request, config)
        caps["has_confirmation_gate"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection evaluate_confirmation_gate failed for %s",
            provider_name,
        )

    try:
        probe_activation = StandingActivationRequest(
            automation_name="_capability_inspection",
            activation_kind="interactive",
            grant_record_present=False,
            grantor_still_authorized=True,
            consent_active=True,
            current_policy_version="0",
            authority_expires_at_activation=0,
            current_activation_index=0,
        )
        result = await provider.revalidate_activation_authority(
            probe_activation, config
        )
        caps["has_activation_revalidation"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection revalidate_activation_authority failed for %s",
            provider_name,
        )

    try:
        probe_record = TrainingRecordCandidate(
            record_name="_capability_inspection",
            record_origin="human_authored",
            manifest_present=False,
            manifest_resolves=False,
            instruction_payload_present=False,
            canary_trigger_present=False,
            duplicate_cluster_size=1,
            duplicate_cluster_threshold=1,
        )
        result = await provider.screen_training_corpus(probe_record, config)
        caps["has_corpus_screening"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection screen_training_corpus failed for %s",
            provider_name,
        )

    try:
        probe_claim = ResponseIdentityClaim(
            response_name="_capability_inspection",
            declared_model_id="_declared",
            declared_model_version="1",
            declared_safety_tier=0,
            served_model_id="_declared",
            served_model_version="1",
            served_safety_tier=0,
            attestation_present=False,
            attestation_resolves=False,
            fingerprint_probe_conclusive=False,
        )
        result = await provider.attest_response_identity(probe_claim, config)
        caps["has_identity_attestation"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection attest_response_identity failed for %s",
            provider_name,
        )

    try:
        probe_handoff = DelegationHandoffClaim(
            delegation_name="_capability_inspection",
            crosses_org_boundary=True,
            parent_scope=("_probe",),
            subtask_required_scope=("_probe",),
            proposed_delegated_scope=("_probe",),
            expected_counterparty_id="_counterparty",
            counterparty_attestation_present=False,
            counterparty_attestation_resolves=False,
        )
        result = await provider.attenuate_delegation_scope(probe_handoff, config)
        caps["has_delegation_attenuation"] = result is not None
    except _CAPABILITY_INSPECTION_EXPECTED_ERRORS:
        _logger.exception(
            "Capability inspection attenuate_delegation_scope failed for %s",
            provider_name,
        )

    caps["has_rate_limit_observability"] = bool(provider.surfaces_rate_limit_errors)

    return ProviderCapabilities(**caps)
