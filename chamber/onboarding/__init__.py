"""Real-service onboarding contracts for Phase 10."""

from chamber.onboarding.tara2 import (
    ExternalDependencyPolicy,
    ImageBuildPlan,
    OnboardingValidationError,
    RealServiceOnboardingPlan,
    RedactedConfigEntry,
    WorkloadPlan,
    build_tara2_onboarding_plan,
    validate_tara2_environment,
)

__all__ = [
    "ExternalDependencyPolicy",
    "ImageBuildPlan",
    "OnboardingValidationError",
    "RealServiceOnboardingPlan",
    "RedactedConfigEntry",
    "WorkloadPlan",
    "build_tara2_onboarding_plan",
    "validate_tara2_environment",
]
