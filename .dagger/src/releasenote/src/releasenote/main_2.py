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
        ticket_id: str,
        prod_repo: str,
        source_branch: str,
        user_name: str = "mrudhulm",
        default_prod_repo: str = "main",
    ) -> str:
        # 1. Setup Auth
        plain_token = await token.plaintext()
        clean_url = prod_repo.replace("https://", "")
        auth_url = f"https://{user_name}:{plain_token}@{clean_url}"

        # 2. Start Logging Stages
        log = ["--- DEBUG LOG START ---"]

        # Setup Container
        container = (
            dag.container()
            .from_("python:3.11-slim")
            .with_exec(["apt-get", "update"])
            .with_exec(["apt-get", "install", "-y", "git"])
        )

        # Stage 1: Clone
        log.append(f"STAGING: Cloning {prod_repo}...")
        container = container.with_exec(["git", "clone", auth_url, ""])
        
        # Move to workdir
        container = container.with_workdir("/repo")

        # Stage 2: Verify Files
        log.append("STAGING: Checking files in root...")
        ls_files = await container.with_exec(["ls", "-F"]).stdout()
        log.append(f"FILES FOUND:\n{ls_files}")

        # Stage 3: Fetch all branches
        log.append("STAGING: Fetching all remote branches...")
        container = container.with_exec(["git", "fetch", "--all"])

        # Stage 4: Version Check Logic
        async def get_version(cont: dagger.Container, ref: str) -> str:
            try:
                # Try to find package.json anywhere in the tree
                find_pkg = await cont.with_exec(["find", ".", "-name", "package.json"]).stdout()
                pkg_path = find_pkg.strip().split('\n')[0].replace("./", "")
                
                log.append(f"STAGING: Reading {ref}:{pkg_path}")
                content = await cont.with_exec(["git", "show", f"{ref}:{pkg_path}"]).stdout()
                return json.loads(content).get("version")
            except Exception as e:
                log.append(f"ERROR reading {ref}: {str(e)}")
                return None

        main_ver = await get_version(container, f"origin/{default_prod_repo}")
        curr_ver = await get_version(container, f"origin/{source_branch}")

        log.append(f"RESULT: Main={main_ver}, Current={curr_ver}")

        if not main_ver or not curr_ver:
            return "\n".join(log) + "\n❌ FAILED: Version detection failed."

        if main_ver == curr_ver:
            return "\n".join(log) + f"\n✅ SKIP: No version change detected ({curr_ver})."

        # Stage 5: Branch and Push
        date_str = datetime.now().strftime("%Y%m%d")
        new_branch = f"release/{ticket_id}-{curr_ver}-{date_str}"
        
        log.append(f"STAGING: Creating branch {new_branch}")
        
        await (
            container.with_exec(["git", "config", "user.name", user_name])
            .with_exec(["git", "config", "user.email", f"{user_name}@dev.azure.com"])
            .with_exec(["git", "checkout", f"origin/{source_branch}"])
            .with_exec(["git", "checkout", "-b", new_branch])
            .with_exec(["sh", "-c", f"echo 'Release {curr_ver}' > RELEASE_NOTES.md"])
            .with_exec(["git", "add", "RELEASE_NOTES.md"])
            .with_exec(["git", "commit", "-m", f"release: {curr_ver}"])
            .with_exec(["git", "push", "origin", new_branch])
            .stdout()
        )

        log.append(f"STAGING: Successfully pushed {new_branch}")
        return "\n".join(log) + "\n🚀 RELEASE COMPLETE!"