"""Native source promotion preserves history and rejects changed review contracts."""
import importlib.util
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True,
                      capture_output=True, text=True).stdout.strip()
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("module_update_operator", ROOT / "scripts/module_update.py")
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)
PREFIX = "instruments/measurement/calibration"


@pytest.fixture
def draft(tmp_path):
    # Objects are immutable and shared; refs, index, identity and worktrees are
    # exclusively owned by this test. Never audit the development checkout.
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix="promotion-") as temporary:
        root = Path(temporary) / "repo"
        subprocess.run(["git", "clone", "--quiet", "--shared", "--config", "core.longpaths=true",
                        "--config", "core.autocrlf=false",
                        "--config", "gc.auto=0", "--config", "maintenance.auto=false",
                        str(ROOT), str(root)], check=True)
        operator._git(root, "checkout", "--quiet", "-B", "draft-module", BASE)
        operator._git(root, "config", "user.name", "Promotion Fixture")
        operator._git(root, "config", "user.email", "promotion@example.invalid")
        path = root / PREFIX / "README.md"
        path.write_bytes(path.read_bytes() + b"\nA harmless documented source revision.\n")
        _commit(root)
        yield root


def _commit(root):
    operator._git(root, "add", "--all")
    operator._git(root, "commit", "--quiet", "-m", "One module source draft")


def _prepare(root, tmp_path, **kwargs):
    path = tmp_path / "plan.json"
    return operator.prepare(root, kwargs.get("role", "mcur"), kwargs.get("base", BASE),
                            kwargs.get("branch", "review/calibration"), path), path


def _rewrite(path, plan):
    path.write_text(json.dumps(plan))


def test_prepare_and_apply_retains_native_history_without_moving_caller(draft, tmp_path):
    before = operator._text(draft, "rev-parse", "HEAD")
    index = operator._git(draft, "ls-files", "--stage", "-z")
    refs = operator._git(draft, "show-ref")
    plan, path = _prepare(draft, tmp_path)
    assert operator._git(draft, "show-ref") == refs
    assert operator._text(draft, "rev-parse", "HEAD") == before
    assert operator._git(draft, "ls-files", "--stage", "-z") == index
    old = json.loads(operator._git(draft, "show", BASE + ":instruments/manifest.json"))["modules"][0]
    tree, parents, _ = operator._commit(draft, plan["source_revision"])
    assert tree == operator._text(draft, "rev-parse", before + ":" + PREFIX)
    assert parents == [old["import_revision"]]
    _, parents, _ = operator._commit(draft, plan["candidate_revision"])
    assert parents == [before, plan["source_revision"]]
    assert plan["preparation_id"] != plan["source_audit"]["verification_id"]
    updated = json.loads(operator._git(draft, "show", plan["candidate_revision"] + ":instruments/manifest.json"))
    assert updated["modules"][0]["runtime_revision"] == old["runtime_revision"]
    result = operator.apply(draft, path)
    assert result["status"] == "published"
    assert result["source_audit"]["verification_id"] != plan["source_audit"]["verification_id"]
    assert result["preparation_source_audit_verification_id"] == plan["source_audit"]["verification_id"]
    assert operator._text(draft, "rev-parse", "review/calibration") == plan["candidate_revision"]
    assert operator._text(draft, "rev-parse", "HEAD") == before
    assert operator._git(draft, "ls-files", "--stage", "-z") == index
    assert result["admission"] == result["composition_qualification"] == result["package_qualification"] == "not_performed"
    assert len(operator._git(draft, "worktree", "list", "--porcelain").split(b"worktree ")) == 2


def test_linked_draft_worktree_uses_its_own_HEAD_guard_and_shared_review_ref(draft, tmp_path):
    linked = tmp_path / "linked"
    operator._git(draft, "worktree", "add", "-b", "linked-draft", str(linked), "HEAD")
    try:
        plan, path = _prepare(linked, tmp_path)
        assert plan["root"] == str(linked.resolve())
        assert operator.apply(linked, path)["status"] == "published"
        assert operator._text(linked, "symbolic-ref", "HEAD") == "refs/heads/linked-draft"
        assert operator._text(draft, "symbolic-ref", "HEAD") == "refs/heads/draft-module"
        assert operator._text(draft, "rev-parse", "review/calibration") == plan["candidate_revision"]
    finally:
        operator._git(draft, "worktree", "remove", "--force", str(linked))


