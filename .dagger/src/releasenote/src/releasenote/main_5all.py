import json
import re
import base64
import dagger
from datetime import datetime
from dagger import dag, function, object_type

@object_type
class Releasenote:
    @function
    async def check_and_release(
        self,
        source: dagger.Directory,
        token: dagger.Secret,
        prod_repo: str,
        source_branch: str,
        user_name: str = "mrudhulm",
        default_prod_branch: str = "main",

        # Jira settings
        jira_mode: str = "mock",  # mock | cloud
        jira_base_url: str = "http://localhost:3000",

        # Jira Cloud settings
        jira_cloud_domain: str = "",
        jira_email: str = "",
        jira_api_token: dagger.Secret | None = None,
    ) -> str:

        # -----------------------------
        # 1. Setup Auth + Git/Jira Container
        # -----------------------------
        plain_token = await token.plaintext()
        clean_url = prod_repo.replace("https://", "").split("@")[-1]
        auth_url = f"https://{user_name}:{plain_token}@{clean_url}"

        container = (
            dag.container()
            .from_("python:3.11-slim")
            .with_exec(["apt-get", "update"])
            .with_exec(["apt-get", "install", "-y", "git", "jq", "curl"])
            .with_exec(["git", "clone", auth_url, "/repo"])
            .with_workdir("/repo")
        )

        # -----------------------------
        # 2. Extract Jira ID from branch
        # -----------------------------
        jira_match = re.search(r"([A-Z]+-\d+)", source_branch)
        jira_id = jira_match.group(1) if jira_match else "BACKEND"

        # -----------------------------
        # 3. Semantic version validation
        # -----------------------------
        def is_valid_semver(version: str) -> bool:
            semver_pattern = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
            return re.match(semver_pattern, version) is not None

        # -----------------------------
        # 4. Detect version
        # -----------------------------
        async def get_version(ref: str) -> str | None:
            # pyproject.toml
            try:
                content = await container.with_exec(
                    ["git", "show", f"origin/{ref}:pyproject.toml"]
                ).stdout()
                match = re.search(
                    r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE
                )
                if match:
                    return match.group(1)
            except:
                pass

            # package.json
            try:
                content = await container.with_exec(
                    ["git", "show", f"origin/{ref}:package.json"]
                ).stdout()
                pkg = json.loads(content)
                return pkg.get("version")
            except:
                return None

        # -----------------------------
        # 5. Jira API Helpers
        # -----------------------------
        async def jira_get(key: str) -> dict | None:
            if jira_mode == "mock":
                cmd = f"curl -s '{jira_base_url}/issues?key={key}'"
            else:
                api_token = await jira_api_token.plaintext()
                auth = base64.b64encode(f"{jira_email}:{api_token}".encode()).decode()
                cmd = (
                    f"curl -s -H 'Authorization: Basic {auth}' "
                    f"-H 'Accept: application/json' "
                    f"https://{jira_cloud_domain}/rest/api/3/issue/{key}"
                )

            resp = await container.with_exec(["sh", "-c", cmd]).stdout()
            if not resp.strip():
                return None

            try:
                data = json.loads(resp)
                if jira_mode == "mock" and isinstance(data, list):
                    return data[0] if data else None
                return data
            except:
                return None

        async def jira_create(key: str, version: str) -> dict:
            payload = {
                "key": key,
                "summary": f"Release {version} for {key}",
                "status": "Done",
                "type": "Task",
                "fixVersion": version,
            }

            if jira_mode == "mock":
                cmd = (
                    f"curl -s -X POST '{jira_base_url}/issues' "
                    f"-H 'Content-Type: application/json' "
                    f"-d '{json.dumps(payload)}'"
                )
            else:
                api_token = await jira_api_token.plaintext()
                auth = base64.b64encode(f"{jira_email}:{api_token}".encode()).decode()
                cloud_payload = {
                    "fields": {
                        "project": {"key": key.split("-")[0]},
                        "summary": payload["summary"],
                        "issuetype": {"name": "Task"},
                        "description": f"Auto-created by release pipeline for version {version}",
                    }
                }
                cmd = (
                    f"curl -s -X POST "
                    f"-H 'Authorization: Basic {auth}' "
                    f"-H 'Content-Type: application/json' "
                    f"-d '{json.dumps(cloud_payload)}' "
                    f"https://{jira_cloud_domain}/rest/api/3/issue"
                )

            resp = await container.with_exec(["sh", "-c", cmd]).stdout()
            try:
                return json.loads(resp)
            except:
                return payload

        async def jira_mark_done(issue: dict) -> dict:
            if jira_mode == "mock":
                issue_id = issue.get("id")
                payload = {**issue, "status": "Done"}
                cmd = (
                    f"curl -s -X PUT '{jira_base_url}/issues/{issue_id}' "
                    f"-H 'Content-Type: application/json' "
                    f"-d '{json.dumps(payload)}'"
                )
            else:
                key = issue["key"]
                api_token = await jira_api_token.plaintext()
                auth = base64.b64encode(f"{jira_email}:{api_token}".encode()).decode()

                # Jira Cloud transition to Done
                transition_payload = {"transition": {"id": "31"}}  # "Done" transition ID
                cmd = (
                    f"curl -s -X POST "
                    f"-H 'Authorization: Basic {auth}' "
                    f"-H 'Content-Type: application/json' "
                    f"-d '{json.dumps(transition_payload)}' "
                    f"https://{jira_cloud_domain}/rest/api/3/issue/{key}/transitions"
                )

            resp = await container.with_exec(["sh", "-c", cmd]).stdout()
            return issue

        # -----------------------------
        # 6. Git fetch + version logic
        # -----------------------------
        await container.with_exec(["git", "fetch", "origin"])

        prod_v = await get_version(default_prod_branch)
        feat_v = await get_version(source_branch)

        if not feat_v:
            return f"❌ No valid version found on branch {source_branch}."

        if not is_valid_semver(feat_v):
            return f"❌ Invalid semantic version: {feat_v}"

        if feat_v == prod_v:
            return f"✅ No release needed. Version unchanged."

        # -----------------------------
        # 7. Jira: fetch or create, then mark Done
        # -----------------------------
        jira_issue = await jira_get(jira_id)

        if not jira_issue:
            jira_issue = await jira_create(jira_id, feat_v)
        else:
            jira_issue = await jira_mark_done(jira_issue)

        # -----------------------------
        # 8. Build release notes
        # -----------------------------
        summary = jira_issue.get("summary", "N/A")
        status = jira_issue.get("status", "Done")
        issue_type = jira_issue.get("type", "Task")

        release_notes = f"""
# Release {feat_v}
Jira: {jira_id}

## Issue
- Key: {jira_id}
- Summary: {summary}
- Status: {status}
- Type: {issue_type}
"""

        # -----------------------------
        # 9. Create release metadata
        # -----------------------------
        date_str = datetime.now().strftime("%Y%m%d")
        new_branch = f"release/{jira_id}-{feat_v}-{date_str}"
        new_tag = f"v{feat_v}"

        # -----------------------------
        # 10. Git: branch + tag (safe)
        # -----------------------------
        await (
            container
            .with_exec(["git", "config", "user.name", user_name])
            .with_exec(["git", "config", "user.email", f"{user_name}@dev.azure.com"])
            .with_exec(["git", "checkout", f"origin/{source_branch}"])

            # Safe branch creation
            .with_exec([
                "sh", "-c",
                f"""
                if git ls-remote --heads origin {new_branch} | grep {new_branch}; then
                    git fetch origin {new_branch}:{new_branch};
                    git checkout {new_branch};
                    git pull --ff-only origin {new_branch} || echo 'No FF merge';
                else
                    git checkout -b {new_branch};
                fi
                """
            ])

            # Write release notes
            .with_exec([
                "sh", "-c",
                f"cat << 'EOF' > RELEASE_NOTES.md\n{release_notes}\nEOF"
            ])
            .with_exec(["git", "add", "RELEASE_NOTES.md"])
            .with_exec([
                "sh", "-c",
                f"git commit -m 'chore: release {feat_v}' || echo 'No changes'"
            ])

            # Tag
            .with_exec([
                "sh", "-c",
                f"git tag {new_tag} 2>/dev/null || echo 'Tag exists'"
            ])

            # Push
            .with_exec(["git", "push", "origin", new_branch])
            .with_exec(["git", "push", "origin", "--tags"])
            .stdout()
        )

        # -----------------------------
        # 11. Output
        # -----------------------------
        return json.dumps({
            "status": "success",
            "jira_mode": jira_mode,
            "jira": jira_id,
            "jira_issue": jira_issue,
            "version": feat_v,
            "release_branch": new_branch,
            "tag": new_tag,
        }, indent=2)
