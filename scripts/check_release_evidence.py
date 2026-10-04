"""Collect read-only GitHub Actions evidence for one immutable NET candidate.

This is a CI-evidence gate, not scientific qualification, artifact authentication,
or permission to publish. Offline snapshots are unauthenticated records.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = "https://api.github.com"
MAX_BYTES = 16 * 1024 * 1024
MAX_PAGES = 10
EVENTS = {"push", "workflow_dispatch"}


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def constant(value):
        raise ValueError(f"nonfinite JSON value: {value}")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def read_json(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("JSON input exceeds 16 MiB")
    return strict_json(raw)


def names(value, label, *, nonempty=False):
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{label} must contain unique nonempty strings")
    return value


def validate_policy(policy):
    if (not isinstance(policy, dict) or type(policy.get("schema_version")) is not int
            or policy.get("schema_version") != 1):
        raise ValueError("unsupported release policy")
    repository = policy.get("repository", "")
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be owner/name")
    workflows = policy.get("required_workflows")
    if not isinstance(workflows, list) or not workflows:
        raise ValueError("required_workflows must not be empty")
    paths = []
    for item in workflows:
        if not isinstance(item, dict):
            raise ValueError("workflow requirement must be an object")
        path = item.get("path", "")
        if not isinstance(path, str) or not re.fullmatch(r"\.github/workflows/[A-Za-z0-9_-]+\.ya?ml", path):
            raise ValueError("invalid workflow path")
        paths.append(path)
        required = names(item.get("jobs"), f"{path}: jobs", nonempty=True)
        skipped = names(item.get("allowed_skipped_jobs", []), f"{path}: allowed_skipped_jobs")
        names(item.get("artifacts", []), f"{path}: artifacts")
        excluded = item.get("excluded_jobs", {})
        if (not isinstance(excluded, dict)
                or any(not isinstance(key, str) or not key.strip()
                       or not isinstance(reason, str) or not reason.strip()
                       for key, reason in excluded.items())
                or set(excluded) & (set(required) | set(skipped))):
            raise ValueError(f"{path}: invalid or overlapping excluded jobs")
        if set(required) & set(skipped):
            raise ValueError(f"{path}: a required job cannot be allowed to skip")
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate required workflow")
    return policy


def candidate_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("candidate must be a full lowercase 40-character Git commit SHA")
    return value


def positive_int(value):
    return type(value) is int and value > 0


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return result


def select_run(runs, sha, repository, path):
    eligible = [
        run for run in runs
        if isinstance(run, dict)
        and run.get("head_sha") == sha
        and run.get("event") in EVENTS
        and run.get("path") == path
        and isinstance(run.get("head_repository"), dict)
        and run["head_repository"].get("full_name") == repository
    ]
    if any(not positive_int(run.get("id")) or not positive_int(run.get("run_attempt")) for run in eligible):
        raise ValueError("invalid workflow run identity")
    return max(eligible, key=lambda run: (run["id"], run["run_attempt"]), default=None)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub API redirect refused; use the canonical repository")


class GitHub:
    def __init__(self, repository, token=None):
        self.repository = repository
        self.token = token
        self.opener = build_opener(NoRedirect())

    def get(self, suffix, params=None):
        url = f"{API}/repos/{self.repository}/{suffix}"
        if params:
            url += "?" + urlencode(params)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "net-release-evidence"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            with self.opener.open(Request(url, headers=headers), timeout=30) as response:
                raw = response.read(MAX_BYTES + 1)
        except HTTPError as exc:
            raise ValueError(f"GitHub API HTTP {exc.code} for {suffix}") from None
        except (URLError, TimeoutError, OSError):
            raise ValueError(f"GitHub API unavailable for {suffix}") from None
        if len(raw) > MAX_BYTES:
            raise ValueError("GitHub response exceeds 16 MiB")
        return strict_json(raw)

    def pages(self, suffix, key, params=None):
        rows = []
        expected_total = None
        for page in range(1, MAX_PAGES + 1):
            data = self.get(suffix, {**(params or {}), "per_page": 100, "page": page})
            if not isinstance(data, dict) or not isinstance(data.get(key), list):
                raise ValueError(f"invalid GitHub {key} response")
            total = data.get("total_count")
            if type(total) is not int or total < 0 or total > MAX_PAGES * 100:
                raise ValueError(f"invalid or excessive GitHub {key} total")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise ValueError(f"changing GitHub {key} pagination; collect again")
            rows.extend(data[key])
            if len(rows) >= total:
                if len(rows) != total:
                    raise ValueError(f"changing GitHub {key} pagination; collect again")
                ids = [row.get("id") if isinstance(row, dict) else None for row in rows]
                if any(not positive_int(item) for item in ids) or len(set(ids)) != len(ids):
                    raise ValueError(f"invalid or duplicate GitHub {key} records")
                return rows
            if not data[key]:
                raise ValueError(f"incomplete GitHub {key} pagination")
        raise ValueError(f"GitHub {key} pagination limit exceeded")


def collect(policy, sha, api):
    records = []
    for requirement in policy["required_workflows"]:
        path = requirement["path"]
        record = {"path": path}
        try:
            runs = api.pages(f"actions/workflows/{path.rsplit('/', 1)[-1]}/runs",
                             "workflow_runs", {"head_sha": sha})
            selected = select_run(runs, sha, policy["repository"], path)
            record["run"] = selected
            if selected:
                run_id, attempt = selected["id"], selected["run_attempt"]
                record["jobs"] = api.pages(f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
                record["artifacts"] = api.pages(f"actions/runs/{run_id}/artifacts", "artifacts")
                # A rerun beginning during collection invalidates this snapshot.
                latest = api.get(f"actions/runs/{run_id}")
                if not isinstance(latest, dict):
                    raise ValueError("invalid latest workflow response")
                if any(latest.get(key) != selected.get(key)
                       for key in ("id", "run_attempt", "status", "conclusion", "updated_at")):
                    raise ValueError("workflow changed during collection; collect again")
                current = select_run(
                    api.pages(f"actions/workflows/{path.rsplit('/', 1)[-1]}/runs",
                              "workflow_runs", {"head_sha": sha}),
                    sha, policy["repository"], path)
                if not current or any(current.get(key) != selected.get(key)
                                      for key in ("id", "run_attempt", "status", "conclusion", "updated_at")):
                    raise ValueError("latest eligible workflow changed; collect again")
        except (ValueError, TypeError, KeyError) as exc:
            record["error"] = str(exc)
        records.append(record)
    return {"schema_version": 1, "repository": policy["repository"], "candidate_sha": sha,
            "collected_at": datetime.now(timezone.utc).isoformat(), "workflows": records}


def assess_workflow(requirement, record, sha, repository):
    errors = []
    path = requirement["path"]
    if record.get("error"):
        return [f"collection error: {record['error']}"]
    run = record.get("run")
    if not isinstance(run, dict):
        return ["no eligible push/manual run on this exact candidate"]
    if (run.get("head_sha") != sha or run.get("path") != path
            or run.get("event") not in EVENTS
            or not isinstance(run.get("head_repository"), dict)
            or run["head_repository"].get("full_name") != repository
            or not positive_int(run.get("id")) or not positive_int(run.get("run_attempt"))):
        return ["workflow identity, event, repository or candidate mismatch"]
    excluded = requirement.get("excluded_jobs", {})
    acceptable = {"success", "failure"} if excluded else {"success"}
    if run.get("status") != "completed" or run.get("conclusion") not in acceptable:
        errors.append(f"run is {run.get('status')}/{run.get('conclusion')}")
    jobs = record.get("jobs", [])
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        return errors + ["invalid jobs evidence"]
    job_ids = [job.get("id") for job in jobs]
    if any(not positive_int(value) for value in job_ids) or len(set(job_ids)) != len(job_ids):
        return errors + ["invalid or duplicate job identities"]
    job_names = [job.get("name") for job in jobs]
    if any(not isinstance(name, str) for name in job_names) or len(set(job_names)) != len(job_names):
        return errors + ["invalid or duplicate job names"]
    for name in [*requirement["jobs"], *requirement.get("allowed_skipped_jobs", [])]:
        if name not in job_names:
            errors.append(f"missing required job: {name}")
    allowed = requirement.get("allowed_skipped_jobs", [])
    for job in jobs:
        name = job.get("name")
        if (not positive_int(job.get("run_id")) or not positive_int(job.get("run_attempt"))
                or job.get("head_sha") != sha or job.get("run_id") != run["id"]
                or job.get("run_attempt") != run["run_attempt"]):
            errors.append(f"job identity mismatch: {name}")
        if name in excluded:
            continue
        success = job.get("status") == "completed" and job.get("conclusion") == "success"
        permitted_skip = (name in allowed and job.get("status") == "completed"
                          and job.get("conclusion") == "skipped")
        if not success and not permitted_skip:
            errors.append(f"job is not successful: {name} ({job.get('status')}/{job.get('conclusion')})")
    if run.get("conclusion") == "failure" and not any(
            job.get("name") in excluded and job.get("conclusion") == "failure" for job in jobs):
        errors.append("workflow failure is not explained by an explicitly excluded job")
    artifacts = record.get("artifacts", [])
    if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
        return errors + ["invalid artifacts evidence"]
    for name in requirement.get("artifacts", []):
        named = [item for item in artifacts if item.get("name") == name]
        try:
            started = timestamp(run.get("run_started_at"))
            finished = timestamp(run.get("updated_at"))
            if finished < started:
                raise ValueError("invalid run time window")
            matches = [item for item in named
                       if started <= timestamp(item.get("created_at")) <= finished]
        except ValueError:
            errors.append(f"invalid artifact/run timestamp: {name}")
            continue
        if len(matches) != 1:
            errors.append(f"missing or ambiguous required artifact: {name}")
            continue
        artifact = matches[0]
        origin = artifact.get("workflow_run", {})
        try:
            current_attempt = timestamp(artifact.get("created_at")) >= timestamp(run.get("run_started_at"))
        except ValueError:
            current_attempt = False
        if (not positive_int(artifact.get("id")) or artifact.get("expired") is not False
                or not positive_int(artifact.get("size_in_bytes"))
                or not isinstance(origin, dict) or not positive_int(origin.get("id"))
                or origin.get("id") != run["id"]
                or origin.get("head_sha") != sha or not current_attempt):
            errors.append(f"artifact is empty, expired, stale or mismatched: {name}")
    return errors


def evaluate(policy, evidence, sha):
    validate_policy(policy)
    candidate_sha(sha)
    if (not isinstance(evidence, dict) or type(evidence.get("schema_version")) is not int
            or evidence.get("schema_version") != 1
            or evidence.get("repository") != policy["repository"] or evidence.get("candidate_sha") != sha):
        raise ValueError("evidence repository/candidate/schema mismatch")
    records = evidence.get("workflows")
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError("invalid workflow evidence")
    paths = [row.get("path") for row in records]
    if any(not isinstance(path, str) for path in paths) or len(set(paths)) != len(paths):
        raise ValueError("invalid or duplicate workflow evidence")
    rows = []
    for requirement in policy["required_workflows"]:
        matches = [row for row in records if row["path"] == requirement["path"]]
        errors = (assess_workflow(requirement, matches[0], sha, policy["repository"])
                  if matches else ["missing workflow evidence"])
        rows.append({"path": requirement["path"], "status": "blocked" if errors else "pass",
                     "reasons": errors, "excluded_jobs": requirement.get("excluded_jobs", {}),
                     "run_url": matches[0].get("run", {}).get("html_url")
                     if matches and isinstance(matches[0].get("run"), dict) else None})
    return {"schema_version": 1, "repository": policy["repository"], "candidate_sha": sha,
            "policy_sha256": hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "status": "blocked" if any(row["reasons"] for row in rows) else "ci_pass",
            "scope": "CI metadata and retained artifact presence only; not scientific qualification or artifact authentication.",
            "workflows": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True, help="Full immutable candidate commit SHA")
    parser.add_argument("--policy", type=Path, default=Path(__file__).resolve().parents[1] / "release/qualification-policy.json")
    parser.add_argument("--evidence", type=Path, help="Re-evaluate an unauthenticated saved snapshot without network")
    parser.add_argument("--output", type=Path, required=True, help="Directory for evidence.json and report.json")
    args = parser.parse_args(argv)
    try:
        sha = candidate_sha(args.sha)
        if args.output.exists():
            raise ValueError("output directory already exists; choose a new evidence directory")
        policy = validate_policy(read_json(args.policy))
        evidence = (read_json(args.evidence) if args.evidence else
                    collect(policy, sha, GitHub(policy["repository"], os.environ.get("GITHUB_TOKEN"))))
        report = evaluate(policy, evidence, sha)
        report["evidence_mode"] = "offline_snapshot" if args.evidence else "github_api"
        args.output.mkdir(parents=True)
        for name, value in (("evidence.json", evidence), ("report.json", report)):
            (args.output / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{report['status']}: {sha}; see {args.output / 'report.json'}")
        return 0 if report["status"] == "ci_pass" else 1
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f"release evidence error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
