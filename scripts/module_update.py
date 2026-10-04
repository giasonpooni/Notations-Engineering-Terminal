"""Prepare and publish a reviewed module source update without moving runtime pins.

A committed one-module draft becomes a native source-history commit and a
two-parent review commit. Preparation changes no caller refs or index. Applying
an explicit plan creates an absent review branch in one guarded transaction;
neither operation qualifies numerical behavior or admits operational evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time
import tomllib
import uuid

from monorepo import (_PATHS, _generated_file, load_manifest, verify_imports,
                      verify_terminal_source, worktree_mutation_lock)

SCHEMA = "notations.module-update-plan.v1"
_SHA = re.compile(r"[0-9a-f]{40}")
_PLAN_KEYS = {"schema", "preparation_id", "root", "git_common_dir", "role", "base_revision",
              "draft_revision", "draft_ref", "review_ref", "source_revision", "source_tree",
              "candidate_revision", "candidate_tree", "source_audit", "package_qualification",
              "composition_qualification", "admission"}
_CONTEXT_KEYS = ("schema", "preparation_id", "root", "git_common_dir", "role", "base_revision",
                 "draft_revision", "draft_ref", "review_ref", "source_revision", "source_tree")


def _command(root, arguments):
    environment = dict(os.environ)
    for name in tuple(environment):
        if (name in {"GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"}
                or name.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG_"))):
            environment.pop(name, None)
    command = ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false",
               "-c", "core.longpaths=true", "-C", str(root), *arguments]
    return command, environment


def _git(root, *arguments, data=None):
    command, environment = _command(root, arguments)
    return subprocess.run(
        command,
        input=data, env=environment, check=True, capture_output=True, timeout=30,
    ).stdout


def _text(root, *arguments):
    return os.fsdecode(_git(root, *arguments)).strip()


def _json(data):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate plan or manifest key: " + key)
            result[key] = value
        return result
    def non_json(value):
        raise ValueError("Non-JSON numeric constant: " + value)
    try:
        result = json.loads(data, object_pairs_hook=unique, parse_constant=non_json)
    except RecursionError as error:
        raise ValueError("The JSON document exceeds the supported nesting bound") from error
    # A host or test runner can increase Python's recursion limit. Keep this
    # bound explicit rather than deriving the input contract from that setting.
    pending = [(result, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 32:
            raise ValueError("The JSON document exceeds the supported nesting bound")
        children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        pending.extend((child, depth + 1) for child in children)
    return result


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def _revision(value):
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError("An explicit full lowercase commit identity is required")
    return value


def _ref(root, branch):
    if (not isinstance(branch, str) or not branch or len(branch) > 240
            or branch.startswith("refs/")):
        raise ValueError("An explicit review branch name is required")
    ref = "refs/heads/" + branch
    try:
        _git(root, "check-ref-format", ref)
    except subprocess.CalledProcessError as error:
        raise ValueError("Invalid review branch name") from error
    return ref


def _repository(root):
    root = Path(root).expanduser().resolve()
    if Path(_text(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("Module preparation requires the actual repository root")
    if _text(root, "rev-parse", "--show-object-format") != "sha1":
        raise ValueError("Module source identities require SHA-1 Git objects")
    match = re.search(r"git version (\d+)\.(\d+)", _text(root, "version"))
    if match is None or tuple(map(int, match.groups())) < (2, 46):
        raise ValueError("Git 2.46 or newer is required for atomic HEAD verification")
    common = Path(_text(root, "rev-parse", "--git-common-dir"))
    return root, (common if common.is_absolute() else root / common).resolve()


def _identity(root):
    for name in ("user.name", "user.email"):
        try:
            value = _text(root, "config", "--get", name)
        except subprocess.CalledProcessError as error:
            raise ValueError("Configured Git author and committer identity is required") from error
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Configured Git author and committer identity is required")
    _git(root, "var", "GIT_AUTHOR_IDENT")
    _git(root, "var", "GIT_COMMITTER_IDENT")


def _direct_ref(root, ref):
    # Inspect without dereferencing: a symbolic destination is occupied even
    # when its referent is unborn. A symbolic draft branch is unsupported.
    try:
        target = _text(root, "symbolic-ref", "--quiet", ref)
    except subprocess.CalledProcessError as error:
        if error.returncode != 1:
            raise
    else:
        return "symbolic:" + target
    try:
        return _text(root, "show-ref", "--verify", "--hash", ref)
    except subprocess.CalledProcessError as error:
        if error.returncode not in {1, 128}:
            raise
        return None


def _draft(root):
    try:
        ref = _text(root, "symbolic-ref", "--quiet", "HEAD")
    except subprocess.CalledProcessError as error:
        raise ValueError("The committed draft must be on an attached branch") from error
    if not ref.startswith("refs/heads/"):
        raise ValueError("The draft must be on an attached local branch")
    revision = _text(root, "rev-parse", "HEAD")
    if _direct_ref(root, ref) != revision:
        raise ValueError("The captured draft branch must be a direct commit reference")
    verify_terminal_source(root)
    # The draft intentionally differs from its old import tree. Audit every
    # instrument's ignored/untracked executable paths without granting that
    # source mismatch a general verify_imports exception.
    for prefix in _PATHS.values():
        for raw_name in _git(root, "ls-files", "--others", "-z", "--", prefix).split(b"\0"):
            if raw_name and not _generated_file(root / os.fsdecode(raw_name), root / prefix,
                                                environments=True, package_metadata=True):
                raise ValueError("Unexpected untracked file inside an imported module")
    if _text(root, "rev-parse", "HEAD") != revision or _text(root, "symbolic-ref", "HEAD") != ref:
        raise ValueError("The draft HEAD changed during source audit")
    return revision, ref


def _destination(root, ref):
    if _direct_ref(root, ref) is not None:
        raise ValueError("The review branch already exists")
    for field in _git(root, "worktree", "list", "--porcelain", "-z").split(b"\0"):
        if field == b"branch " + ref.encode():
            raise ValueError("The review branch is occupied by an existing or unborn worktree")


@contextmanager
def _detached(root, revision):
    with tempfile.TemporaryDirectory(prefix="notations-module-audit-") as temporary:
        path = Path(temporary) / "checkout"
        created = False
        try:
            with worktree_mutation_lock(root):
                _git(root, "worktree", "add", "--detach", str(path), revision)
            created = True
            yield path
        finally:
            if created:
                with worktree_mutation_lock(root):
                    _git(root, "worktree", "remove", "--force", str(path))


def _audit(root, revision, verification_id):
    with _detached(root, revision) as path:
        return _audit_path(path, verification_id)


def _audit_path(path, verification_id):
    imports = verify_imports(path)
    terminal = verify_terminal_source(path)
    return {"verification_id": verification_id, "status": "passed", "imports": imports,
            "terminal_source": terminal, "independent_verification": False}


@contextmanager
def _head_guard(root, draft_ref):
    """Hold a prepared native HEAD verification until publication finishes.

    Git's files backend rejects HEAD symref verification and verification of
    its referent in one transaction because the latter synthesizes a second
    HEAD reflog update. A separate verification-only transaction holds the
    caller's actual HEAD lock, while a detached publishing worktree performs
    the shared draft-ref and absent-destination transaction. Neither guard
    changes caller HEAD, the draft reference, or its index.
    """
    command, environment = _command(root, ("update-ref", "--stdin"))
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=environment)
    lines = queue.Queue()

    def reader():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    try:
        process.stdin.write(("start\noption no-deref\nsymref-verify HEAD " + draft_ref + "\nprepare\n").encode())
        process.stdin.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                line = lines.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as error:
                raise subprocess.TimeoutExpired(command, 30) from error
            if line is None:
                raise subprocess.CalledProcessError(process.wait(), command, stderr=process.stderr.read())
            if line.strip() == b"prepare: ok":
                break
        yield process
    finally:
        if process.poll() is None:
            try:
                process.stdin.write(b"abort\n")
                process.stdin.flush()
                process.stdin.close()
                process.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=30)
        else:
            process.stdin.close()
        reader_thread.join(timeout=1)
        process.stdout.close()
        process.stderr.close()


def _project(root, revision, module):
    prefix = module["path"]
    if module["role"] in {"gsv", "framemapper"}:
        return _json(_git(root, "show", revision + ":" + prefix + "/package.json"))
    return tomllib.loads(_git(root, "show", revision + ":" + prefix + "/pyproject.toml").decode())


def _scope(root, role, base, draft):
    _revision(base)
    _git(root, "cat-file", "-e", base + "^{commit}")
    try:
        _git(root, "merge-base", "--is-ancestor", base, draft)
    except subprocess.CalledProcessError as error:
        raise ValueError("The audited base must be an ancestor of the draft") from error
    if role not in _PATHS:
        raise ValueError("Select one registered module role")
    with _detached(root, base) as path:
        verify_imports(path)
        baseline = load_manifest(path)
    declared = load_manifest(root)
    old = next(module for module in baseline["modules"] if module["role"] == role)
    new = next(module for module in declared["modules"] if module["role"] == role)
    comparison = deepcopy(declared)
    next(module for module in comparison["modules"] if module["role"] == role)["version"] = old["version"]
    if comparison != baseline:
        raise ValueError("Only the selected module version may change in the draft manifest")
    prefix = old["path"]
    changes = [os.fsdecode(name) for name in _git(root, "diff", "--no-ext-diff", "--no-textconv",
               "--name-only", "-z", base, draft).split(b"\0") if name]
    if not any(name.startswith(prefix + "/") for name in changes):
        raise ValueError("The draft must contain a selected module source change")
    if any(not name.startswith(prefix + "/") and name != "instruments/manifest.json" for name in changes):
        raise ValueError("The draft changes another module or Terminal source")
    for name in {"LICENSE", old.get("notice")} - {None}:
        if _git(root, "show", base + ":" + prefix + "/" + name) != _git(root, "show", draft + ":" + prefix + "/" + name):
            raise ValueError("The original LICENSE and NOTICE bytes must remain unchanged")
    before, after = _project(root, base, old), _project(root, draft, new)
    if role in {"gsv", "framemapper"}:
        protected = ("name", "license", "type", "main", "exports", "files", "workspaces")
        if any(before.get(key) != after.get(key) for key in protected):
            raise ValueError("The original package identity and import layout must remain unchanged")
    else:
        if before.get("build-system") != after.get("build-system"):
            raise ValueError("The original package build boundary must remain unchanged")
        for key in ("setuptools", "hatch", "poetry", "flit", "pdm"):
            if before.get("tool", {}).get(key) != after.get("tool", {}).get(key):
                raise ValueError("The original package import layout must remain unchanged")
    return declared, old, _text(root, "rev-parse", draft + ":" + prefix)


def _replace_blob(root, tree, parts, blob):
    entries = []
    found = False
    for entry in _git(root, "ls-tree", "-z", tree).split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        if name == parts[0].encode():
            mode, kind, old = metadata.decode().split()
            if len(parts) == 1:
                if mode != "100644" or kind != "blob":
                    raise ValueError("The import manifest must retain its regular file mode")
                metadata = (mode + " blob " + blob).encode()
            else:
                if kind != "tree":
                    raise ValueError("The import manifest path must traverse ordinary trees")
                replacement = _replace_blob(root, old, parts[1:], blob)
                metadata = (mode + " tree " + replacement).encode()
            found = True
        entries.append(metadata + b"\t" + name + b"\0")
    if not found:
        raise ValueError("The import manifest must already exist")
    return os.fsdecode(_git(root, "mktree", "-z", data=b"".join(entries))).strip()


def _candidate_tree(root, draft, declared, role, source, tree):
    updated = deepcopy(declared)
    selected = next(module for module in updated["modules"] if module["role"] == role)
    selected.update(import_revision=source, import_tree=tree)
    data = (json.dumps(updated, indent=2) + "\n").encode()
    blob = os.fsdecode(_git(root, "hash-object", "-w", "--stdin", data=data)).strip()
    return _replace_blob(root, _text(root, "rev-parse", draft + "^{tree}"),
                         ["instruments", "manifest.json"], blob)


def _context(plan):
    value = {key: plan[key] for key in _CONTEXT_KEYS}
    value["source_audit_verification_id"] = plan["source_audit"]["verification_id"]
    return value


def _candidate_message(plan):
    return b"Prepare module source review\n\n" + _canonical(_context(plan))


def _source_message(preparation_id, role):
    return ("Promote " + role + " native source for " + preparation_id + "\n").encode()


def _commit(root, revision):
    header, message = _git(root, "cat-file", "commit", revision).split(b"\n\n", 1)
    trees = [line[5:].decode() for line in header.splitlines() if line.startswith(b"tree ")]
    parents = [line[7:].decode() for line in header.splitlines() if line.startswith(b"parent ")]
    if len(trees) != 1:
        raise ValueError("Invalid retained commit tree")
    return trees[0], parents, message


def _output_path(root, common, output, *, exclusive=True):
    path = Path(output).expanduser().absolute()
    if path.resolve() != path or not path.parent.is_dir():
        raise ValueError("The exclusive plan path requires an existing ordinary parent directory")
    forbidden = [root / name for name in ("src", "scripts", "tests", "instruments", ".git")]
    forbidden.append(common)
    if any(path.is_relative_to(boundary) for boundary in forbidden):
        raise ValueError("The plan must live outside executable source and Git metadata directories")
    if exclusive and path.exists():
        raise ValueError("The plan path already exists")
    return path


def prepare(root, role, base, branch, output: Path) -> dict:
    """Prepare retained native history and audit a review candidate; publish no ref."""
    root, common = _repository(root)
    path = _output_path(root, common, output)
    _identity(root)  # Fail before writing any object when identity is absent.
    review_ref = _ref(root, branch)
    with worktree_mutation_lock(root):
        _destination(root, review_ref)
    draft, draft_ref = _draft(root)
    declared, old, tree = _scope(root, role, base, draft)
    preparation = "preparation:" + uuid.uuid4().hex
    verification = "verification:" + uuid.uuid4().hex
    source = os.fsdecode(_git(root, "commit-tree", tree, "-p", old["import_revision"],
                             data=_source_message(preparation, role))).strip()
    candidate_tree = _candidate_tree(root, draft, declared, role, source, tree)
    plan = {"schema": SCHEMA, "preparation_id": preparation, "root": str(root),
            "git_common_dir": str(common), "role": role, "base_revision": base,
            "draft_revision": draft, "draft_ref": draft_ref, "review_ref": review_ref,
            "source_revision": source, "source_tree": tree, "candidate_tree": candidate_tree,
            "source_audit": {"verification_id": verification},
            "package_qualification": "not_performed", "composition_qualification": "not_performed",
            "admission": "not_performed"}
    candidate = os.fsdecode(_git(root, "commit-tree", candidate_tree, "-p", draft, "-p", source,
                                data=_candidate_message(plan))).strip()
    plan["candidate_revision"] = candidate
    plan["source_audit"] = _audit(root, candidate, verification)
    if _draft(root) != (draft, draft_ref):
        raise ValueError("The draft HEAD changed during preparation")
    with worktree_mutation_lock(root):
        _destination(root, review_ref)
    # Exclusive creation is the final durable preparation action. A competing
    # writer cannot replace a previously reviewed plan.
    with path.open("x", encoding="utf-8") as output_file:
        json.dump(plan, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    return plan


def _read_plan(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("A regular bounded module update plan is required")
    plan = _json(path.read_bytes())
    if not isinstance(plan, dict) or set(plan) != _PLAN_KEYS or plan["schema"] != SCHEMA:
        raise ValueError("Unsupported or extended module update plan")
    for key in ("base_revision", "draft_revision", "source_revision", "source_tree",
                "candidate_revision", "candidate_tree"):
        _revision(plan[key])
    for key in ("root", "git_common_dir", "role", "draft_ref", "review_ref"):
        if not isinstance(plan[key], str) or len(plan[key]) > 4096:
            raise ValueError("Invalid module update plan field: " + key)
    if plan["role"] not in _PATHS:
        raise ValueError("Select one registered module role")
    if not isinstance(plan["preparation_id"], str) or re.fullmatch(r"preparation:[0-9a-f]{32}", plan["preparation_id"]) is None:
        raise ValueError("Invalid preparation identity")
    audit = plan["source_audit"]
    if (not isinstance(audit, dict) or set(audit) != {"verification_id", "status", "imports", "terminal_source", "independent_verification"}
            or not isinstance(audit["verification_id"], str)
            or re.fullmatch(r"verification:[0-9a-f]{32}", audit["verification_id"]) is None
            or audit["status"] != "passed" or audit["independent_verification"] is not False):
        raise ValueError("Invalid source audit declaration")
    if (not isinstance(audit["imports"], dict) or set(audit["imports"]) != set(_PATHS)
            or any(not isinstance(value, str) or _SHA.fullmatch(value) is None for value in audit["imports"].values())
            or not isinstance(audit["terminal_source"], dict)
            or set(audit["terminal_source"]) != {"revision", "source_tree"}):
        raise ValueError("Invalid source audit identities")
    for value in audit["terminal_source"].values():
        _revision(value)
    if any(plan[key] != "not_performed" for key in ("package_qualification", "composition_qualification", "admission")):
        raise ValueError("Source promotion cannot claim package qualification or admission")
    return plan


def _apply(root, plan_path, publication) -> dict:
    """Re-audit a plan and atomically create its review branch without checkout."""
    root, common = _repository(root)
    _output_path(root, common, plan_path, exclusive=False)
    plan = _read_plan(plan_path)
    if (plan["root"], plan["git_common_dir"]) != (str(root), str(common)):
        raise ValueError("The plan belongs to another repository or worktree")
    _identity(root)
    review = _ref(root, plan["review_ref"].removeprefix("refs/heads/"))
    if review != plan["review_ref"]:
        raise ValueError("Invalid review reference")
    if _draft(root) != (plan["draft_revision"], plan["draft_ref"]):
        raise ValueError("The captured draft HEAD or branch changed")
    declared, old, tree = _scope(root, plan["role"], plan["base_revision"], plan["draft_revision"])
    source_tree, source_parents, source_message = _commit(root, plan["source_revision"])
    if (tree != plan["source_tree"] or source_tree != tree or source_parents != [old["import_revision"]]
            or source_message != _source_message(plan["preparation_id"], plan["role"])):
        raise ValueError("The retained native source commit does not match the draft contract")
    expected_tree = _candidate_tree(root, plan["draft_revision"], declared, plan["role"], plan["source_revision"], tree)
    candidate_tree, parents, message = _commit(root, plan["candidate_revision"])
    if (candidate_tree != expected_tree or candidate_tree != plan["candidate_tree"]
            or parents != [plan["draft_revision"], plan["source_revision"]]
            or message != _candidate_message(plan)):
        raise ValueError("The retained candidate commit does not match the plan contract")
    # Retain the detached candidate through publication: its HEAD protects the
    # audited candidate and native parent against concurrent ordinary pruning.
    with _detached(root, plan["candidate_revision"]) as publisher:
        audit = _audit_path(publisher, "verification:" + uuid.uuid4().hex)
        comparison = dict(audit, verification_id=plan["source_audit"]["verification_id"])
        if comparison != plan["source_audit"]:
            raise ValueError("The candidate source audit does not match the plan")
        # Keep occupancy stable against this operator's managed worktree
        # mutations. Native HEAD and shared draft-ref locks guard ref races.
        with worktree_mutation_lock(root):
            _destination(root, review)
            if _draft(root) != (plan["draft_revision"], plan["draft_ref"]):
                raise ValueError("The captured draft HEAD or branch changed")
            with _head_guard(root, plan["draft_ref"]) as guard:
                if _draft(root) != (plan["draft_revision"], plan["draft_ref"]):
                    raise ValueError("The captured draft source changed before guarded publication")
                if guard.poll() is not None:
                    raise ValueError("The prepared HEAD guard exited before publication")
                transaction = ("start\noption no-deref\nverify " + plan["draft_ref"] + " " + plan["draft_revision"]
                               + "\noption no-deref\ncreate " + review + " " + plan["candidate_revision"]
                               + "\nprepare\ncommit\n").encode()
                publication.update(review_ref=review, candidate_revision=plan["candidate_revision"],
                                   preparation_id=plan["preparation_id"], source_audit=audit,
                                   preparation_source_audit_verification_id=plan["source_audit"]["verification_id"],
                                   package_qualification="not_performed", composition_qualification="not_performed",
                                   admission="not_performed")
                try:
                    _git(publisher, "update-ref", "--stdin", data=transaction)
                    if guard.poll() is not None:
                        raise ValueError("The prepared HEAD guard exited during publication")
                    status = "published"
                except subprocess.TimeoutExpired:
                    # Keep the HEAD guard alive while inspecting a transaction
                    # which may already have committed. Never remove any ref.
                    observed = _direct_ref(root, review)
                    status = ("unpublished" if observed is None else "published" if observed == plan["candidate_revision"]
                              else "concurrent_state")
                    publication.update(status=status, observed_ref=observed, publication_timeout=True)
                    return dict(publication)
    return dict(publication, status=status)


def apply(root, plan_path) -> dict:
    """Re-audit a plan and create its review branch with coordinated native guards.

    Once publication may have happened, lifecycle errors are reported alongside
    the observed ref. Cleanup failure must not imply that a published branch
    disappeared, and recovery never removes or dereferences that branch.
    """
    publication = {}
    try:
        return _apply(root, plan_path, publication)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if not publication:
            raise
        observed = _direct_ref(Path(root).expanduser().resolve(), publication["review_ref"])
        status = ("unpublished" if observed is None else "published" if observed == publication["candidate_revision"]
                  else "concurrent_state")
        return dict(publication, status=status, observed_ref=observed,
                    publication_error={"type": type(error).__name__, "message": str(error)})
