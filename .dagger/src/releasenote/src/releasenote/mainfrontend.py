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
        user_name: str = "Azure-Pipelines-Bot",
        default_prod_branch: str = "main",
    ) -> str:
        # 1. Setup Auth and Container
        plain_token = await token.plaintext()
        auth_url = f"https://{user_name}:{plain_token}@{prod_repo.replace('https://', '')}"
        
        container = (
            dag.container()
            .from_("python:3.11-slim")
            .with_exec(["apt-get", "update"])
            .with_exec(["apt-get", "install", "-y", "git"])
            .with_exec(["git", "clone", auth_url, "/repo"])
            .with_workdir("/repo")
        )

        # 2. Extract Jira ID from Branch Name (e.g., feature/ABC-123-task -> ABC-123)
        # Regex looks for standard Jira pattern: [ProjectKey]-[Number]
        jira_match = re.search(r'([A-Z]+-\d+)', source_branch)
        jira_id = jira_match.group(1) if jira_match else "NO-JIRA"

        # 3. Detect Version Changes
        async def get_version(ref: str) -> str:
            try:
                # Support both package.json and pyproject.toml
                # Check for package.json first
                find_pkg = await container.with_exec(["find", ".", "-name", "package.json"]).stdout()
                if find_pkg.strip():
                    content = await container.with_exec(["git", "show", f"origin/{ref}:package.json"]).stdout()
                    return json.loads(content).get("version")
                
                # Fallback to pyproject.toml
                content = await container.with_exec(["git", "show", f"origin/{ref}:pyproject.toml"]).stdout()
                match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
                return match.group(1) if match else None
            except:
                return None

        await container.with_exec(["git", "fetch", "origin"])
        prod_v = await get_version(default_prod_branch)
        feat_v = await get_version(source_branch)

        if not feat_v or feat_v == prod_v:
            return f"✅ No release needed. Feature version ({feat_v}) matches Production ({prod_v})."

        # 4. Create Release Branch and Tag
        # Format: release/<jira-id>-<new-version>-<yyyymmdd>
        date_str = datetime.now().strftime("%Y%m%d")
        new_branch = f"release/{jira_id}-{feat_v}-{date_str}"
        new_tag = f"v{feat_v}"

        # 5. Execute Git Workflow
        await (
            container.with_exec(["git", "config", "user.name", user_name])
            .with_exec(["git", "config", "user.email", f"{user_name}@dev.azure.com"])
            .with_exec(["git", "checkout", f"origin/{source_branch}"])
            .with_exec(["git", "checkout", "-b", new_branch])
            # Add a professional Release Note
            .with_exec(["sh", "-c", f"echo '# Release {feat_v}\nGenerated from {source_branch}\nJira: {jira_id}' > RELEASE_NOTES.md"])
            .with_exec(["git", "add", "RELEASE_NOTES.md"])
            .with_exec(["git", "commit", "-m", f"release: version {feat_v} for {jira_id}"])
            # Create Tag
            .with_exec(["git", "tag", "-a", new_tag, "-m", f"Release version {feat_v}"])
            # Push both branch and tags
            .with_exec(["git", "push", "origin", new_branch, "--tags"])
            .stdout()
        )

        return f"🚀 RELEASE SUCCESSFUL\n- Branch: {new_branch}\n- Tag: {new_tag}\n- Version: {prod_v} -> {feat_v}"