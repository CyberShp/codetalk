"""Registry for domain-neutral, read-only Validator implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .artifact_exists import validate_artifact_exists
from .contracts import ValidationResult
from .json_schema import validate_json_schema
from .source_evidence import validate_source_evidence


ValidatorHandler = Callable[..., ValidationResult]


@dataclass(frozen=True)
class ValidatorDefinition:
    validator_id: str
    handler: ValidatorHandler
    read_only: bool = True


class ValidatorRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ValidatorDefinition] = {}

    def register(self, definition: ValidatorDefinition) -> None:
        if definition.validator_id in self._definitions:
            raise ValueError(f"Validator already registered: {definition.validator_id}")
        if not definition.read_only:
            raise ValueError("Base Validator registry only accepts read-only handlers")
        self._definitions[definition.validator_id] = definition

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def get(self, validator_id: str) -> ValidatorDefinition:
        try:
            return self._definitions[validator_id]
        except KeyError as exc:
            raise KeyError(f"Unknown Validator: {validator_id}") from exc

    def run(self, validator_id: str, **kwargs: object) -> ValidationResult:
        definition = self.get(validator_id)
        result = definition.handler(**kwargs)
        if not isinstance(result, ValidationResult):
            raise TypeError(f"Validator {validator_id} returned an invalid result")
        return result


DEFAULT_VALIDATOR_REGISTRY = ValidatorRegistry()
for _validator_id, _handler in (
    ("artifact_exists", validate_artifact_exists),
    ("json_schema", validate_json_schema),
    ("source_evidence", validate_source_evidence),
):
    DEFAULT_VALIDATOR_REGISTRY.register(
        ValidatorDefinition(validator_id=_validator_id, handler=_handler)
    )

