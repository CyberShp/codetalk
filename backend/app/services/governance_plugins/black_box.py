"""Read-only black-box test design professional validator."""

from __future__ import annotations

from app.services.governance_plugins._legacy_validation import validate_json_artifact
from app.services.governance_plugins.contracts import GovernancePluginRequest


class BlackBoxValidatorPlugin:
    def execute(self, request: GovernancePluginRequest):
        return validate_json_artifact(request, artifact_name="black_box_cases.json")


def create_plugin() -> BlackBoxValidatorPlugin:
    return BlackBoxValidatorPlugin()
