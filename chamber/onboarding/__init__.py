"""Real-service onboarding contracts for Phase 10."""

from chamber.onboarding.external_translation import (
    ExternalDependencyPolicy,
    ImageBuildPlan,
    OnboardingValidationError,
    RealServiceOnboardingPlan,
    RedactedConfigEntry,
    WorkloadPlan,
    build_external_translation_onboarding_plan,
    validate_external_translation_environment,
)

__all__ = [
    "ExternalDependencyPolicy",
    "ImageBuildPlan",
    "OnboardingValidationError",
    "RealServiceOnboardingPlan",
    "RedactedConfigEntry",
    "WorkloadPlan",
    "build_external_translation_onboarding_plan",
    "validate_external_translation_environment",
]
