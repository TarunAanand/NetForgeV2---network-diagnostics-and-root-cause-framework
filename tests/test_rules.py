from analysis.context import AnalysisContext
from analysis.engine import RuleEngine
from analysis.rules.gateway_rules import NoDefaultGatewayRule
from analysis.rules.path_rules import PathChangeRule
from core.result import DiagnosticResult, DiagnosticStatus, Severity


def _result(**kwargs) -> DiagnosticResult:
    defaults = dict(
        module="test",
        category="host",
        status=DiagnosticStatus.HEALTHY,
        severity=Severity.INFO,
        summary="ok",
    )
    defaults.update(kwargs)
    return DiagnosticResult(**defaults)


def test_no_default_gateway_rule():
    results = [
        _result(
            module="interface",
            target="eth0",
            metrics={"is_up": True},
        ),
        _result(
            module="routing",
            status=DiagnosticStatus.FAILED,
            severity=Severity.CRITICAL,
            summary="no gw",
            metrics={"default_gateway": None},
        ),
    ]
    issue = NoDefaultGatewayRule().evaluate(AnalysisContext(results))
    assert issue is not None
    assert issue.rule_id == "RULE_NO_DEFAULT_GATEWAY"


def test_path_change_rule():
    results = [
        _result(
            module="path_change",
            category="path",
            status=DiagnosticStatus.DEGRADED,
            severity=Severity.MEDIUM,
            summary="changed",
            metrics={"changed": True, "path_fingerprint": "abc"},
            evidence=["fingerprint changed"],
        )
    ]
    issue = PathChangeRule().evaluate(AnalysisContext(results))
    assert issue is not None
    assert issue.rule_id == "RULE_PATH_CHANGE"


def test_rule_engine_logs_failures_without_crash():
    class BoomRule:
        rule_id = "RULE_BOOM"
        name = "Boom"
        category = "Test"

        def evaluate(self, ctx):
            raise RuntimeError("boom")

    engine = RuleEngine(rules=[BoomRule()])  # type: ignore[arg-type]
    report = engine.analyze([])
    assert report.status == DiagnosticStatus.HEALTHY
    assert engine.rule_errors
    assert engine.rule_errors[0]["rule_id"] == "RULE_BOOM"