@pytest.mark.parametrize("change", ["working_bytes", "index", "ignored_shadow", "other_ignored_shadow"])
def test_draft_refuses_hidden_bytes_index_and_ignored_instrument_sources(draft, tmp_path, change):
    relative = PREFIX + "/README.md"
    path = draft / relative
    data = path.read_bytes()
    if change == "working_bytes":
        operator._git(draft, "update-index", "--assume-unchanged", relative)
        path.write_bytes(data + b"hidden\n")
    elif change == "index":
        path.write_bytes(data + b"staged\n")
        operator._git(draft, "add", relative)
        path.write_bytes(data)
    else:
        module = PREFIX if change == "ignored_shadow" else "instruments/measurement/clocksync"
        shadow = draft / module / ".pytest_cache" / "plugin.py"
        shadow.parent.mkdir(exist_ok=True)
        shadow.write_text("raise RuntimeError('shadow')\n")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        _prepare(draft, tmp_path)
    assert not (tmp_path / "plan.json").exists()
    assert operator._direct_ref(draft, "refs/heads/review/calibration") is None


@pytest.mark.parametrize("change", ["terminal", "other_module", "license", "notice", "metadata", "runtime_pin", "version", "build_layout"])
def test_committed_draft_refuses_other_sources_and_protected_contracts(draft, tmp_path, change):
    if change in {"terminal", "other_module", "license", "notice"}:
        relative = {"terminal": "README.md", "other_module": "instruments/measurement/clocksync/README.md",
                    "license": PREFIX + "/LICENSE", "notice": PREFIX + "/NOTICE.md"}[change]
        path = draft / relative
        path.write_bytes(path.read_bytes() + b"\nUnexpected scope change.\n")
    elif change == "build_layout":
        path = draft / PREFIX / "pyproject.toml"
        path.write_text(path.read_text().replace('requires = ["hatchling"]', 'requires = ["hatchling", "wheel"]'), encoding="utf-8", newline="\n")
        # This source uses setuptools; ensure the fixture changes its actual
        # build-system independently of upstream formatting.
        if operator._git(draft, "diff", "--", str(path.relative_to(draft))) == b"":
            path.write_text(path.read_text().replace("setuptools>=68", "setuptools>=69"), encoding="utf-8", newline="\n")
        assert operator._git(draft, "diff", "--", str(path.relative_to(draft)))
    else:
        path = draft / "instruments/manifest.json"
        manifest = json.loads(path.read_text())
        key = {"metadata": "ownership", "runtime_pin": "runtime_revision", "version": "version"}[change]
        manifest["modules"][0][key] = {"metadata": "someone-else", "runtime_pin": "0" * 40, "version": "99.0"}[change]
        path.write_text(json.dumps(manifest))
    _commit(draft)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        _prepare(draft, tmp_path)


def test_prepare_allows_matching_declared_project_version_without_changing_runtime(draft, tmp_path):
    project = draft / PREFIX / "pyproject.toml"
    project.write_text(project.read_text().replace('version = "0.1.0"', 'version = "0.1.1"'), encoding="utf-8", newline="\n")
    path = draft / "instruments/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["modules"][0]["version"] = "0.1.1"
    path.write_text(json.dumps(manifest))
    _commit(draft)
    plan, _ = _prepare(draft, tmp_path)
    updated = json.loads(operator._git(draft, "show", plan["candidate_revision"] + ":instruments/manifest.json"))
    assert updated["modules"][0]["version"] == "0.1.1"
    assert updated["modules"][0]["runtime_revision"] == manifest["modules"][0]["runtime_revision"]


def test_missing_configured_identity_refuses_before_source_object_write(draft, tmp_path, monkeypatch):
    # An explicit empty local identity also defeats a configured global email.
    operator._git(draft, "config", "user.email", "")
    original = operator._git

    def missing(root, *arguments, **kwargs):
        assert arguments[0] not in {"commit-tree", "hash-object", "mktree"}
        return original(root, *arguments, **kwargs)
    monkeypatch.setattr(operator, "_git", missing)
    with pytest.raises(ValueError, match="identity"):
        _prepare(draft, tmp_path)


@pytest.mark.parametrize("tamper", ["extra", "preparation", "audit_identity", "audit_tree", "source", "candidate", "qualification", "root", "destination"])
def test_apply_distrusts_plan_metadata_and_retained_objects(draft, tmp_path, tamper):
    plan, path = _prepare(draft, tmp_path)
    if tamper == "extra":
        plan["extra"] = True
    elif tamper == "preparation":
        plan["preparation_id"] = "preparation:" + "0" * 32
    elif tamper == "audit_identity":
        plan["source_audit"]["verification_id"] = "verification:" + "0" * 32
    elif tamper == "audit_tree":
        plan["source_audit"]["imports"]["mcur"] = "0" * 40
    elif tamper == "source":
        plan["source_revision"] = BASE
    elif tamper == "candidate":
        plan["candidate_revision"] = BASE
    elif tamper == "qualification":
        plan["package_qualification"] = "passed"
    elif tamper == "root":
        plan["root"] = str(tmp_path)
    else:
        plan["review_ref"] = "refs/heads/unreviewed"
    _rewrite(path, plan)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        operator.apply(draft, path)
    assert operator._direct_ref(draft, "refs/heads/review/calibration") is None
    assert operator._direct_ref(draft, "refs/heads/unreviewed") is None


