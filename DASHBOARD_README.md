# Debit Note Intelligence dashboard

The handover bundle contains valid parser logic under mismatched filenames. The new dashboard is intentionally isolated from those originals.

## Run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r dashboard_requirements.txt
.venv/bin/python -m streamlit run streamlit_app.py
```

The virtual environment is already prepared in this workspace, so only the final command is needed here.

The app supports Reliance Retail, More Retail, Vishal/Airplaza, and Tesco/Trent PDF formats recovered from the handed-over rules. Uploaded files are processed in memory; the dashboard does not move, archive, or register them automatically.
