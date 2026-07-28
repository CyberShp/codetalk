"""Public contracts for the domain-neutral Validator layer."""

from .common import validate_required_output_subset
from .contracts import ValidationIssue, ValidationResult
from .registry import (
    DEFAULT_VALIDATOR_REGISTRY,
    ValidatorDefinition,
    ValidatorRegistry,
)

__all__ = [
    "DEFAULT_VALIDATOR_REGISTRY",
    "ValidationIssue",
    "ValidationResult",
    "ValidatorDefinition",
    "ValidatorRegistry",
    "validate_required_output_subset",
]
