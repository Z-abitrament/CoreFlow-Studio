# M10 Verification

## Scope
M10 implements the first flexible experiment extension points and a simulator-backed example experiment. It does not implement production fixture drivers, a complete experiment designer, real ML model execution, or high-rate DSP workflows.

## Implemented
- Experiment definition models for capture plans, processing modules, fixture actions, ML placeholder configuration, and metadata.
- Signal-processing module interface.
- Fixture-control placeholder interface with a no-op controller that records unsupported fixture actions without touching hardware.
- ML inference placeholder interface with a no-op module.
- `BasicSignalStatsModule` example processor for mass-flow statistics.
- Headless `ExperimentWorkflow` that captures simulator samples, stores raw CSV, runs processing, stores processed CSV, records analysis results, and tracks fixture/ML placeholder steps.
- Runtime default experiment launch path.
- Qt UI `Run Experiment` action and result inspection support through existing run history views.

## Commands Run
```powershell
.\.venv\Scripts\python -m pytest tests\test_experiment_workflow.py tests\test_ui_main_window.py -q
.\.venv\Scripts\python -m pytest -q
```

## Results
- M10 focused tests passed: 5 tests passed.
- Full test suite passed: 61 tests passed.
- M10 covers `TP-EXT-001` for simulator-backed experiment capture, sample processing, stored outputs, and isolated fixture/ML placeholders.

## Notes
- Fixture actions marked `required=True` fail before any capture if no real fixture controller is configured.
- The default UI experiment is intentionally small and deterministic.
- ML execution remains a placeholder until a model runtime and validation requirements are specified.
