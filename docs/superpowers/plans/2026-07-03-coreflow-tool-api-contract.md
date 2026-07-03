# CoreFlow Tool API Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a machine-readable CoreFlow Studio API manifest and JSON Modbus raw-frame CLI output for future local tool wrappers.

**Architecture:** Keep execution in the existing `coreflow.modbus_api` raw-frame API and expose discovery through a small `coreflow.app.api_manifest` module. Extend `src/coreflow/__main__.py` with `--api-manifest` and `--modbus-json` while preserving existing text output.

**Tech Stack:** Python standard library JSON, argparse, pytest, existing CoreFlow Modbus fake tests.

---

## File Structure

- Create `src/coreflow/app/api_manifest.py`: JSON-serializable capability manifest.
- Modify `src/coreflow/__main__.py`: add CLI flags and JSON output handling.
- Modify `tests/test_packaging.py`: CLI parser and `main()` JSON-mode tests.
- Modify `tests/test_modbus_api.py`: manifest helper assertions if useful.
- Modify `docs/MODBUS_API.md`: document manifest and JSON output.
- Modify `docs/PROTOCOLS.md`: mention machine-readable contract.
- Modify `docs/TEST_PLAN.md`: add manifest/JSON output protocol test expectations.

### Task 1: API Manifest Module

**Files:**
- Create: `src/coreflow/app/api_manifest.py`
- Test: `tests/test_packaging.py`

- [ ] **Step 1: Write the failing manifest test**

Add this test to `tests/test_packaging.py`:

```python
def test_api_manifest_cli_prints_machine_readable_contract(capsys) -> None:
    from coreflow import __version__
    from coreflow.__main__ import main

    assert main(["--api-manifest"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["application"]["name"] == "CoreFlow Studio"
    assert payload["application"]["package"] == "coreflow"
    assert payload["application"]["version"] == __version__

    raw_frame = next(
        capability
        for capability in payload["capabilities"]
        if capability["id"] == "modbus.raw_frame"
    )
    assert raw_frame["python_api"]["import"] == "coreflow.modbus_api.ModbusRawClient"
    assert "python -m coreflow --modbus-raw" in raw_frame["cli"]["source_command"]
    assert "CoreFlowStudioConsole.exe --modbus-raw" in raw_frame["cli"]["packaged_command"]
    assert "guarded calibration workflows" in " ".join(raw_frame["safety"])
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `python -m pytest tests/test_packaging.py::test_api_manifest_cli_prints_machine_readable_contract -q`

Expected: FAIL because `--api-manifest` is not a recognized argument.

- [ ] **Step 3: Implement the manifest module and CLI flag**

Create `src/coreflow/app/api_manifest.py`:

```python
"""Machine-readable local API contract for CoreFlow Studio integrations."""

from __future__ import annotations

from coreflow import __version__


def build_api_manifest() -> dict[str, object]:
    """Return the supported local API capabilities as JSON-ready data."""

    return {
        "schema_version": 1,
        "application": {
            "name": "CoreFlow Studio",
            "package": "coreflow",
            "version": __version__,
        },
        "capabilities": [
            {
                "id": "modbus.raw_frame",
                "stability": "stable",
                "summary": (
                    "Send one local Modbus RTU raw frame through the same transport "
                    "path used by the standalone Modbus Module."
                ),
                "python_api": {
                    "import": "coreflow.modbus_api.ModbusRawClient",
                    "method": "send_raw_frame",
                },
                "cli": {
                    "source_command": "python -m coreflow --modbus-raw",
                    "packaged_command": "CoreFlowStudioConsole.exe --modbus-raw",
                    "json_flag": "--modbus-json",
                },
                "arguments": [
                    {"name": "frame", "required": True, "type": "hex-string-or-bytes"},
                    {"name": "port", "required": True, "type": "serial-port"},
                    {"name": "unit_id", "required": False, "type": "integer"},
                    {"name": "append_crc", "required": False, "type": "boolean"},
                ],
                "output_modes": ["text", "json"],
                "examples": [
                    {
                        "shell": (
                            "python -m coreflow --modbus-raw \"01 03 00 3D 00 02\" "
                            "--modbus-port COM9 --modbus-unit 1 --modbus-auto-crc "
                            "--modbus-json"
                        )
                    }
                ],
                "safety": [
                    (
                        "This is a local diagnostics and lab automation surface, "
                        "not a remote-control service."
                    ),
                    (
                        "Callers can explicitly send Modbus write function codes; "
                        "guarded calibration workflows and audited parameter writes "
                        "remain separate."
                    ),
                ],
                "limitations": [
                    "Requires a local PC that owns the USB-to-serial adapter.",
                    "Does not load production calibration formulas or acceptance thresholds.",
                ],
            }
        ],
    }
