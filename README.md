# Kobra FC Team Sorter

Automated, balanced team draw for Kobra FC. It reads a player list from `lista.txt`, fetches player level and type from a Google Sheet, and optimizes team assignment to keep team averages as close as possible and prefer similar level distributions when feasible.

## Features
- Exact team balancing via ILP (PuLP/CBC)
- Priority: equalize team level sums/averages; secondary: prefer similar per-level composition
- Randomization to avoid input-order bias
- Detailed logs: team averages and per-player levels per team
- Supports 15–18 players (commonly 18 → 3 teams of 6)

## Requirements
- Python 3.10+
- Packages: `pulp`, `gspread`, `google-auth`

Install:
```powershell
python -m pip install --upgrade pip
python -m pip install pulp gspread google-auth
```

## Credentials (Google Service Account) – do NOT commit
This project uses a Google Service Account to access Google Sheets.

1) In Google Cloud Console:
- Enable APIs: “Google Sheets API” and “Google Drive API”
- Create a Service Account and generate a JSON key (download the JSON file)
- Share your target Google Sheet with the Service Account email (Viewer is enough)

2) Place the JSON key in the project root and keep it out of Git. Either:
- Name it exactly like the current code expects: `lofty-mark-407721-c78e211dcebf.json`, or
- Update the path in `team_sorter.py` (Credentials.from_service_account_file) to match your filename

The `.gitignore` in this repo excludes typical secret patterns and the current credential filename so you don’t accidentally commit it.

## Spreadsheet expectations
- The code opens the spreadsheet by ID and uses the third worksheet (index 2):
  - `open_by_key('1GRC7L_2Q75OEywLz-gFU6jrcKLhpgA3-1LZ_qFviFsY')`
  - `get_worksheet(2)`
- Expected headers include: `ID`, `Nome do Jogador`, `Apelido`, `Tipo de Jogador`, `NOVO FORMS MODA`, etc. See `expected_headers` in `team_sorter.py`.
- Player matching uses the `Apelido` column (lowercased) to find `NOVO FORMS MODA` (level 1–4, where 1 is best) and `Tipo de Jogador` (e.g., Mensalista/Avulso).

## Input list format (`lista.txt`)
The script parses nicknames from lines like:
```
01. Guimma
02. Gazolla
...
```
Two digits, a dot, a space, then the nickname. One player per line.

## Running
```powershell
python team_sorter.py
```
Output includes:
- Printed teams in the existing WhatsApp-friendly format
- Logs with team averages and detailed roster levels

## How balancing works (summary)
- Optimize to minimize the maximum difference between team level sums (balances averages)
- Tie-breakers prefer sums near the exact target and gently prefer similar level distributions per team
- Randomness: shuffling player order and team display order avoids bias from input order

## Customization
- Spreadsheet ID and worksheet index: edit at the top of `team_sorter.py`
- Credentials file path: update `Credentials.from_service_account_file(...)`
- Logging level/format: see `logging.basicConfig(...)` in `team_sorter.py`

## Troubleshooting
- “File not found” for credentials: ensure the JSON file exists at the path the code expects
- “Level not found”: ensure the `Apelido` in `lista.txt` matches the `Apelido` column in the sheet
- Access issues: share the spreadsheet with the Service Account email
- Dependency errors: reinstall packages with the commands in Requirements; prefer a clean venv
