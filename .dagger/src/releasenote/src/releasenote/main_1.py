import json
import re
import dagger
# Import the 'dag' object which is the entry point for all Dagger calls
from dagger import dag, function, object_type

@object_type
class Releasenote:
    @function
    async def check_and_release(
        self,
        source: dagger.Directory,
        token: dagger.Secret,
    ) -> str:
        """
        Detects version changes between main and current branch.
        Creates a release branch if a change is found.
        """
        # Use 'dag' instead of 'dagger' for the container entry point
        container = (
            dag.container()
            .from_("python:3.11-slim")
            .with_exec(["apt-get", "update"])
            .with_exec(["apt-get", "install", "-y", "git"])
            .with_mounted_directory("/src", source)
            .with_workdir("/src")
            .with_secret_variable("GITHUB_TOKEN", token)
        )

        # Fetch main to compare files
        container = container.with_exec(["git", "fetch", "origin", "main"])

        # Helper function to extract versions using 'await' on .stdout()
        async def get_version(cont: dagger.Container, ref: str, filename: str) -> str:
            try:
                # We chain from the container and await the final stdout() string
                content = await (
                    cont.with_exec(["git", "show", f"{ref}:{filename}"])
                    .stdout()
                )
                
                if filename == "package.json":
                    return json.loads(content).get("version")
                elif "toml" in filename:
                    match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
                    return match.group(1) if match else None
            except Exception:
                return None
            return None

        # Run the comparisons
        main_ver = await get_version(container, "origin/main", "package.json")
        curr_ver = await get_version(container, "HEAD", "package.json")

        if not curr_ver or curr_ver == main_ver:
            return f"No version change detected. Main: {main_ver}, Current: {curr_ver}"

        # 3. If version changed, create the release branch
        release_branch = f"release/{curr_ver}"
        
        # We must await the final execution
        await (
            container.with_exec(["git", "config", "--global", "user.email", "dagger@local"])
            .with_exec(["git", "config", "--global", "user.name", "Dagger Bot"])
            .with_exec(["git", "checkout", "-b", release_branch])
            # .with_exec(["git", "push", "origin", release_branch])
            .stdout()
        )

        return f"✅ Detected version bump from {main_ver} to {curr_ver}. Created branch {release_branch}"