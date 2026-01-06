# Release Automation — Version Check & Release Branch Creation

This repository contains Azure Pipelines templates and a small Python script that:

- Compares semantic versions in `package.json` and `project.toml` between `main` and a `feature/*` branch.
- If the feature branch has a higher version, it creates `release/{ticket}-{version}-YYYYMMDD`.

Files updated by this change:

- `daggerpipeline/dagger-release.py` — robust, env-driven script that emits JSON on creation.
- `cicd/shared/dagger-call.yml` — runs the script (via Dagger if available) and captures output.
- `cicd/template/azure-pipeline.yml` — reusable pipeline template that calls the shared step.
- `project/azure-pipeline.yml` — project pipeline that passes parameters to the template.

Quick local run (mac):

1. Ensure you have the repo and recent branches:

```bash
cd /Users/aiml/Documents/azure_repo/releasepipeline-cicd/releasepipeline-cicd
git fetch origin main feature/xxx --depth=1
```

2. Run the checker locally (example):

```bash
FEATURE_BRANCH=feature/xxx TICKET_NUMBER=PROJ-123 python3 -u daggerpipeline/dagger-release.py
```

3. To test the pipeline template locally, run the script directly. In CI the pipeline will set `BUILD_SOURCEBRANCHNAME`.

Notes:
- The script will prefer `package.json` version if present; otherwise `project.toml` (Poetry) is used.
- To push created release branches from CI set `PUSH_RELEASE_BRANCH=true` and ensure pipeline has push credentials.
- For TOML support, run on Python 3.11+ (tomllib) or install the `toml` package in the agent.

If you want me to also add unit tests, or change version precedence (choose `project.toml` over `package.json`), tell me and I'll update the code accordingly.
# Release Automation Project

This project automates the release process by checking for version changes in `package.json` or `project.toml` when comparing feature branches with the main branch. If changes are detected, a new branch is created following the naming convention `release/ticketnumber-versionid-yyyyddmm`.

## Project Structure

```
release-automation
├── project
│   └── azure-pipeline.yml
├── cicd
│   ├── template
│   │   └── azure-pipeline.yml
│   └── shared
│       └── dagger-call.yml
├── daggerpipeline
│   └── dagger-release.py
├── src
│   ├── checker.py
│   └── __init__.py
├── package.json
├── project.toml
├── requirements.txt
├── .gitignore
└── README.md
```

## Instructions

1. Clone the repository:
   ```
   git clone <repository-url>
   cd release-automation
   ```

2. Install the required Python packages:
   ```
   pip install -r requirements.txt
   ```

3. Configure Azure DevOps with the necessary permissions to run the pipeline.

4. Trigger the Azure Pipeline to check for version changes. This can be done through the Azure DevOps interface or by pushing changes to a feature branch.

5. If changes are detected in `package.json` or `project.toml`, a new branch will be created automatically following the naming convention `release/ticketnumber-versionid-yyyyddmm`.

## Additional Notes

- Ensure that your Azure DevOps environment is properly set up to run the pipeline.
- Review the `dagger-release.py` script for details on how version comparisons are performed.
- The pipeline configuration files in the `project` and `cicd` directories can be customized as needed for your specific workflow.