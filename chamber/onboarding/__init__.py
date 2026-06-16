"""Real-service onboarding contracts for Phase 10."""

from chamber.onboarding.external_translation import (
    build_external_translation_onboarding_plan,
    validate_external_translation_environment,
)
from chamber.onboarding.generic import (
    EvidenceAttribution,
    ExternalDependencyPolicy,
    FollowUpCheck,
    ImageBuildPlan,
    ImageBuildSpec,
    ImageReplacement,
    LivePreflightResult,
    OnboardingSpec,
    OnboardingValidationError,
    RealServiceOnboardingPlan,
    RedactedConfigEntry,
    TrafficJourney,
    WorkloadPlan,
    WorkloadSpec,
    build_onboarding_plan,
    preflight_live_onboarding,
    validate_onboarding_environment,
)

__all__ = [
    "EvidenceAttribution",
    "ExternalDependencyPolicy",
    "FollowUpCheck",
    "ImageBuildPlan",
    "ImageBuildSpec",
    "ImageReplacement",
    "LivePreflightResult",
    "OnboardingSpec",
    "OnboardingValidationError",
    "RealServiceOnboardingPlan",
    "RedactedConfigEntry",
    "TrafficJourney",
    "WorkloadPlan",
    "WorkloadSpec",
    "build_external_translation_onboarding_plan",
    "build_onboarding_plan",
    "preflight_live_onboarding",
    "validate_external_translation_environment",
    "validate_onboarding_environment",
]
