# CoreFlow Codex Plugin Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a personal local Codex plugin named `coreflow-studio-tools` that exposes CoreFlow Studio API-manifest and raw Modbus CLI wrapper tools.

**Architecture:** Use the plugin-creator scaffold for a personal marketplace-backed plugin under `C:\Users\admin\plugins\coreflow-studio-tools`. Add a stdio MCP server implemented in Python that shells out to `conda run -n coreflow-studio python -m coreflow ...` inside `E:\Agentic_AI_Lab\CoreFlow Studio`.

**Tech Stack:** Codex plugin manifest, MCP stdio JSON-RPC, Python standard library, CoreFlow Studio CLI, conda.

---

## File Structure

- Create `C:\Users\admin\plugins\coreflow-studio-tools\.codex-plugin\plugin.json`: plugin metadata with `mcpServers` and `skills`.
- Create `C:\Users\admin\plugins\coreflow-studio-tools\.mcp.json`: local stdio MCP server configuration.
- Create `C:\Users\admin\plugins\coreflow-studio-tools\scripts\coreflow_mcp_server.py`: MCP server and CLI smoke entry point.
- Create `C:\Users\admin\plugins\coreflow-studio-tools\skills\coreflow-studio-tools\SKILL.md`: concise usage and safety guidance.
- Update `C:\Users\admin\.agents\plugins\marketplace.json`: personal marketplace entry created by scaffold helper.
- Validate with `C:\Users\admin\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py`.

### Task 1: Scaffold Personal Plugin

