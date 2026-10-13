import json
import pytest
from pathlib import Path

from module_auto_healer.models import (
    CodeHunk,
    DiagnosisResult,
    FailureContext,
    VulnerabilityFix,
)
from module_auto_healer.validator import (
    contains_hardcoded_secrets,
    is_safe_target_path,
    validate_patch,
    validate_python_syntax,
)
from module_auto_healer.analyzer import (
    apply_code_hunks,
    apply_vulnerability_fixes,
    diagnose_failure,
    extract_error_snippet,
    extract_referenced_files,
)
from module_auto_healer.healer import (
    build_telegram_heal_message,
    execute_heal_workflow,
    main as healer_main,
    send_telegram_notification,
)


def test_models_are_immutable():
    ctx = FailureContext(
        run_id="2001",
        workflow_name="KTMTicBot CI & EC2 Deploy",
        log_excerpt="Traceback...",
        failed_files=(("ktm_sniper/engine.py", "print('ok')"),),
    )
    with pytest.raises(Exception):
        ctx.run_id = "999"  # type: ignore

    hunk = CodeHunk(target="x = 1", replacement="x = 2")
    with pytest.raises(Exception):
        hunk.target = "y = 1"  # type: ignore


def test_is_safe_target_path_ktm(tmp_path: Path):
    (tmp_path / "ktm_sniper" / "browser").mkdir(parents=True)
    (tmp_path / "ktm_sniper" / "network").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)

    assert is_safe_target_path("ktm_sniper/engine.py", str(tmp_path)) is True
    assert is_safe_target_path("ktm_sniper/browser/driver.py", str(tmp_path)) is True
    assert is_safe_target_path("ktm_sniper/network/session.py", str(tmp_path)) is True
    assert is_safe_target_path("main.py", str(tmp_path)) is True
    assert is_safe_target_path("requirements.txt", str(tmp_path)) is True

    # Disallowed paths
    assert is_safe_target_path("", str(tmp_path)) is False
    assert is_safe_target_path("../secrets.py", str(tmp_path)) is False
    assert is_safe_target_path(".github/workflows/auto_healer.yml", str(tmp_path)) is False
    assert is_safe_target_path("ktm-bot-key.pem", str(tmp_path)) is False
    assert is_safe_target_path(".ktm_key", str(tmp_path)) is False
    assert is_safe_target_path("/etc/passwd", str(tmp_path)) is False


def test_contains_hardcoded_secrets():
    assert contains_hardcoded_secrets("") is False
    assert contains_hardcoded_secrets('k = os.getenv("DEEPSEEK_API_KEY")') is False
    assert contains_hardcoded_secrets('k = "sk-proj-1234567890abcdef1234567890abcdef"') is True
    assert contains_hardcoded_secrets('k = "-----BEGIN RSA PRIVATE KEY-----"') is True


def test_validate_python_syntax_and_patch(tmp_path: Path):
    (tmp_path / "ktm_sniper").mkdir()
    orig = "\n".join([f"val_{i} = {i}" for i in range(20)]) + "\n"
    patched = orig.replace("val_3 = 3", "val_3 = 300")

    ok, msg = validate_patch("ktm_sniper/engine.py", orig, patched, str(tmp_path))
    assert ok is True
    assert msg == "OK"

    assert validate_patch("ktm-bot-key.pem", orig, patched, str(tmp_path))[0] is False
    assert validate_patch("ktm_sniper/engine.py", orig, "  ", str(tmp_path))[0] is False
    assert validate_patch("ktm_sniper/engine.py", orig, orig, str(tmp_path))[0] is False
    assert validate_patch("ktm_sniper/engine.py", orig, "val_0 = 0\n", str(tmp_path))[0] is False
    assert validate_patch(
        "ktm_sniper/engine.py",
        orig,
        orig + 'pem = "-----BEGIN OPENSSH PRIVATE KEY-----"\n',
        str(tmp_path),
    )[0] is False
    assert validate_patch(
        "ktm_sniper/engine.py",
        orig,
        orig + "def broken(\n",
        str(tmp_path),
    )[0] is False


