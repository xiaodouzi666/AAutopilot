"""Deterministic complexity routing and safe weak-to-strong escalation."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .complexity import (
    DEFAULT_COMPLEXITY_THRESHOLD,
    ComplexityFeatures,
    ComplexityReport,
    score_complexity,
)
from .schema import TriageResponse
from .validator import ValidationResult, validate_response

InitialRoute = Literal["weak", "strong"]
FinalRoute = Literal["weak", "strong", "weak_then_strong"]
ModelCallable = Callable[[str], Any]
AsyncModelCallable = Callable[[str], Awaitable[Any] | Any]


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: InitialRoute
    complexity_score: float
    threshold: float
    features: ComplexityFeatures
    contributions: dict[str, float]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["features"] = self.features.as_dict()
        return result


@dataclass(frozen=True, slots=True)
class RoutingMetadata:
    initial_route: InitialRoute
    final_route: FinalRoute
    selected_model: Literal["weak", "strong"]
    complexity_score: float
    threshold: float
    weak_attempted: bool
    escalated: bool
    escalation_reason: str | None
    weak_validation: ValidationResult | None
    strong_validation: ValidationResult | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "initial_route": self.initial_route,
            "final_route": self.final_route,
            "selected_model": self.selected_model,
            "complexity_score": self.complexity_score,
            "threshold": self.threshold,
            "weak_attempted": self.weak_attempted,
            "escalated": self.escalated,
            "escalation_reason": self.escalation_reason,
            "weak_validation": self.weak_validation.as_dict() if self.weak_validation else None,
            "strong_validation": self.strong_validation.as_dict()
            if self.strong_validation
            else None,
        }


@dataclass(frozen=True, slots=True)
class RoutedResponse:
    response: TriageResponse
    metadata: RoutingMetadata


class ModelInvocationError(RuntimeError):
    pass


class InvalidStrongModelOutput(ModelInvocationError):
    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        issues = "; ".join(issue.code for issue in result.issues)
        super().__init__(f"strong model output failed validation: {issues or 'unknown error'}")


class ComplexityRouter:
    """A transparent router whose decision sees only the request text."""

    def __init__(self, threshold: float = DEFAULT_COMPLEXITY_THRESHOLD) -> None:
        if not 0.0 <= threshold <= 100.0:
            raise ValueError("complexity threshold must be in 0..100")
        self.threshold = float(threshold)

    def decide(self, incident: str) -> RouteDecision:
        report: ComplexityReport = score_complexity(incident)
        # Equality routes strong.  This conservative boundary also makes a single explicit
        # ambiguity marker strong at the default threshold.
        route: InitialRoute = "strong" if report.score >= self.threshold else "weak"
        reason = (
            f"complexity {report.score:.3f} {'>=' if route == 'strong' else '<'} "
            f"frozen threshold {self.threshold:.3f}"
        )
        return RouteDecision(
            route=route,
            complexity_score=report.score,
            threshold=self.threshold,
            features=report.features,
            contributions=report.contributions,
            reason=reason,
        )


class CascadeRouter:
    """Call the weak tier only for simple requests and validate before returning it."""

    def __init__(
        self,
        weak_model: ModelCallable,
        strong_model: ModelCallable,
        *,
        threshold: float = DEFAULT_COMPLEXITY_THRESHOLD,
    ) -> None:
        self.weak_model = weak_model
        self.strong_model = strong_model
        self.complexity_router = ComplexityRouter(threshold)

    @staticmethod
    def _valid_strong(value: Any) -> tuple[TriageResponse, ValidationResult]:
        validation = validate_response(value)
        if not validation.valid or validation.response is None:
            raise InvalidStrongModelOutput(validation)
        return validation.response, validation

    @staticmethod
    def _invoke_sync(model: ModelCallable, incident: str, role: str) -> Any:
        try:
            value = model(incident)
        except Exception as exc:
            raise ModelInvocationError(f"{role} model invocation failed") from exc
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise ModelInvocationError(
                f"{role} model returned an awaitable to the synchronous router"
            )
        return value

    def route(self, incident: str) -> RoutedResponse:
        decision = self.complexity_router.decide(incident)
        if decision.route == "strong":
            response, strong_validation = self._valid_strong(
                self._invoke_sync(self.strong_model, incident, "strong")
            )
            metadata = RoutingMetadata(
                initial_route="strong",
                final_route="strong",
                selected_model="strong",
                complexity_score=decision.complexity_score,
                threshold=decision.threshold,
                weak_attempted=False,
                escalated=False,
                escalation_reason=None,
                weak_validation=None,
                strong_validation=strong_validation,
            )
            return RoutedResponse(response=response, metadata=metadata)

        weak_validation: ValidationResult | None = None
        escalation_reason: str | None = None
        try:
            weak_value = self._invoke_sync(self.weak_model, incident, "weak")
            weak_validation = validate_response(weak_value)
            if weak_validation.valid and weak_validation.response is not None:
                if not weak_validation.response.needs_escalation:
                    metadata = RoutingMetadata(
                        initial_route="weak",
                        final_route="weak",
                        selected_model="weak",
                        complexity_score=decision.complexity_score,
                        threshold=decision.threshold,
                        weak_attempted=True,
                        escalated=False,
                        escalation_reason=None,
                        weak_validation=weak_validation,
                        strong_validation=None,
                    )
                    return RoutedResponse(response=weak_validation.response, metadata=metadata)
                escalation_reason = "weak_requested_escalation"
            else:
                issue_codes = ",".join(issue.code for issue in weak_validation.issues)
                escalation_reason = f"weak_validation_failed:{issue_codes}"
        except ModelInvocationError:
            # A weak-tier availability failure is recoverable by design.
            escalation_reason = "weak_invocation_failed"

        response, strong_validation = self._valid_strong(
            self._invoke_sync(self.strong_model, incident, "strong")
        )
        metadata = RoutingMetadata(
            initial_route="weak",
            final_route="weak_then_strong",
            selected_model="strong",
            complexity_score=decision.complexity_score,
            threshold=decision.threshold,
            weak_attempted=True,
            escalated=True,
            escalation_reason=escalation_reason,
            weak_validation=weak_validation,
            strong_validation=strong_validation,
        )
        return RoutedResponse(response=response, metadata=metadata)

    async def aroute(self, incident: str) -> RoutedResponse:
        decision = self.complexity_router.decide(incident)

        async def invoke(model: AsyncModelCallable, role: str) -> Any:
            try:
                value = model(incident)
                return await value if inspect.isawaitable(value) else value
            except Exception as exc:
                raise ModelInvocationError(f"{role} model invocation failed") from exc

        if decision.route == "strong":
            response, strong_validation = self._valid_strong(
                await invoke(self.strong_model, "strong")
            )
            return RoutedResponse(
                response=response,
                metadata=RoutingMetadata(
                    initial_route="strong",
                    final_route="strong",
                    selected_model="strong",
                    complexity_score=decision.complexity_score,
                    threshold=decision.threshold,
                    weak_attempted=False,
                    escalated=False,
                    escalation_reason=None,
                    weak_validation=None,
                    strong_validation=strong_validation,
                ),
            )

        weak_validation: ValidationResult | None = None
        escalation_reason: str
        try:
            weak_validation = validate_response(await invoke(self.weak_model, "weak"))
            if (
                weak_validation.valid
                and weak_validation.response is not None
                and not weak_validation.response.needs_escalation
            ):
                return RoutedResponse(
                    response=weak_validation.response,
                    metadata=RoutingMetadata(
                        initial_route="weak",
                        final_route="weak",
                        selected_model="weak",
                        complexity_score=decision.complexity_score,
                        threshold=decision.threshold,
                        weak_attempted=True,
                        escalated=False,
                        escalation_reason=None,
                        weak_validation=weak_validation,
                        strong_validation=None,
                    ),
                )
            if weak_validation.response is not None and weak_validation.response.needs_escalation:
                escalation_reason = "weak_requested_escalation"
            else:
                codes = ",".join(issue.code for issue in weak_validation.issues)
                escalation_reason = f"weak_validation_failed:{codes}"
        except ModelInvocationError:
            escalation_reason = "weak_invocation_failed"

        response, strong_validation = self._valid_strong(await invoke(self.strong_model, "strong"))
        return RoutedResponse(
            response=response,
            metadata=RoutingMetadata(
                initial_route="weak",
                final_route="weak_then_strong",
                selected_model="strong",
                complexity_score=decision.complexity_score,
                threshold=decision.threshold,
                weak_attempted=True,
                escalated=True,
                escalation_reason=escalation_reason,
                weak_validation=weak_validation,
                strong_validation=strong_validation,
            ),
        )


__all__ = [
    "CascadeRouter",
    "ComplexityRouter",
    "InvalidStrongModelOutput",
    "ModelInvocationError",
    "RouteDecision",
    "RoutedResponse",
    "RoutingMetadata",
]