**Files:**
- Create: `C:\Users\admin\plugins\coreflow-studio-tools\.codex-plugin\plugin.json`
- Create: `C:\Users\admin\plugins\coreflow-studio-tools\.mcp.json`
- Create: `C:\Users\admin\plugins\coreflow-studio-tools\scripts\`
- Create: `C:\Users\admin\plugins\coreflow-studio-tools\skills\`
- Modify: `C:\Users\admin\.agents\plugins\marketplace.json`

- [ ] **Step 1: Run scaffold command**

Run from the plugin-creator skill root:

```powershell
python scripts\create_basic_plugin.py coreflow-studio-tools --with-skills --with-scripts --with-mcp --with-marketplace
```

Expected: creates plugin source under `C:\Users\admin\plugins\coreflow-studio-tools` and updates the personal marketplace.

- [ ] **Step 2: Validate scaffold**

Run:

```powershell
python scripts\validate_plugin.py C:\Users\admin\plugins\coreflow-studio-tools
```

Expected: PASS or only errors caused by intentionally incomplete scaffold companion files that the next task replaces.

### Task 2: MCP Server Script

**Files:**
- Create/Modify: `C:\Users\admin\plugins\coreflow-studio-tools\scripts\coreflow_mcp_server.py`

- [ ] **Step 1: Write the MCP server**

Use a focused Python script with:

- constants:
  - `COREFLOW_WORKSPACE = os.environ.get("COREFLOW_WORKSPACE", r"E:\Agentic_AI_Lab\CoreFlow Studio")`
  - `COREFLOW_CONDA_ENV = os.environ.get("COREFLOW_CONDA_ENV", "coreflow-studio")`
- JSON-RPC methods:
  - `initialize`
  - `tools/list`
  - `tools/call`
- tools:
  - `coreflow_api_manifest`
  - `coreflow_modbus_raw_frame`
- smoke CLI:
  - `--smoke-manifest`
  - `--smoke-dry-run`

The `coreflow_modbus_raw_frame` implementation must default `dry_run` to true and return the command without opening a COM port.

- [ ] **Step 2: Run manifest smoke**

Run:

```powershell
python C:\Users\admin\plugins\coreflow-studio-tools\scripts\coreflow_mcp_server.py --smoke-manifest
```

Expected: JSON includes `schema_version: 1` and `modbus.raw_frame`.

- [ ] **Step 3: Run dry-run smoke**

Run:

```powershell
python C:\Users\admin\plugins\coreflow-studio-tools\scripts\coreflow_mcp_server.py --smoke-dry-run
```

Expected: JSON includes `dry_run: true`, a command array, and no serial-port access.

### Task 3: Plugin Manifests And Skill

**Files:**
- Modify: `C:\Users\admin\plugins\coreflow-studio-tools\.codex-plugin\plugin.json`
- Modify: `C:\Users\admin\plugins\coreflow-studio-tools\.mcp.json`
- Create: `C:\Users\admin\plugins\coreflow-studio-tools\skills\coreflow-studio-tools\SKILL.md`

- [ ] **Step 1: Update plugin metadata**

Set plugin metadata:

```json
{
  "name": "coreflow-studio-tools",
  "version": "0.1.0",
  "description": "Local CoreFlow Studio tools for Codex.",
  "author": {
    "name": "CoreFlow Studio Team"
  },
  "skills": "./skills/",
  "mcpServers": "./.mcp.json",
  "interface": {
    "displayName": "CoreFlow Studio Tools",
    "shortDescription": "Call local CoreFlow Studio CLI tools from Codex.",
    "longDescription": "Provides local Codex tools for reading the CoreFlow Studio API manifest and constructing or sending Modbus raw-frame CLI calls through the coreflow-studio conda environment.",
    "developerName": "CoreFlow Studio Team",
    "category": "Productivity",
    "capabilities": ["Tools", "Local"],
    "defaultPrompt": "Use CoreFlow Studio tools."
  }
}
```

- [ ] **Step 2: Update `.mcp.json`**

Use stdio server configuration that runs:

```json
{
  "mcpServers": {
    "coreflow-studio-tools": {
      "command": "python",
      "args": [
        "C:\\Users\\admin\\plugins\\coreflow-studio-tools\\scripts\\coreflow_mcp_server.py"
      ],
      "env": {
        "COREFLOW_WORKSPACE": "E:\\Agentic_AI_Lab\\CoreFlow Studio",
        "COREFLOW_CONDA_ENV": "coreflow-studio"
      }
    }
  }
}
```

- [ ] **Step 3: Add skill guidance**

Create a skill with frontmatter:

```markdown
---
name: coreflow-studio-tools
description: Use when the user asks Codex to inspect or call CoreFlow Studio local tools, read the CoreFlow API manifest, or construct/send a Modbus RTU raw frame through the local CoreFlow CLI.
---
```

Include these rules:

- Prefer `coreflow_api_manifest` before calling other CoreFlow tools.
- Use `coreflow_modbus_raw_frame` with `dry_run: true` unless the user explicitly asks to access a COM port.
- Warn that raw Modbus frames may include write function codes and do not replace guarded calibration workflows.

### Task 4: Validate And Install Metadata

**Files:**
- Existing plugin and marketplace files.

- [ ] **Step 1: Validate plugin**

Run:

```powershell
python C:\Users\admin\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py C:\Users\admin\plugins\coreflow-studio-tools
```

Expected: `Plugin validation passed`.

- [ ] **Step 2: Read marketplace name**

Run:

```powershell
python C:\Users\admin\.codex\skills\.system\plugin-creator\scripts\read_marketplace_name.py
```

Expected: prints `personal` or the actual personal marketplace name.

- [ ] **Step 3: Update cachebuster for install visibility**

Run:

```powershell
python C:\Users\admin\.codex\skills\.system\plugin-creator\scripts\update_plugin_cachebuster.py C:\Users\admin\plugins\coreflow-studio-tools
```

Expected: plugin version becomes `0.1.0+codex.<timestamp>`.

- [ ] **Step 4: Re-validate plugin**

Run:

```powershell
python C:\Users\admin\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py C:\Users\admin\plugins\coreflow-studio-tools
```

Expected: `Plugin validation passed`.

### Task 5: Handoff

**Files:**
- None.

- [ ] **Step 1: Summarize created paths**

Report plugin path, marketplace path, smoke results, and validation result.

- [ ] **Step 2: Tell user how to load it**

Provide the command with the actual marketplace name printed by Task 4 Step 2.
For example, when the script prints `personal`, provide:

```powershell
codex plugin add coreflow-studio-tools@personal
```

Then say to start a new Codex thread to pick up the plugin tools.