def test_duplicate_plan_keys_and_oversized_plan_refused(draft, tmp_path):
    plan, path = _prepare(draft, tmp_path)
    path.write_text('{"schema": "x", "schema": "x"}')
    with pytest.raises(ValueError, match="Duplicate"):
        operator.apply(draft, path)
    path.write_text(" " * 65537)
    with pytest.raises(ValueError, match="bounded"):
        operator.apply(draft, path)
    path.write_text("[" * 1100 + "0" + "]" * 1100)
    with pytest.raises(ValueError, match="nesting"):
        operator.apply(draft, path)
    path.write_text('{"unknown": NaN}')
    with pytest.raises(ValueError, match="Non-JSON"):
        operator.apply(draft, path)


@pytest.mark.parametrize("change", ["head_commit", "branch", "working_bytes", "destination"])
def test_apply_refuses_changed_draft_or_occupied_destination(draft, tmp_path, change):
    plan, path = _prepare(draft, tmp_path)
    if change == "head_commit":
        operator._git(draft, "commit", "--quiet", "--allow-empty", "-m", "Concurrent draft")
    elif change == "branch":
        operator._git(draft, "checkout", "--quiet", "-b", "another-branch")
    elif change == "working_bytes":
        source = draft / PREFIX / "README.md"
        operator._git(draft, "update-index", "--assume-unchanged", PREFIX + "/README.md")
        source.write_bytes(source.read_bytes() + b"hidden\n")
    else:
        operator._git(draft, "update-ref", plan["review_ref"], BASE)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        operator.apply(draft, path)


def test_prepare_refuses_unborn_worktree_destination(draft, tmp_path):
    path = tmp_path / "unborn"
    operator._git(draft, "worktree", "add", "--orphan", "-b", "review/calibration", str(path))
    try:
        assert operator._direct_ref(draft, "refs/heads/review/calibration") is None
        with pytest.raises(ValueError, match="unborn worktree"):
            _prepare(draft, tmp_path)
    finally:
        operator._git(draft, "worktree", "remove", "--force", str(path))


@pytest.mark.parametrize("race", ["head", "draft_ref", "destination"])
def test_atomic_transaction_rejects_head_branch_and_destination_races(draft, tmp_path, monkeypatch, race):
    plan, path = _prepare(draft, tmp_path)
    original = operator._git

    def racing(root, *arguments, **kwargs):
        if arguments == ("update-ref", "--stdin"):
            if race == "head":
                original(draft, "branch", "replacement", plan["draft_revision"])
                original(draft, "symbolic-ref", "HEAD", "refs/heads/replacement")
            elif race == "draft_ref":
                original(root, "update-ref", plan["draft_ref"], BASE)
            else:
                original(root, "update-ref", plan["review_ref"], BASE)
        return original(root, *arguments, **kwargs)
    monkeypatch.setattr(operator, "_git", racing)
    result = operator.apply(draft, path)
    assert result["status"] == ("concurrent_state" if race == "destination" else "unpublished")
    assert result["publication_error"]["type"] == "CalledProcessError"
    assert operator._direct_ref(draft, plan["review_ref"]) == (BASE if race == "destination" else None)


def test_native_HEAD_guard_remains_locked_through_publication_and_is_cleaned(draft, tmp_path, monkeypatch):
    plan, path = _prepare(draft, tmp_path)
    operator._git(draft, "branch", "replacement", plan["draft_revision"])
    original = operator._git
    observed = []

    def guarded(root, *arguments, **kwargs):
        if arguments == ("update-ref", "--stdin"):
            assert Path(root) != draft
            with pytest.raises(subprocess.CalledProcessError):
                original(root, "symbolic-ref", "--quiet", "HEAD")
            # A real competing Git mutation must fail while the guard holds
            # HEAD; a Python lock or pre-publication read cannot provide this.
            attempt = subprocess.run(["git", "-C", str(draft), "symbolic-ref", "HEAD",
                                      "refs/heads/replacement"], capture_output=True)
            assert attempt.returncode != 0 and b"HEAD.lock" in attempt.stderr
            observed.append(True)
        return original(root, *arguments, **kwargs)
    monkeypatch.setattr(operator, "_git", guarded)
    assert operator.apply(draft, path)["status"] == "published"
    assert observed == [True]
    assert operator._text(draft, "symbolic-ref", "HEAD") == plan["draft_ref"]
    head_path = Path(operator._text(draft, "rev-parse", "--git-path", "HEAD"))
    if not head_path.is_absolute():
        head_path = draft / head_path
    assert not Path(str(head_path) + ".lock").exists()
    assert len(operator._git(draft, "worktree", "list", "--porcelain").split(b"worktree ")) == 2


