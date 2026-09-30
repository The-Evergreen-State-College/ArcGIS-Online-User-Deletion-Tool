# :world_map: ArcGIS-Online User Deletion Tool

Automates the deletion for user accounts in ArcGIS Online by processing the full prerequisite chain required before a user
account can be deleted, then deletes the account via the REST API.

## 📃 Table of Contents

- [:world\_map: ArcGIS-Online User Deletion Tool](#world_map-arcgis-online-user-deletion-tool)
  - [📃 Table of Contents](#-table-of-contents)
  - [📖 Description](#-description)
  - [✅ Features](#-features)
  - [📦 Requirements](#-requirements)
  - [🗂️ Files](#️-files)
  - [📗 Instructions](#-instructions)
    - [1. Configure credentials](#1-configure-credentials)
      - [A. Local credentials in .env file](#a-local-credentials-in-env-file)
      - [B. Python keyring](#b-python-keyring)
    - [2. Queue users for deletion](#2-queue-users-for-deletion)
    - [3. Dry run (recommended)](#3-dry-run-recommended)
    - [4. Run for real](#4-run-for-real)
  - [⚙️ Configuration](#️-configuration)
  - [📓 Logs \& Reports](#-logs--reports)
  - [🤔 Considerations](#-considerations)
  - [:notebook: Change Log](#notebook-change-log)
  - [📜 License](#-license)

## 📖 Description

ESRI ArcGIS Online enterprise instances have no built in mechanism to bulk process the deletion of user accounts.
This is a major pain point for ArcGIS Online enterprise instance administration.

ArcGIS Online will not let you delete a user account until:

1. All content ownership is removed (every item the user owns).
2. All group ownership is removed.
3. All add-on/app-bundle licenses assigned to the user are revoked.

Only then can `POST /sharing/rest/community/users/{username}/delete` succeed.

`delete_users.py` automates all of the above for one or many users at a time,
talking directly to the ArcGIS Online REST API (via an authenticated
[ArcGIS API for Python](https://developers.arcgis.com/python/) session) so the
process is repeatable, auditable, and safe to run in bulk.


> ⚠️ :hammer: **Warning note:** The current iteration of the tool is a sledge hammer!
> It deletes all content a user owns before deletion.


## ✅ Features

- 🧹 Deletes every item a user owns, across every folder (including root),
  then removes the now-empty folders.
- 🔁 **Reassigns** (does not delete) any group the user owns to the admin
  running the script, so shared group content isn't lost.
- 🔐 Revokes every add-on license entitlement assigned to the user.
- 🗑️ Permanently purges items (bypasses the 14-day Recycle Bin) so deletion
  doesn't silently get blocked by retained trash items.
- 🧪 `--dry-run` mode previews every action with zero destructive calls.
- ⌨️ Requires typing `DELETE` to confirm before any real run (skippable with
  `--yes` for scripted/unattended use).
- 📅 Optional **year-based targeting**: set `LAST_LOGON_YEAR=<year>` in
  [`delete_users_by_year.txt`](delete_users_by_year.txt) to auto-queue every
  org member whose last login falls in that year.
- 📝 Every successfully deleted user is appended to
  [`deleted_users.csv`](deleted_users.csv) with date, admin, and last-login
  metadata.
- 📊 A timestamped per-run report (status + notes for every step) is written
  to [`reports/`](reports).
- 🪵 **Structured logging** via Python's `logging` module — every action is
  logged to both STDOUT and a timestamped file under `logs/` for auditing.

## 📦 Requirements

| Dependency | Notes |
|---|---|
| Python 3.10+ | Uses modern type-hint syntax (`str \| None`) |
| [arcgis](https://developers.arcgis.com/python/) | ArcGIS API for Python; see [`requirements.txt`](requirements.txt) |
| [keyring](https://pypi.org/project/keyring/) | Optional; used as a fallback credential source instead of plaintext `credentials.txt` |
| ArcGIS Online admin account | Must have content, group, and license administration privileges |

```powershell
pip install -r requirements.txt
```

## 🗂️ Files

| File | Purpose |
|---|---|
| [`delete_users.py`](delete_users.py) | Main script |
| [`users.csv`](users.csv) | Input queue — one `username` per line |
| [`.env`](.env) | Optional local `ADMIN_USERNAME=`/`ADMIN_PASSWORD=` file (not committed) |
| [`.env.template`](.env.template) | Committed template; copy to .env |
| [`delete_users_by_year.txt`](delete_users_by_year.txt) | Optional `LAST_LOGON_YEAR=<year>` filter |
| [`deleted_users.csv`](deleted_users.csv) | Running audit log of every user this tool has deleted |
| [`reports/`](reports) | Per-run CSV reports (status/notes per user) |
| `logs/` | Timestamped `.log` files (structured logging output, gitignored) |

## 📗 Instructions

### 1. Configure credentials

#### A. Local credentials in .env file
Create `.env` (already `.gitignore`) next to the script:

```ini
admin_username=your_admin_account
admin_password=your_password
```

If this file is missing, or either value is blank, the script falls back to
the OS keyring (via the [`keyring`](https://pypi.org/project/keyring/)
package, service name `arcgis-online-delete-users`) for the password. If
neither the file nor the keyring has a password, you'll get an interactive
prompt (username + masked password).

#### B. Python keyring
To store a password in the keyring instead of `.env`:

```powershell
python -c "import keyring; keyring.set_password('arcgis-online-delete-users', 'your_admin_account', 'your_password')"
```

> ⚠️ **Security note:** storing a plaintext password on disk is a tradeoff for
> automation convenience. Prefer the keyring or the interactive prompt, and
> rotate the admin password if `.env` is ever shared or committed.

### 2. Queue users for deletion

Add one username per line to `users.csv`:

```csv
username
jdoe
asmith
```

### 3. Dry run (recommended)

```powershell
python delete_users.py --url https://geoduck.maps.arcgis.com --input users.csv --dry-run
```

Review the console output and the generated report in `reports/` before
proceeding — this previews every item, folder, group, and license that would
be affected, with zero changes made.

### 4. Run for real

```powershell
python delete_users.py --url https://geoduck.maps.arcgis.com --input users.csv
```

You'll be asked to type `DELETE` to confirm. Pass `--yes` to skip this prompt
for unattended/scheduled runs.

## ⚙️ Configuration

| Flag | Default | Description |
|---|---|---|
| `--url` | *(required)* | ArcGIS Online organization URL |
| `--input` | *(required)* | CSV file with a `username` column |
| `--dry-run` | off | Preview actions without deleting anything |
| `--yes` | off | Skip the typed `DELETE` confirmation |
| `--credentials-file` | `credentials.txt` | Path to the admin credentials file |
| `--deleted-log` | `deleted_users.csv` | Audit log appended to on each successful deletion |
| `--year-file` | `delete_users_by_year.txt` | Optional `LAST_LOGON_YEAR=<year>` auto-queue filter |
| `--logs-dir` | `logs` | Directory for the timestamped `.log` file (in addition to STDOUT) |

## 📓 Logs & Reports

- **Structured log:** `logs/delete_users_<timestamp>.log` — every action is
  logged via Python's `logging` module to both STDOUT and this file
  (`timestamp [LEVEL] message`), for auditing.
- **Per-run report:** `reports/<input>_report_<timestamp>.csv` — one row per
  user with `status` and detailed `notes` for every step.
- **Audit log:** `deleted_users.csv` — one row per successfully deleted user:
  `date`, `admin`, `user`, `Last Login`.

## 🤔 Considerations

- Items with dependent related service items (e.g. Survey123 forms with a
  linked feature service) may fail to delete individually; this does not block
  the final user/profile deletion.
- Groups owned by the target user are **reassigned**, not deleted, to avoid
  disrupting other group members' shared content.
- If a group has **delete protection** or a service item has a dependent
  relationship the admin may need to intervene manually before a retry
  succeeds.
- The ArcGIS Online Recycle Bin (if enabled for the org) retains deleted items
  for 14 days by default; this tool passes `permanentDelete=true` to bypass
  that so a user's content ownership is fully cleared in one pass.

## :notebook: [Change Log](ChangeLog.md)

## 📜 License

[GPL-3.0](LICENSE)
