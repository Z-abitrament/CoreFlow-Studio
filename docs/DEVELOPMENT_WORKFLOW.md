# Development Workflow

## Summary
CoreFlow Studio uses a local-first development workflow. v1 implementation should begin with a local git repository, small checkpoint commits, deterministic tests, and simulator-first behavior.

## Local Git Workflow
- Initialize git before implementation work if `.git` is absent.
- Keep the main working tree reviewable with small coherent commits.
- Use local commits even when no remote repository exists.
- Do not rewrite history, delete user changes, or run destructive git cleanup commands without explicit user approval.
- Prefer one commit per completed milestone slice or verified checkpoint.

Recommended first setup:

```powershell
git init
git status --short
```

If git identity is missing, set it locally for this repository:

```powershell
git config user.name "CoreFlow Codex"
git config user.email "codex@local.invalid"
```

## Branch And Commit Conventions
- Use `main` for the local primary branch unless the user asks for another branch.
- Use Conventional Commits for all non-WIP commits.
- Commit only coherent changes that can be explained in one sentence.
- Do not commit failing or half-written work unless the commit message clearly starts with `WIP:`.
- Keep the project software version in `pyproject.toml` and `src/coreflow/__init__.py` synchronized.
- Before every commit, run the version check or install the repository Git hook so staged code and packaging changes are reviewed for a required version bump.
- Before handing back, report commit hashes and test results.

Recommended commit format:

```text
<type>(optional-scope): <imperative summary>
```

Allowed common types:

- `feat`: user-facing feature.
- `fix`: bug fix.
- `docs`: documentation-only change.
- `test`: test-only change.
- `refactor`: behavior-preserving code restructuring.
- `build`: packaging, dependency, or build-system change.
- `ci`: CI or automation change.
- `chore`: repository maintenance.

Examples:

```text
docs(workflow): document conda setup
build(windows): use conda environment for packaging
fix(packaging): hide console for UI executable
```

## Versioning
The current project software version is `0.9.0`. It is defined in exactly two
places and both must match:

- `pyproject.toml` under `[project].version`.
- `src/coreflow/__init__.py` as `__version__`.

Use semantic versioning for the application version:

- Patch version for bug fixes, documentation shipped with the app, packaging
  fixes, and low-risk internal corrections.
- Minor version for new user-visible workflows, UI capabilities, protocol
  support, storage/report formats, or hardware-module features.
- Major version for incompatible storage, workflow, protocol, or operator
  behavior changes after a stable release line exists.

Every non-documentation commit must explicitly consider whether the version
should change. The repository includes a pre-commit check that blocks staged
`src/` or `packaging/` changes unless both version files are part of the commit.
Documentation-only, test-only, and local test-tooling configuration commits may
keep the same version.

Install the hook once per checkout:

```powershell
git config core.hooksPath .githooks
```

Run the same check manually when needed:

```powershell
python scripts/check_version_update.py
```

## Release Automation
Version commits are not automatically published by default. To publish the
current committed version as a GitHub Release, run:

```powershell
.\scripts\release.ps1
```

The script reads the synchronized version, requires a clean working tree, builds
and verifies `dist\CoreFlowStudio`, creates full and compatible patch update
assets, creates and pushes the version tag, and uploads the GitHub Release
assets.

To make version commits publish automatically after commit, enable the
repository-local post-commit switch:

```powershell
git config core.hooksPath .githooks
git config coreflow.autoRelease true
```

With that switch enabled, the post-commit hook runs `scripts\release.ps1 -Yes`
only when the commit changes both version files. Turn it off with:

```powershell
git config coreflow.autoRelease false
```

Keep automatic release disabled while making experimental version commits or
when GitHub/network access is unavailable. The release script can be rerun after
a transient upload failure as long as the GitHub Release was not created.

## Overnight Autonomous Run Checklist
Before an unattended run:

- Confirm the user requested implementation, not planning only.
- Confirm the target milestone and stop at that milestone.
- Confirm git is initialized and the starting status is understood.
- Confirm network-dependent commands may request approval.
- Avoid hardware access, destructive git commands, and parameter-write workflows unless explicitly requested.
- Make checkpoint commits after coherent passing slices.
- Let the pre-commit version check run before each checkpoint commit and update the version when the commit changes shipped behavior or packaging.
- Leave a final summary with completed work, tests, blockers, and commit hashes.

## UI Bug-Fix Workflow
When a bug report is about a missing control, invisible field, stale label, or
history/detail display, treat the report as a full user-path problem rather
than only a data-layer problem.

- Reproduce or inspect the exact user path first: menu/action, dialog open,
  control visibility, save action, result/history refresh, and packaged app if
  that is what the operator is running.
- Add or update a Qt test that follows the same user path. Prefer clicking the
  public button or action that opens the dialog, then assert the label text,
  input widget visibility, placeholder/help text when relevant, saved value,
  and downstream history/detail display.
- Do not count direct access to an internal widget as sufficient coverage for a
  visibility bug. Internal-widget tests can supplement, but at least one test
  must prove the operator can find the control from the UI.
- For metadata that moves through several layers, verify the complete chain:
  configuration capture, persisted configuration, runtime calculation record,
  database/test-record entry, table column, detail panel, export/report if
  applicable.
- If the user is validating a Windows packaged build under `dist/`, compare the
  `dist\CoreFlowStudio\CoreFlowStudio.exe` timestamp or build metadata with the
  changed source files. Rebuild the package before asking the user to recheck
  any UI change that only exists in source.
- After rebuilding `dist/`, run the packaged UI startup smoke check. Use the
  console diagnostics executable with `--ui` and captured stdout/stderr, and
  confirm the app stays alive long enough to prove startup.
- In the handoff, say which executable was tested and whether the result came
  from source tests, the packaged build, or both.

## Permission Checklist
Expected safe permissions for M0:

- Workspace file writes inside this repository.
- Local git commands: `git init`, `git add`, `git commit`, `git status`, `git diff`, and `git log`.
- Local git configuration for repository hooks: `git config core.hooksPath .githooks`.
- Conda environment creation and updates from `environment.yml`.
- Test execution with pytest. Pytest cache and temporary directories should stay under `.tmp/`.

Commands that require explicit approval:

- Network dependency installation if sandboxed network access fails.
- Destructive commands such as `git reset --hard`, `git clean`, or file deletion outside generated caches.
- Any command that accesses physical hardware or writes device parameters.
- ASIO/IIS hardware diagnostics that enumerate or open Windows audio devices, including the BRAVO-HD USB sound-card module.

## Next-Morning Review Checklist
After an overnight run, review:

- Final assistant summary and listed commit hashes.
- `git status --short` for a clean or clearly explained working tree.
- Test output and any skipped or failed checks.
- New or changed documentation against the canonical docs.
- Whether the implementation stopped at the requested milestone.
- Any assumptions or blockers that require human decisions.

## M0 Setup Commands
Use Windows PowerShell from the repository root:

```powershell
conda env create -f environment.yml
conda activate coreflow-studio
python -m pytest
python -m coreflow
```
