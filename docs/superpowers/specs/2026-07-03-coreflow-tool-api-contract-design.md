# CoreFlow Tool API Contract Design

## Goal

Expose a stable, machine-readable CoreFlow Studio API contract from the current
project so scripts and future Codex tooling can discover supported local
capabilities before invoking them.

## Scope

This phase stays inside the CoreFlow Studio repository. It does not create a
Codex plugin, MCP server, HTTP service, or long-running remote-control process.
The deliverable is a source and packaged-console API contract that future
wrappers can call reliably.

The first advertised capability is the existing local Modbus raw-frame path:

- Python API: `coreflow.modbus_api.ModbusRawClient`
- Source CLI: `python -m coreflow --modbus-raw ...`
- Packaged CLI: `CoreFlowStudioConsole.exe --modbus-raw ...`

## Architecture

Add a small API manifest module under `coreflow.app` that returns plain Python
dictionaries suitable for JSON serialization. The command-line entry point will
expose the manifest with `--api-manifest`.

Keep Modbus raw-frame execution in `coreflow.modbus_api`. The CLI will add an
optional JSON output mode for raw-frame requests so external tools can parse
success and error results without scraping human text.

No UI code, workflow code, storage schema, or hardware write-guard behavior is
changed in this phase.

## Public Contract

`--api-manifest` prints a JSON object with:

- `schema_version`: contract format version.
- `application`: name, package, and current CoreFlow version.
- `capabilities`: list of locally callable capabilities.
- For each capability: identifier, stability, summary, safety notes, Python API,
  CLI command names, arguments, output modes, examples, and limitations.

The initial capability identifier is `modbus.raw_frame`.

`--modbus-json` applies only when `--modbus-raw` is used. On success it prints:

```json
{
  "ok": true,
  "capability": "modbus.raw_frame",
  "request": {
    "frame": "01 03 00 3D 00 02",
    "append_crc": true,
    "port": "COM9",
    "unit_id": 1
  },
  "response_hex": "01 03 04 3B E1 72 D8 83 DB"
}
```

On failure it prints a JSON object with `ok: false`, the same capability id, and
an `error` string. The process exit code remains `2` for invalid frames,
connection failures, and Modbus request failures.

Existing text output for `--modbus-raw` remains unchanged unless `--modbus-json`
is supplied.

## Safety

The manifest must state that `modbus.raw_frame` is a local diagnostics and lab
automation surface. It can transmit Modbus write function codes if the caller
explicitly supplies them, but it is not a replacement for guarded calibration
workflows or audited parameter writes.

This phase does not add any automatic write-capable calibration operation. It
does not silently promote placeholder register maps or simulator assumptions to
production hardware behavior.

## Testing

Add tests that:

- Parse `--api-manifest` and verify the JSON names CoreFlow Studio, the current
  package version, and `modbus.raw_frame`.
- Verify the manifest describes both Python and CLI invocation paths.
- Verify `--modbus-json` prints parseable success JSON for a fake raw-frame
  client.
- Verify `--modbus-json` prints parseable failure JSON and returns exit code
  `2` when the fake client raises `ModbusCommunicationError`.
- Verify the existing human-readable Modbus raw-frame output still works.

## Documentation

Update `docs/MODBUS_API.md` with the manifest and JSON output contract. Update
`docs/PROTOCOLS.md` and `docs/TEST_PLAN.md` where they describe scriptable
Modbus API behavior.

## Future Codex Wrapper

After this phase, a separate Codex plugin or MCP wrapper can advertise a local
tool by reading or mirroring `--api-manifest`, then invoke the packaged console
or Python API. That wrapper is intentionally outside this implementation slice.
