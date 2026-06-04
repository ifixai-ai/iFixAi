import asyncio
import logging
from abc import ABC, abstractmethod
from enum import Enum

from ifixai.core.types import (
    AuditRecord,
    ChatMessage,
    ConfidenceReport,
    ConfigurationVersion,
    FallbackRoutingReport,
    GovernanceArchitecture,
    GroundingReport,
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

    caps["has_rate_limit_observability"] = bool(provider.surfaces_rate_limit_errors)

    return ProviderCapabilities(**caps)
