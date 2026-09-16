"""CLI 测试：``check`` 子命令必须真的把所有主要模块跑通并返回 0。"""

from __future__ import annotations

import json

import pytest

from cli import build_parser, main


def test_parser_exposes_documented_subcommands():
    parser = build_parser()
    for command in ("check", "demo", "serve"):
        args = parser.parse_args([command])
        assert callable(args.func)


def test_unknown_subcommand_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["nope"])


def test_check_self_test_passes(capsys):
    """这是最完整的集成测试：配置、本体、路由、工具四条探针 + 端到端链路。"""
    exit_code = main(["check"])
    output = capsys.readouterr().out

    assert exit_code == 0, output
    assert "自检通过" in output

    # 关键探针结果必须出现在输出里
    assert "high_risk_without_approval" in output
    assert "idempotent_replay_same_result" in output
    assert "端到端" in output
    assert '"status": "completed"' in output

    # 不能出现任何 BUG 标记
    assert "BUG:" not in output


def test_check_reports_trace_hierarchy(capsys):
    main(["check"])
    output = capsys.readouterr().out
    assert "span_count" in output
    assert "max_depth" in output
    # 扁平 Trace 是旧实现的核心缺陷，这里断言层级确实存在
    assert '"max_depth": 4' in output or '"max_depth": 5' in output


def test_check_output_is_valid_json_sections(capsys):
    main(["check"])
    output = capsys.readouterr().out
    assert json.loads(output.split("=== 本体 ===")[1].split("\n\n===")[0].strip())["version"] == "3.5.0"
