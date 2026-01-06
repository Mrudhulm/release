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
        # 1. Setup Auth + Git Container
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
        # 2. RESOLVE JIRA URL (Fix Connection Issues)
        # -----------------------------
        # Inside Dagger, 'localhost' refers to the container. 
        # 'host.docker.internal' refers to your Mac/PC.
        resolved_jira_url = jira_base_url
        if jira_mode == "mock" and "localhost" in jira_base_url:
            test_host = jira_base_url.replace("localhost", "host.docker.internal")
            # Test if host.docker.internal is reachable
            check_cmd = f"curl -s -o /dev/null -w '%{{http_code}}' --connect-timeout 2 {test_host}/issues || echo 'failed'"
            result = await container.with_exec(["sh", "-c", check_cmd]).stdout()
            
            if result.strip() != "failed":
                resolved_jira_url = test_host

        # -----------------------------
        # 3. Jira API Helpers
        # -----------------------------
        async def jira_request(method: str, path: str, payload: dict = None) -> dict | None:
            if jira_mode == "mock":
                url = f"{resolved_jira_url}{path}"
                headers = ["-H", "Content-Type: application/json"]
            else:
                url = f"https://{jira_cloud_domain}/rest/api/3{path}"
                api_token = await jira_api_token.plaintext()
                auth = base64.b64encode(f"{jira_email}:{api_token}".encode()).decode()
                headers = [
                    "-H", f"Authorization: Basic {auth}",
                    "-H", "Accept: application/json",
                    "-H", "Content-Type: application/json"
                ]

            cmd = ["curl", "-s", "-X", method, url] + headers
            if payload:
                cmd += ["-d", json.dumps(payload)]

            resp = await container.with_exec(cmd).stdout()
            try:
                return json.loads(resp) if resp.strip() else {}
            except:
                return {"raw_response": resp}

        # -----------------------------
        # 4. Version & Jira ID Detection
        # -----------------------------
        jira_match = re.search(r"([A-Z]+-\d+)", source_branch)
        jira_id = jira_match.group(1) if jira_match else "BACKEND"

        async def get_version(ref: str) -> str | None:
            for file in ["pyproject.toml", "package.json"]:
                try:
                    content = await container.with_exec(["git", "show", f"origin/{ref}:{file}"]).stdout()
                    if file == "pyproject.toml":
                        match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
                        if match: return match.group(1)
                    else:
                        return json.loads(content).get("version")
                except: continue
            return None

        await container.with_exec(["git", "fetch", "origin"])
        prod_v = await get_version(default_prod_branch)
        feat_v = await get_version(source_branch)

        if not feat_v or feat_v == prod_v:
            return f"✅ No release needed. Version {feat_v} is already in production."

        # -----------------------------
        # 5. Process Jira Ticket
        # -----------------------------
        # Fetch existing or create new
        issue = await jira_request("GET", f"/issue/{jira_id}" if jira_mode == "cloud" else f"/issues?key={jira_id}")
        
        # If it's a list (mock mode), take the first item
        if jira_mode == "mock" and isinstance(issue, list):
            issue = issue[0] if issue else None

        if not issue or "key" not in str(issue):
            # Create if missing
            issue = await jira_request("POST", "/issue" if jira_mode == "cloud" else "/issues", {
                "fields" if jira_mode == "cloud" else "summary": {
                    "project": {"key": jira_id.split("-")[0]},
                    "summary": f"Release {feat_v} for {jira_id}",
                    "issuetype": {"name": "Task"}
                } if jira_mode == "cloud" else f"Release {feat_v} for {jira_id}"
            })
        
        # Mark as Done and Update Fix Version
        if jira_mode == "cloud":
            await jira_request("POST", f"/issue/{jira_id}/transitions", {"transition": {"id": "31"}})
        
        # -----------------------------
        # 6. Git Operations (Branch, Tag, Push)
        # -----------------------------
        date_str = datetime.now().strftime("%Y%m%d")
        new_branch = f"release/{jira_id}-{feat_v}-{date_str}"
        new_tag = f"v{feat_v}"
        
        release_notes = f"# Release {feat_v}\n- Jira: {jira_id}\n- Date: {date_str}\n- Summary: {issue.get('summary', 'Auto-generated')}"

        await (
            container
            .with_exec(["git", "config", "user.name", user_name])
            .with_exec(["git", "config", "user.email", f"{user_name}@dev.azure.com"])
            .with_exec(["git", "checkout", f"origin/{source_branch}"])
            .with_exec(["git", "checkout", "-b", new_branch])
            .with_exec(["sh", "-c", f"cat << 'EOF' > RELEASE_NOTES.md\n{release_notes}\nEOF"])
            .with_exec(["git", "add", "RELEASE_NOTES.md"])
            .with_exec(["git", "commit", "-m", f"chore: release {feat_v}"])
            .with_exec(["git", "tag", new_tag])
            .with_exec(["git", "push", "origin", new_branch, "--tags"])
            .stdout()
        )

        return json.dumps({
            "status": "success",
            "version": feat_v,
            "branch": new_branch,
            "tag": new_tag,
            "jira_url": resolved_jira_url if jira_mode == "mock" else jira_cloud_domain
        }, indent=2)