@pytest.mark.parametrize("when", ["before", "during"])
def test_premature_HEAD_guard_exit_is_refused_or_reported_with_actual_publication(draft, tmp_path, monkeypatch, when):
    plan, path = _prepare(draft, tmp_path)
    original_guard = operator._head_guard
    original_git = operator._git
    captured = {}

    def stop(process):
        process.stdin.write(b"abort\n")
        process.stdin.flush()
        process.stdin.close()
        process.wait(timeout=5)

    @contextmanager
    def exiting(root, ref):
        with original_guard(root, ref) as process:
            captured["guard"] = process
            if when == "before":
                stop(process)
            yield process

    def publication(root, *arguments, **kwargs):
        result = original_git(root, *arguments, **kwargs)
        if when == "during" and arguments == ("update-ref", "--stdin"):
            stop(captured["guard"])
        return result

    monkeypatch.setattr(operator, "_head_guard", exiting)
    monkeypatch.setattr(operator, "_git", publication)
    if when == "before":
        with pytest.raises(ValueError, match="HEAD guard exited"):
            operator.apply(draft, path)
        assert operator._direct_ref(draft, plan["review_ref"]) is None
    else:
        result = operator.apply(draft, path)
        assert result["status"] == "published"
        assert "HEAD guard exited" in result["publication_error"]["message"]
        assert operator._direct_ref(draft, plan["review_ref"]) == plan["candidate_revision"]


def test_post_publication_cleanup_failure_reports_retained_review_branch(draft, tmp_path, monkeypatch):
    plan, path = _prepare(draft, tmp_path)
    original = operator._detached

    @contextmanager
    def cleanup(root, revision):
        with original(root, revision) as checkout:
            yield checkout
        if revision == plan["candidate_revision"]:
            raise OSError("fixture cleanup failure after branch publication")

    monkeypatch.setattr(operator, "_detached", cleanup)
    result = operator.apply(draft, path)
    assert result["status"] == "published"
    assert result["publication_error"]["type"] == "OSError"
    assert operator._direct_ref(draft, plan["review_ref"]) == plan["candidate_revision"]
    assert len(operator._git(draft, "worktree", "list", "--porcelain").split(b"worktree ")) == 2


@pytest.mark.parametrize("outcome", ["absent", "published", "conflicting", "symbolic"])
def test_timeout_observes_destination_without_deleting_or_dereferencing(draft, tmp_path, monkeypatch, outcome):
    plan, path = _prepare(draft, tmp_path)
    original = operator._git

    def timeout(root, *arguments, **kwargs):
        if arguments == ("update-ref", "--stdin"):
            if outcome == "published":
                original(root, *arguments, **kwargs)
            elif outcome == "conflicting":
                original(root, "update-ref", plan["review_ref"], BASE)
            elif outcome == "symbolic":
                original(root, "symbolic-ref", plan["review_ref"], "refs/heads/unborn-target")
            raise subprocess.TimeoutExpired(arguments, 30)
        return original(root, *arguments, **kwargs)
    monkeypatch.setattr(operator, "_git", timeout)
    result = operator.apply(draft, path)
    assert result["status"] == {"absent": "unpublished", "published": "published",
                                "conflicting": "concurrent_state", "symbolic": "concurrent_state"}[outcome]
    assert result["publication_timeout"] is True
    assert len(operator._git(draft, "worktree", "list", "--porcelain").split(b"worktree ")) == 2
    if outcome == "symbolic":
        assert result["observed_ref"] == "symbolic:refs/heads/unborn-target"


def test_plan_is_exclusive_and_cannot_be_saved_in_sources_or_git_metadata(draft, tmp_path):
    for output in (draft / "scripts/plan.json", draft / "instruments/plan.json", draft / ".git/plan.json"):
        with pytest.raises(ValueError, match="outside executable"):
            operator.prepare(draft, "mcur", BASE, "review/calibration", output)
    plan, path = _prepare(draft, tmp_path)
    with pytest.raises(ValueError, match="already exists"):
        operator.prepare(draft, "mcur", BASE, "review/calibration", path)
    assert json.loads(path.read_text()) == plan


def test_detached_draft_and_nonexplicit_base_refused(draft, tmp_path):
    with pytest.raises(ValueError, match="explicit full"):
        _prepare(draft, tmp_path, base="HEAD~1")
    operator._git(draft, "checkout", "--quiet", "--detach")
    with pytest.raises(ValueError, match="attached branch"):
        _prepare(draft, tmp_path)
