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
        jira_mode: str = "mock",
        jira_base_url: str = "http://localhost:3000",
    ) -> str:
        # 1. Setup Auth
        plain_token = await token.plaintext()
        clean_url = prod_repo.replace("https://", "").split("@")[-1]
        auth_url = f"https://{user_name}:{plain_token}@{clean_url}"

        # 2. Setup Container and Clone
        container = (
            dag.container()
            .from_("python:3.11-slim")
            .with_exec(["apt-get", "update"])
            .with_exec(["apt-get", "install", "-y", "git", "jq", "curl"])
            .with_exec(["git", "clone", auth_url, "/repo"])
            .with_workdir("/repo")
        )

        # 3. Aggressive Fetching
        # We must fetch the remote references so 'git show' can see them
        container = (
            container.with_exec(["git", "fetch", "origin", default_prod_branch])
            .with_exec(["git", "fetch", "origin", source_branch])
        )

        # 4. Universal Version Extractor
        async def get_version(ref: str) -> str | None:
            # This searches for package.json OR pyproject.toml anywhere in the branch
            find_cmd = f"git ls-tree -r --name-only origin/{ref} | grep -E 'package.json|pyproject.toml'"
            
            try:
                # Find the path
                paths_raw = await container.with_exec(["sh", "-c", find_cmd]).stdout()
                path = paths_raw.strip().split('\n')[0]
                
                if not path:
                    return None

                # Read the file
                content = await container.with_exec(["git", "show", f"origin/{ref}:{path}"]).stdout()

                if "toml" in path:
                    # Poetry version pattern
                    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
                    return match.group(1) if match else None
                else:
                    # Node.js version pattern
                    return json.loads(content).get("version")
            except:
                return None

        # 5. Version Comparison
        prod_v = await get_version(default_prod_branch)
        feat_v = await get_version(source_branch)

        if not feat_v:
            # DEBUG: List all files on the branch if it fails
            debug_files = await container.with_exec(["git", "ls-tree", "-r", "--name-only", f"origin/{source_branch}"]).stdout()
            return f"❌ ERROR: Version file not detected on {source_branch}.\nFiles visible on branch:\n{debug_files}"

        if feat_v == prod_v:
            return f"✅ SKIP: Version {feat_v} is already on {default_prod_branch}."

        # 6. Branch, Tag, and Push
        date_str = datetime.now().strftime("%Y%m%d")
        new_tag = f"v{feat_v}"
        new_branch = f"release/{feat_v}-{date_str}"

        await (
            container.with_exec(["git", "config", "user.name", user_name])
            .with_exec(["git", "config", "user.email", f"{user_name}@dev.azure.com"])
            # Create the release branch based on the feature branch
            .with_exec(["git", "checkout", "-b", new_branch, f"origin/{source_branch}"])
            # Write Release Note
            .with_exec(["sh", "-c", f"echo '# Release {feat_v}\nDate: {date_str}' > RELEASE_NOTES.md"])
            .with_exec(["git", "add", "RELEASE_NOTES.md"])
            .with_exec(["sh", "-c", "git commit -m 'chore: add release notes' || echo 'no changes'"])
            # Safe Tagging
            .with_exec(["sh", "-c", f"git rev-parse {new_tag} >/dev/null 2>&1 || git tag {new_tag}"])
            # Push
            .with_exec(["git", "push", "origin", new_branch, "--tags", "--force"])
            .stdout()
        )

        return f"🚀 SUCCESS: Version bump {prod_v} -> {feat_v} detected. Pushed {new_branch} and {new_tag}."