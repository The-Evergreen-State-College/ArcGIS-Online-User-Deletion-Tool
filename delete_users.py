"""
Batch-delete ArcGIS Online users, automatically clearing the prerequisites
required by the platform before a user account can be removed:

    1. Remove all content ownership (delete every item the user owns).
    2. Remove all group ownership (delete every group the user owns).
    3. Revoke all add-on/app-bundle licenses assigned to the user.
    4. Delete the user profile.

Usage:
    python delete_users.py --url https://geoduck.maps.arcgis.com --input users.csv
    python delete_users.py --url https://geoduck.maps.arcgis.com --input users.csv --dry-run

The input CSV must have a "username" column (see users.csv for a template).
Requires the ArcGIS API for Python: pip install -r requirements.txt

Optionally, set LAST_LOGON_YEAR=<year> in delete_users_by_year.txt to auto-queue
every org member whose last login falls in that year into --input before running.

Results (success/failure per user, per step) are written to a timestamped
report CSV next to the input file.
"""
import argparse
import csv
import datetime
import getpass
import logging
import os
import sys

from arcgis import gis
from arcgis.gis import GIS
from dotenv import dotenv_values

try:
    import keyring
except ImportError:  # optional dependency, only needed for the keyring fallback
    keyring = None

KEYRING_SERVICE = "arcgis-online-delete-users"

log = logging.getLogger("delete_users")


def setup_logging(logs_dir: str) -> str:
    """Configure logging to STDOUT and a timestamped file under logs_dir; returns the log file path."""
    os.makedirs(logs_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(logs_dir, f"delete_users_{stamp}.log")

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    log.setLevel(logging.INFO)
    log.handlers.clear()
    log.addHandler(stream_handler)
    log.addHandler(file_handler)
    return log_path


def log_note(note: str) -> None:
    """Log a per-step note at a severity that matches its ERROR/FAILED marker, if any."""
    if note.startswith("ERROR"):
        log.error("  - %s", note)
    elif "FAILED" in note:
        log.warning("  - %s", note)
    else:
        log.info("  - %s", note)


def read_credentials(path: str) -> dict[str, str]:
    """Read ADMIN_USERNAME / ADMIN_PASSWORD from a .env file, if present."""
    if not path or not os.path.isfile(path):
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v}


def connect(url: str, credentials_file: str) -> GIS:
    values = read_credentials(credentials_file)
    username = values.get("ADMIN_USERNAME")
    password = values.get("ADMIN_PASSWORD")

    if username and password:
        print(f"Using credentials from {credentials_file}")
    else:
        # .env missing or incomplete: fall back to keyring, then an interactive prompt.
        if not username:
            username = input(f"Admin username for {url}: ").strip()
        password = keyring.get_password(KEYRING_SERVICE, username) if keyring else None
        if password:
            print(f"Using password from OS keyring ({KEYRING_SERVICE}) for {username}")
        else:
            password = getpass.getpass("Admin password: ")

gis = GIS(url, username, password)
    log.info("Connected to %s as %s", url, gis.users.me.username)
    return gis
 


def read_usernames(csv_path: str) -> list[str]:
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "username" not in (reader.fieldnames or []):
            raise ValueError('Input CSV must contain a "username" column')
        return [row["username"].strip() for row in reader if row["username"].strip()]


def read_year_filter(path: str) -> int | None:
    """Read a LAST_LOGON_YEAR=<year> setting from a config file (# lines are comments)."""
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "LAST_LOGON_YEAR" and value.strip():
                return int(value.strip())
    return None


def find_users_by_last_login_year(gis: GIS, year: int) -> list[str]:
    """Return usernames of org members whose last login falls in the given year."""
    matches = []
    for user in gis.users.search(query="", max_users=100000):
        last_login = getattr(user, "lastLogin", -1)
        if last_login and last_login > 0:
            login_year = datetime.datetime.fromtimestamp(last_login / 1000, tz=datetime.timezone.utc).year
            if login_year == year:
                matches.append(user.username)
    return matches


def add_users_to_csv(csv_path: str, usernames: list[str]) -> list[str]:
    """Append usernames not already listed in the CSV; returns the newly added usernames."""
    existing = set(read_usernames(csv_path))
    new_usernames = [u for u in usernames if u not in existing]
    if new_usernames:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for u in new_usernames:
                writer.writerow([u])
    return new_usernames


