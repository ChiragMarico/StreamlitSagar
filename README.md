# Debit Note Intelligence — Streamlit in Snowflake

Production-oriented Streamlit dashboard for extracting and reviewing customer debit notes.

## Repository contents

- `streamlit_app.py` — dashboard entry point
- `parser_engine.py` — recovered customer PDF parsers and validation
- `snowflake_backend.py` — Snowflake stages, duplicate registry, run history and audit persistence
- `setup_snowflake.sql` — one-time database, schema, stage and table setup
- `snowflake.yml` — Snowflake CLI deployment manifest
- `pyproject.toml` — container-runtime dependencies

The corrupted original handover artifacts are intentionally excluded by `.gitignore`.

## One-time Snowflake setup

1. Ask a Snowflake administrator to review and run `setup_snowflake.sql`.
2. Create or supply a query warehouse named `DEBIT_NOTE_WH`.
3. Create or supply a compute pool named `DEBIT_NOTE_COMPUTE_POOL`.
4. Grant the application owner access to the database, schema, tables, stages, warehouse and compute pool.
5. Allow the container runtime to install the declared packages, or provide approved wheel files.

If your object names differ, update `snowflake.yml` and set `DN_APP_DATABASE` / `DN_APP_SCHEMA` for the deployed app.

## Local verification

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r dashboard_requirements.txt
.venv/bin/python -m streamlit run streamlit_app.py
```

## Deploy with Snowflake CLI

Install and configure Snowflake CLI, then run:

```bash
snow streamlit deploy debit_note_intelligence --replace --open
```

Before the first deployment, change the placeholder warehouse and compute-pool names in `snowflake.yml` to the objects approved by your Snowflake administrator.

## Deploy from a Git-backed Snowflake Workspace

1. Push this repository to GitHub or another supported provider.
2. In Snowsight, open **Workspaces** and clone/connect the repository.
3. Open `streamlit_app.py` and run the private development preview.
4. Select **Deploy**, choose the database/schema, warehouse, compute pool and team roles.
5. Validate with representative PDFs before granting wider access.

## Security behavior

- Uploaded PDFs are hashed before processing.
- Previously registered file hashes are rejected as duplicates.
- Snowflake runs persist uploaded PDFs and Excel reports in encrypted internal stages.
- Run metadata, extracted records and exceptions are written to application tables.
- Local development does not write to Snowflake.
