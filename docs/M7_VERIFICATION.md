# M7 Verification

## Scope
M7 implements initial error and stability analysis modules. It does not implement final production acceptance thresholds, report rendering, UI visualization, or advanced signal-processing algorithms.

## Implemented
- Error analysis models and `analyze_error`.
- Absolute error, relative error, mean absolute error, maximum absolute error, mean relative error, and maximum absolute relative error.
- Near-zero reference policies: `absolute_only`, `epsilon`, and `raise`.
- Time-series sample model and CSV loader for persisted mass-flow artifacts.
- Stability analysis models and `analyze_stability`.
- Mean, population standard deviation, value range, drift estimate, dropout count, and configurable pass/fail decision.
- Recompute path from persisted CSV data.

## Commands Run
```powershell
conda run -n coreflow-studio python -m pytest
```

## Results
- Pytest passed: 53 tests passed.
- M0 through M6 tests still pass.
- M7 calculation tests cover `TP-CALC-001` and `TP-CALC-002`.

## Notes
- Thresholds are configuration inputs; no production acceptance thresholds are hard-coded.
- Stability calculations use initial workflow-ready metrics. Higher-fidelity signal processing remains future work.
