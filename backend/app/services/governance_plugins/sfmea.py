"""Read-only SFMEA professional validator."""

from __future__ import annotations

from app.services.governance_plugins._legacy_validation import validate_json_artifact
from app.services.governance_plugins.contracts import GovernancePluginRequest


class SfmeaValidatorPlugin:
    def execute(self, request: GovernancePluginRequest):
        return validate_json_artifact(request, artifact_name="sfmea.json")


def create_plugin() -> SfmeaValidatorPlugin:
    return SfmeaValidatorPlugin()
