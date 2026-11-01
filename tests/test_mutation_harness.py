"""The mutation gate must reject a surviving defect and an invalid test run."""
import importlib.util
import json
import os
import py_compile
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def harness(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "tools" / "mutation_test.py"
    spec = importlib.util.spec_from_file_location("mutation_harness_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "SRC", tmp_path)
    source = tmp_path / "example.py"
    source.write_bytes(b"def value():\r\n    return 1\r\n")
    (tmp_path / "test_example.py").write_text("from example import value\n\ndef test_value():\n    assert value() == 1\n")
    return module, source


@pytest.mark.parametrize("mid,needle,replacement,code,status", [
    ("M1", "return 1", "return 2", 0, "killed"),
    ("M1", "return 1", "return 1 # survives", 1, "SURVIVED"),
    ("M1", "return 999", "return 2", 1, "NOT APPLIED (matches=0)"),
    ("M1", "return 1", "return (", 1, "INCONCLUSIVE"),
    ("E1", "return 1", "return 1 # equivalent", 0, "equivalent (survived, as expected)"),
    ("E1", "return 1", "return 2", 1, "killed: NOT equivalent, reclassify"),
])
def test_real_pytest_outcomes_control_exit_and_restore_source(harness, monkeypatch,
                                                              mid, needle, replacement, code, status):
    module, source = harness
    original = source.read_bytes()
    monkeypatch.setattr(module, "MUTANTS", [(mid, source.name, needle, replacement, "control")])
    report = source.parent / "report.json"
    assert module.main(["--out", str(report)]) == code
    assert source.read_bytes() == original
    result = json.loads(report.read_text())
    assert result["passed"] == (code == 0)
    assert result["mutants"][0]["status"] == status
    if status == "INCONCLUSIVE":
        assert result["killed"] == 0 and result["total"] == 1


@pytest.mark.parametrize("exit_code,status", [(0, "passed"), (1, "failed"), (2, "error"),
                                                (3, "error"), (4, "error"), (5, "error"), (-9, "error")])
def test_only_assertion_failure_is_a_kill(harness, monkeypatch, exit_code, status):
    module, _ = harness
    def run(args, **kwargs):
        report = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--junitxml=")))
        result = '<failure message="assertion" />' if exit_code == 1 else ""
        report.write_text(f"<testsuites><testsuite><testcase>{result}</testcase></testsuite></testsuites>")
        return subprocess.CompletedProcess(args, exit_code, stdout="test output", stderr="")
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.run_tests(5)[0] == status


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_real_fixture_errors_do_not_count_as_kills(harness, monkeypatch, phase):
    module, source = harness
    setup = "    if value() == 2: raise RuntimeError('fixture unavailable')\n"
    fixture = setup + "    yield\n" if phase == "setup" else "    yield\n" + setup
    (source.parent / "test_example.py").write_text(
        "import pytest\nfrom example import value\n@pytest.fixture(autouse=True)\ndef dependency():\n"
        + fixture + "\ndef test_value():\n    assert value() in (1, 2)\n")
    monkeypatch.setattr(module, "MUTANTS", [("M1", source.name, "return 1", "return 2", "control")])
    original = source.read_bytes()
    output = source.parent / "report.json"
    assert module.main(["--out", str(output)]) == 1
    result = json.loads(output.read_text())
    assert result["inconclusive"] == ["M1"] and result["killed"] == 0
    assert source.read_bytes() == original


@pytest.mark.parametrize("xml", [None, "not XML", "<testsuites />",
                                   "<testsuites><testcase><skipped /></testcase></testsuites>",
                                   "<testsuites><testcase><failure/><error/></testcase></testsuites>"])
def test_missing_invalid_or_unexecuted_evidence_is_not_a_kill(harness, monkeypatch, xml):
    module, _ = harness
    def run(args, **kwargs):
        if xml is not None:
            report = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--junitxml=")))
            report.write_text(xml)
        return subprocess.CompletedProcess(args, 1, stdout="failed", stderr="")
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.run_tests(5)[0] == "error"


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("pytest", 1), OSError("cannot execute")])
def test_timeout_and_startup_error_are_inconclusive(harness, monkeypatch, error):
    module, _ = harness
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(module.subprocess, "run", fail)
    assert module.run_tests(1)[0] == "error"


def test_same_size_same_timestamp_mutation_cannot_read_stale_bytecode(harness):
    module, source = harness
    stamp = source.stat()
    py_compile.compile(str(source), doraise=True)
    original = source.read_bytes()
    changed = original.replace(b"return 1", b"return 2")
    assert len(changed) == len(original)
    source.write_bytes(changed)
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert module.run_tests(10)[0] == "failed"


@pytest.mark.parametrize("option", [["--only", "typo"], ["--only", ""], ["--timeout", "0"]])
def test_bad_selection_is_rejected_before_baseline(harness, monkeypatch, option):
    module, _ = harness
    monkeypatch.setattr(module, "run_tests", lambda *a: pytest.fail("baseline must not run"))
    with pytest.raises(SystemExit) as exc:
        module.main(option)
    assert exc.value.code == 2


def test_failed_baseline_preserves_source_and_existing_report(harness, monkeypatch):
    module, source = harness
    original = source.read_bytes()
    (source.parent / "test_example.py").write_text("def test_baseline():\n    assert False\n")
    report = source.parent / "report.json"
    report.write_text("prior result")
    assert module.main(["--out", str(report)]) == 2
    assert source.read_bytes() == original and report.read_text() == "prior result"


def test_keyboard_interrupt_restores_source(harness, monkeypatch):
    module, source = harness
    original = source.read_bytes()
    monkeypatch.setattr(module, "MUTANTS", [("M1", source.name, "return 1", "return 2", "control")])
    outcomes = iter([("passed", 0, "baseline"), KeyboardInterrupt()])
    def run(*a):
        result = next(outcomes)
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(module, "run_tests", run)
    with pytest.raises(KeyboardInterrupt):
        module.main(["--only", "M1"])
    assert source.read_bytes() == original
