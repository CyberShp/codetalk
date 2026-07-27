from __future__ import annotations


def test_iscsi_login_dispatch_chain_precedes_generic_parameter_helpers():
    from app.services.flow_evidence import _prioritize_flow_symbols

    symbols = [
        "iscsi_param_find",
        "iscsi_param_add",
        "iscsi_op_login_store_incoming_params",
        "iscsi_pdu_payload_op_login",
        "iscsi_op_login_response",
        "iscsi_read_pdu",
    ]

    ranked = _prioritize_flow_symbols(
        symbols,
        analysis_target="完整 iSCSI Login 灰盒测试设计：覆盖 CHAP、超时和恢复",
    )

    assert ranked[:4] == [
        "iscsi_pdu_payload_op_login",
        "iscsi_op_login_response",
        "iscsi_read_pdu",
        "iscsi_op_login_store_incoming_params",
    ]
