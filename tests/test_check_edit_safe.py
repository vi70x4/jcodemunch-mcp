"""Tests for check_edit_safe — edit-safety preflight composite tool."""

from pathlib import Path

from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe
from jcodemunch_mcp.tools.index_folder import index_folder


def _make_repo(tmp_path: Path, files: dict) -> tuple[str, str]:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    storage = str(tmp_path / ".index")
    result = index_folder(str(tmp_path), use_ai_summaries=False, storage_path=storage)
    repo_id = result.get("repo", str(tmp_path))
    return repo_id, storage


# orphan_func: trivial, no callers, no tests → free to edit.
# used_func: imported by consumer.py → signature contract matters.
_SIGNATURE_REPO = {
    "lonely.py": "def orphan_func():\n    return 'nobody calls me'\n",
    "used.py": "def used_func():\n    return 1\n",
    "consumer.py": (
        "from used import used_func\n\n"
        "def consume():\n    return used_func() + 1\n"
    ),
}

# tangled(): a pile of branches → high cyclomatic complexity, no callers.
_COMPLEX_REPO = {
    "tangled.py": (
        "def tangled(x):\n"
        + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(14))
        + "    return -1\n"
    ),
}

# helper_function is referenced only by a test file → covered, safe to edit.
_TEST_COVERED_REPO = {
    "lib.py": "def helper_function():\n    return 42\n",
    "tests/test_lib.py": (
        "from lib import helper_function\n\n"
        "def test_helper_function():\n    assert helper_function() == 42\n"
    ),
}


class TestCheckEditSafeSignatureImpact:
    def test_orphan_is_safe_to_edit(self, tmp_path):
        repo, storage = _make_repo(tmp_path, _SIGNATURE_REPO)
        result = check_edit_safe(repo, symbol="orphan_func", storage_path=storage)
        assert "error" not in result, result
        assert result["verdict"] == "safe_to_edit"
        assert result["confidence"] >= 0.7

    def test_imported_symbol_flags_signature_impact(self, tmp_path):
        repo, storage = _make_repo(tmp_path, _SIGNATURE_REPO)
        result = check_edit_safe(repo, symbol="used_func", storage_path=storage)
        assert "error" not in result, result
        # consumer.py imports it → editing the signature breaks a caller.
        assert result["verdict"] in {"signature_impact", "runtime_critical"}
        assert result["confidence"] <= 0.6
        assert result["signals"]["external_import_count"] >= 1


class TestCheckEditSafeComplexity:
    def test_high_complexity_flagged(self, tmp_path):
        repo, storage = _make_repo(tmp_path, _COMPLEX_REPO)
        result = check_edit_safe(repo, symbol="tangled", storage_path=storage)
        assert "error" not in result, result
        # No callers, so complexity is the headline (or at least a blocker).
        assert (
            result["verdict"] == "complexity_risk"
            or any(b.get("kind") == "high_complexity" for b in result["blockers"])
        )
        assert result["signals"]["cyclomatic"] >= 11


class TestCheckEditSafeTestCoverage:
    def test_test_covered_symbol_reports_coverage(self, tmp_path):
        repo, storage = _make_repo(tmp_path, _TEST_COVERED_REPO)
        result = check_edit_safe(repo, symbol="helper_function", storage_path=storage)
        assert "error" not in result, result
        assert result["signals"]["has_test_coverage"] is True
        # Test-only consumption is not an external signature dependency.
        assert result["signals"]["external_import_count"] == 0


class TestCheckEditSafeContract:
    def test_unknown_symbol_errors(self, tmp_path):
        repo, storage = _make_repo(tmp_path, _SIGNATURE_REPO)
        result = check_edit_safe(repo, symbol="does_not_exist", storage_path=storage)
        assert "error" in result

    def test_result_shape_and_readonly(self, tmp_path):
        repo, storage = _make_repo(tmp_path, _SIGNATURE_REPO)
        before = (tmp_path / "used.py").read_text(encoding="utf-8")
        result = check_edit_safe(repo, symbol="used_func", storage_path=storage)
        # Required keys present.
        for key in ("verdict", "confidence", "target", "blockers",
                    "recommended_action", "signals"):
            assert key in result, key
        assert isinstance(result["blockers"], list)
        assert len(result["blockers"]) <= 5
        # Read-only: the source file is untouched.
        assert (tmp_path / "used.py").read_text(encoding="utf-8") == before