def _list_folder_items(gis: GIS, username: str, folder_id: str | None) -> list[dict]:
    """Page through content/users/<username>[/<folderId>] via raw REST.

    The arcgis 2.4.3 SDK's folder/item generators are unreliable (they silently
    ignore the folder filter and Folder.list() raises KeyError), so this talks
    to the REST API directly through the already-authenticated gis._con session.
    """
    path = f"content/users/{username}" + (f"/{folder_id}" if folder_id else "")
    items = []
    start = 1
    while start != -1:
        data = gis._con.post(path, {"f": "json", "num": 100, "start": start})
        items.extend(data.get("items", []))
        start = data.get("nextStart", -1)
    return items


def delete_owned_content(gis: GIS, username: str, dry_run: bool) -> list[str]:
    """Delete every item the user owns, across every folder (including root), then the folders."""
    notes = []
    root = gis._con.post(f"content/users/{username}", {"f": "json", "num": 100, "start": 1})
    folders = root.get("folders", [])

    for folder_id, folder_label in [(None, "root")] + [(f["id"], f["title"]) for f in folders]:
        items = _list_folder_items(gis, username, folder_id)
        for item in items:
            label = f'{item["title"]} ({item["id"]}, folder={folder_label})'
            if dry_run:
                notes.append(f"[dry-run] would delete item {label}")
                continue
            try:
                resp = gis._con.post(
                    f"content/users/{username}/items/{item['id']}/delete", {"f": "json", "permanentDelete": True}
                )
                ok = bool(resp.get("success"))
                notes.append(f"deleted item {label}: {'ok' if ok else 'FAILED ' + str(resp)}")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"ERROR deleting item {label}: {exc}")

    for f in folders:
        label = f'{f["title"]} ({f["id"]})'
        if dry_run:
            notes.append(f"[dry-run] would delete folder {label}")
            continue
        try:
            resp = gis._con.post(f"content/users/{username}/{f['id']}/delete", {"f": "json"})
            ok = bool(resp.get("success"))
            notes.append(f"deleted folder {label}: {'ok' if ok else 'FAILED ' + str(resp)}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"ERROR deleting folder {label}: {exc}")

    if not notes:
        notes.append("no owned content found")
    return notes


def delete_owned_groups(gis: GIS, username: str, admin_username: str, dry_run: bool) -> list[str]:
    """Reassign ownership of every group owned by the user to the admin running this script
    (raw REST; see note in delete_owned_content)."""
    notes = []
    data = gis._con.get(f"community/users/{username}", {"f": "json"})
    owned_groups = [g for g in data.get("groups", []) if g.get("owner") == username]
    if not owned_groups:
        notes.append("no owned groups found")
    for group in owned_groups:
        label = f'{group["title"]} ({group["id"]})'
        if dry_run:
            notes.append(f"[dry-run] would reassign group {label} to {admin_username}")
            continue
        try:
            resp = gis._con.post(
                f"community/groups/{group['id']}/reassign", {"f": "json", "targetUsername": admin_username}
            )
            ok = bool(resp.get("success"))
            notes.append(f"reassigned group {label} to {admin_username}: {'ok' if ok else 'FAILED ' + str(resp)}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"ERROR deleting group {label}: {exc}")
    return notes


def revoke_licenses(gis: GIS, user, dry_run: bool) -> list[str]:
    """Revoke every add-on license entitlement assigned to the user."""
    notes = []
    try:
        license_mgr = gis.admin.license
    except Exception as exc:  # noqa: BLE001
        notes.append(f"ERROR accessing license manager: {exc}")
        return notes

    for lic in license_mgr.all():
        title = lic.properties.get("listing", {}).get("title", lic.properties.get("itemId"))
        try:
            user_entitlements = lic.user_entitlement(user.username)
            entitlements = (user_entitlements or {}).get("entitlements", [])
        except Exception as exc:  # noqa: BLE001
            if "not licensed by user" in str(exc):
                continue  # user simply has no entitlements for this app bundle
            notes.append(f"ERROR reading entitlements for {title}: {exc}")
            continue
        if not entitlements:
            continue
        label = f'{title} [{", ".join(entitlements)}]'
        if dry_run:
            notes.append(f"[dry-run] would revoke license {label}")
            continue
        try:
            ok = lic.revoke(username=user.username, entitlements=entitlements, suppress_email=True)
            notes.append(f"revoked license {label}: {'ok' if ok else 'FAILED'}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"ERROR revoking license {label}: {exc}")
    if not notes:
        notes.append("no assigned licenses found")
    return notes


def format_last_login(last_login: int | None) -> str:
    """Convert an ArcGIS epoch-ms lastLogin value to an ISO date, or 'Never'."""
    if not last_login or last_login <= 0:
        return "Never"
    return datetime.datetime.fromtimestamp(last_login / 1000, tz=datetime.timezone.utc).date().isoformat()


def delete_user(gis: GIS, username: str, dry_run: bool) -> dict:
    result = {"username": username, "status": "skipped", "notes": [], "last_login": "unknown"}
    user = gis.users.get(username)
    if user is None:
        result["status"] = "not_found"
        result["notes"].append("user not found in organization")
        return result

    result["last_login"] = format_last_login(getattr(user, "lastLogin", None))
    result["notes"] += delete_owned_content(gis, username, dry_run)
    result["notes"] += delete_owned_groups(gis, username, gis.users.me.username, dry_run)
    result["notes"] += revoke_licenses(gis, user, dry_run)

    if dry_run:
        result["status"] = "dry-run"
        result["notes"].append("[dry-run] would delete user profile")
        return result

    try:
        ok = user.delete()
        result["status"] = "deleted" if ok else "delete_failed"
        result["notes"].append(f"delete user profile: {'ok' if ok else 'FAILED'}")
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["notes"].append(f"ERROR deleting user profile: {exc}")

    return result


def log_deleted_user(deleted_log: str, admin_username: str, username: str, last_login: str) -> None:
    """Append a row to the deleted-users CSV log, creating it with headers if needed."""
    is_new = not os.path.isfile(deleted_log)
    with open(deleted_log, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["date", "admin", "user", "Last Login"])
        writer.writerow([datetime.date.today().isoformat(), admin_username, username, last_login])


def write_report(results: list[dict], input_csv: str) -> str:
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(input_csv)), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.basename(input_csv).rsplit(".", 1)[0]
    report_path = os.path.join(reports_dir, f"{base_name}_report_{stamp}.csv")
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["username", "status", "notes"])
        for r in results:
            writer.writerow([r["username"], r["status"], " | ".join(r["notes"])])
    return report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="ArcGIS Online org URL, e.g. https://geoduck.maps.arcgis.com")
    parser.add_argument("--input", required=True, help="CSV file with a 'username' column")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without deleting anything")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument(
        "--credentials-file",
        default=".env",
        help="Path to a local .env with ADMIN_USERNAME=/ADMIN_PASSWORD= (falls back to keyring, then an interactive prompt if missing)",
    )
    parser.add_argument(
        "--deleted-log",
        default="deleted_users.csv",
        help="CSV file to append date/admin/user rows to for each successfully deleted user",
    )
    parser.add_argument(
        "--year-file",
        default="delete_users_by_year.txt",
        help="Optional file with LAST_LOGON_YEAR=<year>; matching org users are queued into --input",
    )
    parser.add_argument(
        "--logs-dir",
        default="logs",
        help="Directory to write a timestamped .log file to (in addition to STDOUT)",
    )
    args = parser.parse_args()

    log_path = setup_logging(args.logs_dir)
    log.info("Logging to %s", log_path)

    gis = connect(args.url, args.credentials_file)

    year = read_year_filter(args.year_file)
    if year is not None:
        log.info("Year filter active (%s): LAST_LOGON_YEAR=%s", args.year_file, year)
        matches = find_users_by_last_login_year(gis, year)
        added = add_users_to_csv(args.input, matches)
        log.info("Found %d user(s) with last login in %s; added %d new username(s) to %s", len(matches), year, len(added), args.input)

    usernames = read_usernames(args.input)
    if not usernames:
        log.error("No usernames found in input CSV.")
        sys.exit(1)

    log.info("%d user(s) queued for deletion: %s", len(usernames), ", ".join(usernames))
    if not args.dry_run and not args.yes:
        confirm = input("This will PERMANENTLY delete content, groups, licenses, and profiles for these users. Type 'DELETE' to continue: ")
        if confirm.strip() != "DELETE":
            log.warning("Aborted.")
            sys.exit(1)

    results = []
    for username in usernames:
        log.info("=== Processing %s ===", username)
        result = delete_user(gis, username, args.dry_run)
        for note in result["notes"]:
            log_note(note)
        log.info("  Status: %s", result["status"])
        if result["status"] == "deleted":
            log_deleted_user(args.deleted_log, gis.users.me.username, username, result["last_login"])
        results.append(result)

    report_path = write_report(results, args.input)
    log.info("Report written to %s", report_path)


if __name__ == "__main__":
    main()