def test_extract_error_snippet_and_referenced_files(tmp_path: Path):
    assert extract_error_snippet("") == ""
    pkg_dir = tmp_path / "ktm_sniper" / "browser"
    pkg_dir.mkdir(parents=True)
    driver_py = pkg_dir / "driver.py"
    driver_py.write_text("import os\nprint('driver')\n", encoding="utf-8")

    raw_log = (
        "2026-10-13T00:00:01.1234567Z Traceback (most recent call last):\n"
        '2026-10-13T00:00:02.1234567Z   File "/app/ktm_sniper/browser/driver.py", line 10, in lock_seat\n'
        "2026-10-13T00:00:03.1234567Z TimeoutError: waiting for #confirmSeatBtn\n"
        "2026-10-13T00:00:04.1234567Z ##[error]Process completed with exit code 1.\n"
        "2026-10-13T00:00:05.1234567Z Post job cleanup.\n"
    )
    snippet = extract_error_snippet(raw_log)
    assert "confirmSeatBtn" in snippet
    assert "Post job cleanup" not in snippet

    files = extract_referenced_files(snippet, str(tmp_path))
    assert len(files) == 1
    assert files[0][0] == "ktm_sniper/browser/driver.py"


def test_apply_vulnerability_fixes():
    req_txt = "curl_cffi>=0.15.0\nrequests>=2.31.0\nplaywright>=1.40.0\n"
    audit_payload = json.dumps({
        "dependencies": [
            {
                "name": "requests",
                "version": "2.31.0",
                "vulns": [{"id": "CVE-2024-35195", "fix_versions": ["2.32.3"]}],
            }
        ]
    })
    new_req, fixes = apply_vulnerability_fixes(req_txt, audit_payload)
    assert len(fixes) == 1
    assert "requests>=2.32.3" in new_req


def test_diagnose_and_execute_heal_workflow(tmp_path: Path):
    pkg_dir = tmp_path / "ktm_sniper"
    pkg_dir.mkdir()
    target_py = pkg_dir / "reserver.py"
    orig_code = "\n".join([f"line_{i} = {i}" for i in range(15)]) + "\n"
    target_py.write_text(orig_code, encoding="utf-8")

    raw_log = (
        "Traceback (most recent call last):\n"
        '  File "ktm_sniper/reserver.py", line 5, in <module>\n'
        "ValueError: invalid seat_preference\n"
    )

    outcome = execute_heal_workflow(
        run_id="901",
        workflow_name="KTMTicBot CI & EC2 Deploy",
        raw_log=raw_log,
        project_root=str(tmp_path),
        api_key="dummy",
        llm_responder=lambda _s, _u: json.dumps({
            "category": "CODE_FIX",
            "root_cause": "Fixed line_5 in ktm_sniper/reserver.py",
            "target_file": "ktm_sniper/reserver.py",
            "patches": [{"target": "line_5 = 5", "replacement": "line_5 = 555"}],
        }),
    )
    assert outcome["action"] == "CODE_FIXED"
    assert "line_5 = 555" in target_py.read_text(encoding="utf-8")

    msg = build_telegram_heal_message("KTMTicBot CI & EC2 Deploy", outcome)
    assert "ktm_sniper/reserver.py" in msg
    assert send_telegram_notification("", "", msg) is False


def test_cli_modes(tmp_path: Path, monkeypatch):
    gh_out = tmp_path / "gh_out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    req_file = tmp_path / "requirements.txt"
    req_file.write_text("requests>=2.31.0\n", encoding="utf-8")
    audit_file = tmp_path / "audit.json"
    audit_file.write_text(
        json.dumps({
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.31.0",
                    "vulns": [{"id": "CVE-1", "fix_versions": ["2.32.3"]}],
                }
            ]
        }),
        encoding="utf-8",
    )

    rc = healer_main([
        "--mode", "audit-fix",
        "--requirements-file", str(req_file),
        "--audit-json-file", str(audit_file),
    ])
    assert rc == 0
    assert "requests>=2.32.3" in req_file.read_text(encoding="utf-8")

    rc2 = healer_main([
        "--mode", "heal-run",
        "--run-id", "888",
        "--workflow-name", "KTMTicBot",
        "--error-text", "RuntimeError: test error",
        "--project-root", str(tmp_path),
    ])
    assert rc2 == 0
    assert "action=" in gh_out.read_text(encoding="utf-8")
