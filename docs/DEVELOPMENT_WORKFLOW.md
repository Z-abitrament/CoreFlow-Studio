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

## Overnight Autonomous Run Checklist
Before an unattended run:

- Confirm the user requested implementation, not planning only.
- Confirm the target milestone and stop at that milestone.
- Confirm git is initialized and the starting status is understood.
- Confirm network-dependent commands may request approval.
- Avoid hardware access, destructive git commands, and parameter-write workflows unless explicitly requested.
- Make checkpoint commits after coherent passing slices.
- Leave a final summary with completed work, tests, blockers, and commit hashes.

## Permission Checklist
Expected safe permissions for M0:

- Workspace file writes inside this repository.
- Local git commands: `git init`, `git add`, `git commit`, `git status`, `git diff`, and `git log`.
- Conda environment creation and updates from `environment.yml`.
- Test execution with pytest.

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
