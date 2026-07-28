def test_tool_dispatch_checks_registry_schema_and_permissions_before_local_execution():
    from app.services.tool_dispatch import (
        ToolCallRequest,
        ToolDefinition,
        ToolDispatcher,
    )

    received: list[dict[str, object]] = []
    dispatcher = ToolDispatcher(
        [
            ToolDefinition(
                tool_id="text.preview",
                input_schema={
                    "type": "object",
                    "required": ["text"],
                    "properties": {
                        "text": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
                required_permissions=("workspace.read",),
                handler=lambda arguments: received.append(arguments)
                or {"preview": arguments["text"][:4]},
            )
        ]
    )

    completed = dispatcher.dispatch(
        ToolCallRequest(
            tool_id="text.preview",
            arguments={"text": "hello"},
            granted_permissions=("workspace.read",),
        )
    )
    missing_tool = dispatcher.dispatch(
        ToolCallRequest(
            tool_id="missing.tool",
            arguments={},
            granted_permissions=("workspace.read",),
        )
    )
    invalid_arguments = dispatcher.dispatch(
        ToolCallRequest(
            tool_id="text.preview",
            arguments={"text": 42, "task_state": "completed"},
            granted_permissions=("workspace.read",),
        )
    )
    denied = dispatcher.dispatch(
        ToolCallRequest(tool_id="text.preview", arguments={"text": "hello"})
    )

    assert completed.status == "completed"
    assert completed.output == {"preview": "hell"}
    assert completed.error is None
    assert received == [{"text": "hello"}]
    assert missing_tool.status == "failed"
    assert missing_tool.error.code == "tool_not_found"
    assert invalid_arguments.status == "failed"
    assert invalid_arguments.error.code == "invalid_arguments"
    assert invalid_arguments.error.details["errors"] == [
        "$.text: expected string",
        "$.task_state: additional property rejected",
    ]
    assert denied.status == "failed"
    assert denied.error.code == "permission_denied"
    assert received == [{"text": "hello"}]


def test_harness_facade_dispatches_provider_tool_request_with_orchestrator_permissions(
    tmp_path,
):
    from types import SimpleNamespace

    from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest
    from app.services.provider_adapters.contracts import (
        ProviderCapabilities,
        ProviderSession,
    )
    from app.services.tool_dispatch import ToolDefinition, ToolDispatcher

    class ToolCallingAdapter:
        def capabilities(self):
            return ProviderCapabilities(
                streaming=True,
                tool_call=True,
                session_resume=False,
                structured_output=False,
                mcp=False,
                skills=False,
                cancellation=False,
            )

        def prepare(self, request):
            return ProviderSession(
                session_id="tool-session",
                provider=request.provider,
                artifact_dir=str(tmp_path),
            )

        def execute(self, session, *, event_sink, **_kwargs):
            tool_result = event_sink(
                "tool_requested",
                {
                    "tool_id": "text.preview",
                    "arguments": {"text": "hello"},
                    "granted_permissions": ["provider.must.not.self_grant"],
                },
            )
            return SimpleNamespace(
                session_id=session.session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-28T00:00:00+00:00",
                completed_at="2026-07-28T00:00:01+00:00",
                duration_ms=1000,
                timed_out=False,
                error="",
                provider_diagnostics={"tool_result": tool_result},
            )

        def collect_artifacts(self, _session):
            return []

        def record_raw_output(self, *_args, **_kwargs):
            return None

    dispatcher = ToolDispatcher([
        ToolDefinition(
            tool_id="text.preview",
            input_schema={
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False,
            },
            required_permissions=("workspace.read",),
            handler=lambda arguments: {"preview": arguments["text"][:4]},
        )
    ])
    facade = AgentHarnessFacade(
        tmp_path,
        adapter=ToolCallingAdapter(),
        tool_dispatcher=dispatcher,
        granted_tool_permissions=("workspace.read",),
    )
    session = facade.prepare(HarnessRunRequest(
        provider="builtin",
        command=[],
        cwd=str(tmp_path),
        workflow_snapshot={},
        task_bundle={},
    ))
    events: list[tuple[str, dict]] = []

    result = facade.execute(
        session,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result.status == "completed"
    assert result.provider_diagnostics["tool_result"] == {
        "tool_id": "text.preview",
        "status": "completed",
        "output": {"preview": "hell"},
        "error": None,
    }
    assert any(event_type == "tool_requested" for event_type, _ in events)
    assert any(
        event_type == "tool_completed"
        and payload["tool_id"] == "text.preview"
        for event_type, payload in events
    )


def test_provider_tool_call_reuses_attempt_journal_instead_of_repeating_effect(
    tmp_path,
):
    from types import SimpleNamespace

    from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest
    from app.services.provider_adapters.contracts import ProviderCapabilities, ProviderSession
    from app.services.tool_action_journal import ToolActionContext, ToolActionJournal
    from app.services.tool_dispatch import ToolDefinition, ToolDispatcher

    effects = 0

    class Adapter:
        def capabilities(self):
            return ProviderCapabilities(
                streaming=True,
                tool_call=True,
                session_resume=False,
                structured_output=False,
                mcp=False,
                skills=False,
                cancellation=False,
            )

        def prepare(self, request):
            return ProviderSession(
                session_id="provider-session",
                provider=request.provider,
                artifact_dir=str(tmp_path),
            )

        def execute(self, session, *, event_sink, **_kwargs):
            tool_result = event_sink(
                "tool_requested",
                {
                    "tool_call_id": "call-1",
                    "tool_id": "state.increment",
                    "arguments": {"amount": 1},
                },
            )
            return SimpleNamespace(
                session_id=session.session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-28T00:00:00+00:00",
                completed_at="2026-07-28T00:00:01+00:00",
                duration_ms=1000,
                timed_out=False,
                error="",
                provider_diagnostics={"tool_result": tool_result},
            )

        def collect_artifacts(self, _session):
            return []

        def record_raw_output(self, *_args, **_kwargs):
            return None

    def increment(arguments):
        nonlocal effects
        effects += arguments["amount"]
        return {"value": effects}

    dispatcher = ToolDispatcher([
        ToolDefinition(
            tool_id="state.increment",
            input_schema={
                "type": "object",
                "required": ["amount"],
                "properties": {"amount": {"type": "integer"}},
                "additionalProperties": False,
            },
            required_permissions=("state.write",),
            handler=increment,
        )
    ])
    context = ToolActionContext(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="agent-1",
    )

    outputs = []
    for _ in range(2):
        facade = AgentHarnessFacade(
            tmp_path,
            adapter=Adapter(),
            tool_dispatcher=dispatcher,
            granted_tool_permissions=("state.write",),
            tool_action_journal=ToolActionJournal(tmp_path),
            tool_action_context=context,
        )
        session = facade.prepare(HarnessRunRequest(
            provider="builtin",
            command=[],
            cwd=str(tmp_path),
            workflow_snapshot={},
            task_bundle={},
        ))
        outputs.append(facade.execute(session).provider_diagnostics["tool_result"])

    assert effects == 1
    assert [item["output"] for item in outputs] == [
        {"value": 1},
        {"value": 1},
    ]


def test_provider_tool_call_without_stable_id_fails_closed_before_dispatch(tmp_path):
    from types import SimpleNamespace

    from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest
    from app.services.provider_adapters.contracts import ProviderCapabilities, ProviderSession
    from app.services.tool_action_journal import ToolActionContext, ToolActionJournal
    from app.services.tool_dispatch import ToolDefinition, ToolDispatcher

    effects = 0

    class Adapter:
        def capabilities(self):
            return ProviderCapabilities(
                streaming=True,
                tool_call=True,
                session_resume=False,
                structured_output=False,
                mcp=False,
                skills=False,
                cancellation=False,
            )

        def prepare(self, request):
            return ProviderSession(
                session_id="provider-session",
                provider=request.provider,
                artifact_dir=str(tmp_path),
            )

        def execute(self, session, *, event_sink, **_kwargs):
            tool_result = event_sink(
                "tool_requested",
                {"tool_id": "state.increment", "arguments": {"amount": 1}},
            )
            return SimpleNamespace(
                session_id=session.session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-28T00:00:00+00:00",
                completed_at="2026-07-28T00:00:01+00:00",
                duration_ms=1000,
                timed_out=False,
                error="",
                provider_diagnostics={"tool_result": tool_result},
            )

        def collect_artifacts(self, _session):
            return []

        def record_raw_output(self, *_args, **_kwargs):
            return None

    def increment(arguments):
        nonlocal effects
        effects += arguments["amount"]
        return {"value": effects}

    dispatcher = ToolDispatcher([
        ToolDefinition(
            tool_id="state.increment",
            input_schema={
                "type": "object",
                "required": ["amount"],
                "properties": {"amount": {"type": "integer"}},
                "additionalProperties": False,
            },
            required_permissions=("state.write",),
            handler=increment,
        )
    ])
    facade = AgentHarnessFacade(
        tmp_path,
        adapter=Adapter(),
        tool_dispatcher=dispatcher,
        granted_tool_permissions=("state.write",),
        tool_action_journal=ToolActionJournal(tmp_path),
        tool_action_context=ToolActionContext(
            task_id="task-1",
            attempt_id="attempt-1",
            node_id="agent-1",
        ),
    )
    session = facade.prepare(HarnessRunRequest(
        provider="builtin",
        command=[],
        cwd=str(tmp_path),
        workflow_snapshot={},
        task_bundle={},
    ))

    result = facade.execute(session).provider_diagnostics["tool_result"]

    assert effects == 0
    assert result["status"] == "failed"
    assert result["error"]["code"] == "tool_call_id_required"
    assert not (tmp_path / "tool-actions").exists()
