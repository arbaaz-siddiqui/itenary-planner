# scripts/

Operational and developer-facing scripts. None of these are imported by the
application; they're command-line utilities.

| File | What it does |
|---|---|
| `diagnose_apis.py` | Probes every Technoheaven endpoint, saves raw responses to `data/samples/`. Use after credential rotation or to spot API drift. |
| `validate_against_samples.py` | Runs all 7 parsers against `/mnt/user-data/uploads/api_samples.json`. Shows parsed output per endpoint — quick way to verify parser behavior after changes. |
| `refresh_test_fixtures.py` | Trims `api_samples.json` into the minimal slices used by `tests/fixtures/`. Run after the staging API changes. |
| `grade_benchmarks.py` | Runs evaluation prompts against the agent and reports pass/fail. |
