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
        )

        # -----------------------------
        # 2. RESOLVE JIRA URL (For Local Mocking)
        # -----------------------------
        resolved_jira_url = jira_base_url
        if jira_mode == "mock" and "localhost" in jira_base_url:
            test_host = jira_base_url.replace("localhost", "host.docker.internal")
            check = await container.with_exec(["sh", "-c", f"curl -s -o /dev/null -w '%{{http_code}}' --connect-timeout 2 {test_host}/issues || echo 'fail'"]).stdout()
            if "200" in check:
                resolved_jira_url = test_host

        container = container.with_exec(["git", "clone", auth_url, "/repo"]).with_workdir("/repo")

        # -----------------------------
        # 3. Extract Jira ID & Version Logic
        # -----------------------------
        jira_match = re.search(r"([A-Z]+-\d+)", source_branch)
        jira_id = jira_match.group(1) if jira_match else "BACKEND"

        def is_valid_semver(version: str) -> bool:
            return re.match(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$", version) is not None

        async def get_version(ref: str) -> str | None:
            for file in ["pyproject.toml", "package.json"]:
                try:
                    content = await container.with_exec(["git", "show", f"origin/{ref}:{file}"]).stdout()
                    if "toml" in file:
                        m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
                        if m: return m.group(1)
                    else:
                        return json.loads(content).get("version")
                except: continue
            return None

        await container.with_exec(["git", "fetch", "origin"])
        prod_v = await get_version(default_prod_branch)
        feat_v = await get_version(source_branch)

        if not feat_v or not is_valid_semver(feat_v):
            return f"❌ Invalid or missing version: {feat_v}"
        if feat_v == prod_v:
            return f"✅ No release needed. Version {feat_v} is current."

        # -----------------------------
        # 4. Jira API Helpers
        # -----------------------------
        async def jira_req(method: str, path: str, body: dict = None) -> dict:
            if jira_mode == "mock":
                url = f"{resolved_jira_url}{path}"
                headers = []
            else:
                url = f"https://{jira_cloud_domain}/rest/api/3{path}"
                atoken = await jira_api_token.plaintext()
                auth = base64.b64encode(f"{jira_email}:{atoken}".encode()).decode()
                headers = ["-H", f"Authorization: Basic {auth}", "-H", "Accept: application/json"]
            
            cmd = ["curl", "-s", "-X", method, url, "-H", "Content-Type: application/json"] + headers
            if body: cmd += ["-d", json.dumps(body)]
            
            r = await container.with_exec(cmd).stdout()
            try: return json.loads(r)
            except: return {"status": "ok"}

        # Fetch/Update Jira
        jira_issue = await jira_req("GET", f"/issues?key={jira_id}" if jira_mode == "mock" else f"/issue/{jira_id}")
        if jira_mode == "mock" and isinstance(jira_issue, list): jira_issue = jira_issue[0] if jira_issue else {}
        
        if not jira_issue or "key" not in str(jira_issue):
            # Create Task
            create_payload = {"fields": {"project": {"key": jira_id.split("-")[0]}, "summary": f"Release {feat_v}", "issuetype": {"name": "Task"}}} if jira_mode == "cloud" else {"key": jira_id, "summary": f"Release {feat_v}"}
            jira_issue = await jira_req("POST", "/issue" if jira_mode == "cloud" else "/issues", create_payload)
        
        # -----------------------------
        # 5. Git: Safe Branching & Tagging
        # -----------------------------
        date_str = datetime.now().strftime("%Y%m%d")
        new_branch = f"release/{jira_id}-{feat_v}-{date_str}"
        new_tag = f"v{feat_v}"
        
        release_notes = f"# Release {feat_v}\nJira: {jira_id}\nSummary: {jira_issue.get('summary', 'N/A')}"

        await (
            container
            .with_exec(["git", "config", "user.name", user_name])
            .with_exec(["git", "config", "user.email", f"{user_name}@dev.azure.com"])
            .with_exec(["git", "checkout", f"origin/{source_branch}"])
            .with_exec(["sh", "-c", f"git checkout -b {new_branch} || (git fetch origin {new_branch} && git checkout {new_branch})"])
            .with_exec(["sh", "-c", f"echo '{release_notes}' > RELEASE_NOTES.md"])
            .with_exec(["git", "add", "RELEASE_NOTES.md"])
            .with_exec(["sh", "-c", "git commit -m 'chore: release notes' || echo 'no changes'"])
            # SAFE TAGGING: Check if exists before creating
            .with_exec(["sh", "-c", f"git rev-parse {new_tag} >/dev/null 2>&1 || git tag {new_tag}"])
            .with_exec(["git", "push", "origin", new_branch, "--tags", "--force"])
            .stdout()
        )

        return json.dumps({"status": "success", "version": feat_v, "branch": new_branch, "tag": new_tag}, indent=2)