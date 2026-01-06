import json
import re
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
            .with_exec(["apt-get", "install", "-y", "git", "jq"])
            .with_exec(["git", "clone", auth_url, "/repo"])
            .with_workdir("/repo")
        )

        # -----------------------------
        # 2. Extract Jira ID
        # -----------------------------
        jira_match = re.search(r"([A-Z]+-\d+)", source_branch)
        jira_id = jira_match.group(1) if jira_match else "BACKEND"

        # -----------------------------
        # 3. Detect version from pyproject or package.json
        # -----------------------------
        async def get_version(ref: str) -> str:
            try:
                # Try pyproject.toml
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

            try:
                # Try package.json
                content = await container.with_exec(
                    ["git", "show", f"origin/{ref}:package.json"]
                ).stdout()

                pkg = json.loads(content)
                return pkg.get("version")
            except:
                return None

        await container.with_exec(["git", "fetch", "origin"])
        prod_v = await get_version(default_prod_branch)
        feat_v = await get_version(source_branch)

        if not feat_v or feat_v == prod_v:
            return f"✅ No release needed. {source_branch} version {feat_v} matches {default_prod_branch}."

        # -----------------------------
        # 4. Create release metadata
        # -----------------------------
        date_str = datetime.now().strftime("%Y%m%d")
        new_branch = f"release/{jira_id}-{feat_v}-{date_str}"
        new_tag = f"v{feat_v}"

        # -----------------------------
        # 5. Push release branch + tag
        # -----------------------------
        await (
            container.with_exec(["git", "config", "user.name", user_name])
            .with_exec(["git", "config", "user.email", f"{user_name}@dev.azure.com"])
            .with_exec(["git", "checkout", f"origin/{source_branch}"])
            .with_exec(["git", "checkout", "-b", new_branch])
            .with_exec(
                [
                    "sh",
                    "-c",
                    f"echo '# Release {feat_v}\nJira: {jira_id}' > RELEASE_NOTES.md",
                ]
            )
            .with_exec(["git", "add", "RELEASE_NOTES.md"])
            .with_exec(["git", "commit", "-m", f"chore: release {feat_v}"])
            .with_exec(["git", "tag", new_tag])
            .with_exec(["git", "push", "origin", new_branch, "--tags"])
            .stdout()
        )

        # -----------------------------
        # 6. JSON Output (CI-friendly)
        # -----------------------------
        result = {
            "status": "success",
            "jira": jira_id,
            "version": feat_v,
            "release_branch": new_branch,
            "tag": new_tag,
        }

        return json.dumps(result, indent=2)