```

Modify `src/coreflow/__main__.py`:

```python
parser.add_argument(
    "--api-manifest",
    action="store_true",
    help="Print the machine-readable local CoreFlow API manifest and exit.",
)
```

In `main()` before operational commands:

```python
if args.api_manifest:
    import json
    from coreflow.app.api_manifest import build_api_manifest

    print(json.dumps(build_api_manifest(), indent=2, sort_keys=True))
    return 0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_packaging.py::test_api_manifest_cli_prints_machine_readable_contract -q`

Expected: PASS.

### Task 2: JSON Modbus Raw CLI Output

**Files:**
- Modify: `src/coreflow/__main__.py`
- Test: `tests/test_packaging.py`

- [ ] **Step 1: Write failing success and failure tests**

Add these tests to `tests/test_packaging.py`:

```python
def test_modbus_raw_json_output_prints_parseable_success(monkeypatch, capsys) -> None:
    from coreflow.__main__ import main

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def send_raw_frame(self, frame, *, append_crc=False):
            assert frame == "01 03 00 3D 00 02"
            assert append_crc is True
            return bytes.fromhex("01 03 04 3B E1 72 D8 83 DB")

    monkeypatch.setattr("coreflow.modbus_api.ModbusRawClient", FakeClient)

    assert (
        main(
            [
                "--modbus-raw",
                "01 03 00 3D 00 02",
                "--modbus-port",
                "COM9",
                "--modbus-unit",
                "1",
                "--modbus-auto-crc",
                "--modbus-json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "capability": "modbus.raw_frame",
        "request": {
            "frame": "01 03 00 3D 00 02",
            "append_crc": True,
            "port": "COM9",
            "unit_id": 1,
        },
        "response_hex": "01 03 04 3B E1 72 D8 83 DB",
    }


def test_modbus_raw_json_output_prints_parseable_error(monkeypatch, capsys) -> None:
    from coreflow.__main__ import main
    from coreflow.modbus_api import ModbusCommunicationError

    class FailingClient:
        def __init__(self, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def send_raw_frame(self, frame, *, append_crc=False):
            raise ModbusCommunicationError("COM9 denied")

    monkeypatch.setattr("coreflow.modbus_api.ModbusRawClient", FailingClient)

    assert (
        main(
            [
                "--modbus-raw",
                "01 03 00 3D 00 02",
                "--modbus-port",
                "COM9",
                "--modbus-json",
            ]
        )
        == 2
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["capability"] == "modbus.raw_frame"
    assert payload["error"] == "COM9 denied"
    assert payload["request"]["port"] == "COM9"
```

- [ ] **Step 2: Run both tests to verify they fail**

Run: `python -m pytest tests/test_packaging.py::test_modbus_raw_json_output_prints_parseable_success tests/test_packaging.py::test_modbus_raw_json_output_prints_parseable_error -q`

Expected: FAIL because `--modbus-json` is not recognized.

- [ ] **Step 3: Implement `--modbus-json`**

Modify parser in `src/coreflow/__main__.py`:

```python
parser.add_argument(
    "--modbus-json",
    action="store_true",
    help="Print --modbus-raw result as machine-readable JSON.",
)
```

In the `--modbus-raw` branch:

```python
request_payload = {
    "frame": args.modbus_raw,
    "append_crc": args.modbus_auto_crc,
    "port": args.modbus_port,
    "unit_id": args.modbus_unit,
}
```

On success:

```python
if args.modbus_json:
    print(json.dumps({
        "ok": True,
        "capability": "modbus.raw_frame",
        "request": request_payload,
        "response_hex": bytes_to_hex(response),
    }, sort_keys=True))
else:
    print(bytes_to_hex(response))
```

On `ValueError` or `ModbusCommunicationError`:

```python
if args.modbus_json:
    print(json.dumps({
        "ok": False,
        "capability": "modbus.raw_frame",
        "request": request_payload,
        "error": str(exc),
    }, sort_keys=True))
else:
    print(f"Modbus raw frame failed: {exc}", file=sys.stderr)
return 2
```

- [ ] **Step 4: Run the JSON tests to verify they pass**

Run: `python -m pytest tests/test_packaging.py::test_modbus_raw_json_output_prints_parseable_success tests/test_packaging.py::test_modbus_raw_json_output_prints_parseable_error -q`

Expected: PASS.

- [ ] **Step 5: Run existing text-output test**

Run: `python -m pytest tests/test_packaging.py::test_modbus_raw_cli_uses_client -q`

Expected: PASS with existing uppercase hex text output.

### Task 3: Documentation Updates

**Files:**
- Modify: `docs/MODBUS_API.md`
- Modify: `docs/PROTOCOLS.md`
- Modify: `docs/TEST_PLAN.md`

- [ ] **Step 1: Update Modbus API docs**

Add sections for `--api-manifest` and `--modbus-json`, including one success JSON example and one statement that failures return JSON with `ok: false` and exit code `2`.

- [ ] **Step 2: Update protocol/test docs**

In `docs/PROTOCOLS.md`, mention the machine-readable manifest under Scriptable Modbus API.

In `docs/TEST_PLAN.md`, add test expectations for manifest discovery and JSON raw-frame output under `TP-PROTO-001`.

- [ ] **Step 3: Check docs for consistency**

Run: `rg -n "api-manifest|modbus-json|modbus.raw_frame" docs src tests`

Expected: Matching references in code, tests, and docs.

### Task 4: Verification And Commit

**Files:**
- All changed files.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests/test_packaging.py::test_api_manifest_cli_prints_machine_readable_contract tests/test_packaging.py::test_modbus_raw_json_output_prints_parseable_success tests/test_packaging.py::test_modbus_raw_json_output_prints_parseable_error tests/test_packaging.py::test_modbus_raw_cli_uses_client tests/test_modbus_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run broader packaging/API tests**

Run:

```powershell
python -m pytest tests/test_packaging.py tests/test_modbus_api.py -q
```

Expected: PASS.

- [ ] **Step 3: Run version policy check**

Run: `python scripts/check_version_update.py`

Expected: PASS. If the policy requires a version bump because `src/` changed, bump `pyproject.toml` and `src/coreflow/__init__.py` together.

- [ ] **Step 4: Inspect diff**

Run: `git diff --stat`

Expected: Only manifest, CLI, tests, and docs changed.

- [ ] **Step 5: Commit**

Run:

```powershell
git add src\coreflow\app\api_manifest.py src\coreflow\__main__.py tests\test_packaging.py docs\MODBUS_API.md docs\PROTOCOLS.md docs\TEST_PLAN.md pyproject.toml src\coreflow\__init__.py
git commit -m "feat(api): expose local tool manifest"
```

If the Windows `sh` hook issue recurs, first run `python scripts/check_version_update.py` manually and only then use `git commit --no-verify -m "feat(api): expose local tool manifest"`.
