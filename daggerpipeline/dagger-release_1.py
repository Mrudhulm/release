#!/usr/bin/env python3
"""
dagger-release.py

Compare semantic versions between main and feature branches (package.json or project.toml),
and create a release branch automatically when the feature branch has a higher version.

Environment variables read:
- FEATURE_BRANCH (branch name or refs/heads/feature/xxx)
- MAIN_BRANCH (defaults to 'main')
- TICKET_NUMBER (optional; will be extracted from branch if not provided)
- PUSH_RELEASE_BRANCH (true|1 to push the created branch)
- PROJECT_TYPE (optional; backend/frontend)

Outputs:
- On created branch: prints a single JSON line to stdout {"created_branch":"...","version":"..."}
- Writes the branch name to new_branch.txt for backwards compatibility.

Exit codes:
0 success (created or no-op), 2 = no version change, 1 = error
"""

import os
import sys
import json
import subprocess
from datetime import datetime

try:
    import tomllib as _toml

    def toml_loads(s):
        return _toml.loads(s)
except Exception:
    try:
        import toml

        def toml_loads(s):
            return toml.loads(s)
    except Exception:
        toml_loads = None


def git_show(branch, path):
    if not branch:
        return None
    refs = [branch]
    if branch.startswith("refs/heads/"):
        refs.append(branch[len("refs/heads/"):])
    elif not branch.startswith("refs/"):
        refs.append(f"refs/heads/{branch}")
    for ref in refs:
        try:
            out = subprocess.check_output(["git", "show", f"{ref}:{path}"], stderr=subprocess.DEVNULL)
            return out.decode()
        except subprocess.CalledProcessError:
            continue
    return None


def parse_semver(v):
    if not v:
        return None
    core = v.split("-", 1)[0]
    parts = []
    for p in core.split("."):
        try:
            parts.append(int(p))
        except Exception:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def compare_semver(a, b):
    pa, pb = parse_semver(a), parse_semver(b)
    if pa is None or pb is None:
        return None
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def read_pkg_version_from_content(content):
    if not content:
        return None
    try:
        return json.loads(content).get("version")
    except Exception:
        return None


def read_toml_version_from_content(content):
    if not content or not toml_loads:
        return None
    try:
        return toml_loads(content).get("tool", {}).get("poetry", {}).get("version")
    except Exception:
        return None


def extract_ticket(branch_or_env):
    if not branch_or_env:
        return "TICKET"
    b = branch_or_env
    if b.startswith("refs/heads/"):
        b = b[len("refs/heads/") :]
    if "/" in b:
        part = b.split("/", 1)[1]
    else:
        part = b
    ticket = part.split("-", 1)[0]
    return ticket or "TICKET"


def create_release_branch(base_branch, ticket, version, push=False):
    date = datetime.utcnow().strftime("%Y%m%d")
    branch_name = f"release/{ticket}-{version}-{date}"
    try:
        subprocess.check_call(["git", "checkout", base_branch])
    except subprocess.CalledProcessError:
        # try short name fallback
        if "/" in base_branch:
            short = base_branch.split("/")[-1]
            try:
                subprocess.check_call(["git", "checkout", short])
            except subprocess.CalledProcessError:
                return None
        else:
            return None
    try:
        subprocess.check_call(["git", "checkout", "-b", branch_name])
        if push:
            subprocess.check_call(["git", "push", "-u", "origin", branch_name])
        # write backwards-compatible file
        try:
            with open("new_branch.txt", "w") as f:
                f.write(branch_name)
        except Exception:
            pass
        return branch_name
    except subprocess.CalledProcessError:
        return None


def main():
    feature_branch = os.getenv("FEATURE_BRANCH") or os.getenv("BUILD_SOURCEBRANCHNAME") or os.getenv("BUILD_SOURCEBRANCH") or "feature/xxx"
    main_branch = os.getenv("MAIN_BRANCH") or "main"
    ticket_env = os.getenv("TICKET_NUMBER") or feature_branch
    push = os.getenv("PUSH_RELEASE_BRANCH", "false").lower() in ("1", "true", "yes")
    project_type = os.getenv("PROJECT_TYPE") or "backend"

    pkg_main = read_pkg_version_from_content(git_show(main_branch, "package.json"))
    pkg_feat = read_pkg_version_from_content(git_show(feature_branch, "package.json"))
    toml_main = read_toml_version_from_content(git_show(main_branch, "project.toml"))
    toml_feat = read_toml_version_from_content(git_show(feature_branch, "project.toml"))

    cmp_pkg = compare_semver(pkg_main, pkg_feat) if (pkg_main or pkg_feat) else None
    cmp_toml = compare_semver(toml_main, toml_feat) if (toml_main or toml_feat) else None

    changed = False
    if cmp_pkg is not None and cmp_pkg < 0:
        changed = True
    if cmp_toml is not None and cmp_toml < 0:
        changed = True

    if changed:
        ticket = extract_ticket(ticket_env)
        version = pkg_feat or toml_feat or "0.0.0"
        new_branch = create_release_branch(feature_branch, ticket, version, push=push)
        if new_branch:
            out = {"created_branch": new_branch, "version": version, "project": project_type}
            print(json.dumps(out))
            return 0
        else:
            print(json.dumps({"error": "failed_creating_branch"}), file=sys.stderr)
            return 1
    else:
        print(json.dumps({"changed": False}))
        return 2


if __name__ == "__main__":
    rc = main()
    sys.exit(rc)