import asyncio
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, NoReturn

from ifixai.core.types import (
    ActionConfirmationRequest,
    AuditRecord,
    ChatMessage,
    ConfidenceReport,
    ConfigurationVersion,
    ConfirmationGateReport,
    DeploymentGateReport,
    DetectionAuditWindow,
    FallbackRoutingReport,
    GovernanceArchitecture,
    GroundingReport,
    OutcomeMetricFeed,
    OutcomeReconciliationReport,
    OverrideReceipt,
    Permission,
    ProviderCapabilities,
    ProviderConfig,
    RetrievedSource,
    Role,
    RoutingDecision,
    ToolInfo,
    ToolInvocationResult,
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
    return any(marker in text for marker in _FATAL_ERROR_MARKERS)


async def create_chat_completion_json_fallback(client: Any, **kwargs: Any) -> Any:
    """Call ``client.chat.completions.create(**kwargs)``, retrying once without
    ``response_format`` if the provider rejects JSON mode specifically.

    Any other BadRequestError (bad model, context overflow) propagates unchanged
    so the root cause is not lost. Callers already import the ``openai`` SDK; the
    import is local so ``base.py`` stays usable without the optional extra.
    """
    import openai

    try:
        return await client.chat.completions.create(**kwargs)
    except openai.BadRequestError as exc:
        if "response_format" not in kwargs or "response_format" not in str(exc).lower():
            raise
        kwargs.pop("response_format")
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

    caps["has_rate_limit_observability"] = bool(provider.surfaces_rate_limit_errors)

    return ProviderCapabilities(**caps)
