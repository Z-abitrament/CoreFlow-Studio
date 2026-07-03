# CoreFlow Codex Plugin Wrapper Design

## Goal

Create a personal local Codex plugin that exposes CoreFlow Studio's local API
manifest and Modbus raw-frame CLI as searchable Codex tools.

## Scope

This phase creates a Codex-side wrapper. It does not add new CoreFlow Studio
hardware behavior, calibration workflows, register maps, or write-guard paths.
The wrapper depends on the phase-one CoreFlow CLI contract:

- `python -m coreflow --api-manifest`
- `python -m coreflow --modbus-raw ... --modbus-json`

The plugin is intended for this local Windows development machine. It should be
installed through the personal marketplace so future Codex threads can discover
it with local tool search.

## Recommended Destination

Use a personal plugin named `coreflow-studio-tools`.

Default locations:

- Plugin source: `C:\Users\admin\plugins\coreflow-studio-tools`
- Personal marketplace: `C:\Users\admin\.agents\plugins\marketplace.json`

The marketplace entry should use the standard personal-plugin local source path:

```json
"path": "./plugins/coreflow-studio-tools"
```

## Architecture

The plugin contains:

- `.codex-plugin/plugin.json` for plugin metadata.
- `.mcp.json` for registering a local stdio MCP server.
- `scripts/coreflow_mcp_server.py` as the MCP server implementation.
- Optional `skills/coreflow-studio-tools/SKILL.md` with usage guidance for when
  a user asks to operate CoreFlow Studio from Codex.

The MCP server will be a small Python stdio server that shells out to the
CoreFlow CLI through the `coreflow-studio` conda environment. It should avoid
direct imports from the CoreFlow checkout so the wrapper can report environment
or checkout problems clearly.

The server configuration should pass the CoreFlow workspace path explicitly:

```text
E:\Agentic_AI_Lab\CoreFlow Studio
```

## Tools

### `coreflow_api_manifest`

Inputs:

- none

Behavior:

- Runs `conda run -n coreflow-studio python -m coreflow --api-manifest` in the
  CoreFlow workspace.
- Parses stdout as JSON.
- Returns the JSON manifest as structured tool output.

### `coreflow_modbus_raw_frame`

Inputs:

- `frame`: hex string.
- `port`: COM port string.
- `unit_id`: integer, default `1`.
- `baudrate`: integer, default `19200`.
- `parity`: `N`, `E`, or `O`, default `N`.
- `stop_bits`: `1` or `2`, default `1`.
- `timeout_s`: float, default `3.0`.
- `retries`: integer, default `3`.
- `append_crc`: boolean, default `false`.
- `dry_run`: boolean, default `true`.

Behavior:

- When `dry_run` is true, returns the exact command it would run and does not
  open the serial port.
- When `dry_run` is false, runs `python -m coreflow --modbus-raw ... --modbus-json`
  through the `coreflow-studio` conda environment.
- Parses stdout as JSON and returns the parsed payload.
- If the CLI exits nonzero or emits non-JSON output, returns an MCP error with
  the exit code, stdout, and stderr summarized.

Safety:

- The tool description must state that raw Modbus frames can include write
  function codes supplied by the caller.
- `dry_run` defaults to true so discovery and command construction do not touch
  hardware by accident.
- The wrapper does not claim to enforce calibration write-guard rules; guarded
  calibration workflows remain inside CoreFlow Studio.

## Error Handling

The MCP server should return clear errors for:

- Missing `conda` executable.
- Missing or broken `coreflow-studio` environment.
- Missing CoreFlow workspace.
- CoreFlow CLI returning nonzero.
- CoreFlow CLI returning invalid JSON.

Errors should include enough context for debugging without dumping unlimited
process output.

## Validation

Before handoff:

- Validate the plugin manifest with the plugin-creator validator.
- Run the MCP script's built-in smoke test or a small local CLI test for
  manifest retrieval.
- Confirm `coreflow_api_manifest` can retrieve `schema_version: 1` and
  `modbus.raw_frame` from the current CoreFlow checkout.
- Confirm `coreflow_modbus_raw_frame` dry-run returns a command without opening
  a COM port.

## Installation

The personal marketplace entry should be created by the plugin scaffold flow.
After validation, reinstall or install the plugin from the personal marketplace
using the marketplace name read from `C:\Users\admin\.agents\plugins\marketplace.json`.

Users should test the newly installed tools in a new Codex thread so plugin
metadata and MCP tools are loaded fresh.
