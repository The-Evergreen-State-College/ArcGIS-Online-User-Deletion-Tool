"""
Batch-delete ArcGIS Online users, automatically clearing the prerequisites
required by the platform before a user account can be removed:

    1. Remove all content ownership (delete every item the user owns).
    2. Remove all group ownership by reassigning owned groups to the administrator running the script.
    3. Remove user from all joined (non-owned) groups.
    4. Revoke all add-on/app-bundle licenses assigned to the user.
    5. Delete the user profile.

Usage:
    python delete_users.py --url https://geoduck.maps.arcgis.com --input users.csv
    python delete_users.py --url https://geoduck.maps.arcgis.com --input users.csv --dry-run
"""
import argparse
import csv
import datetime
import getpass
import logging
import os
import sys
import time

from arcgis.gis import GIS
from dotenv import dotenv_values

try:
    import keyring
except ImportError:
    keyring = None

KEYRING_SERVICE = "arcgis-online-delete-users"

log = logging.getLogger("delete_users")


def setup_logging(logs_dir: str) -> str:
    """Configure logging to STDOUT and a timestamped file under logs_dir."""
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
    """Log a per-step note at a severity matching its marker."""
    if note.startswith("ERROR"):
        log.error("  - %s", note)
    elif note.startswith("BLOCKED") or "FAILED" in note:
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
        if not username:
            username = input(f"Admin username for {url}: ").strip()

        password = (
            keyring.get_password(KEYRING_SERVICE, username)
            if keyring
            else None
        )

        if password:
            print(f"Using password from OS keyring ({KEYRING_SERVICE}) for {username}")
        else:
            password = getpass.getpass("Admin password: ")

    gis = GIS(url, username, password)
    log.info("Connected to %s as %s", url, gis.users.me.username)
    return gis


def read_usernames(input_value: str) -> list[str]:
    if not input_value.lower().endswith(".csv"):
        username = input_value.strip()
        return [username] if username else []

    csv_path = input_value
    if not os.path.isfile(csv_path):
        return []

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if "username" not in (reader.fieldnames or []):
            raise ValueError('Input CSV must contain a "username" column')

        usernames = []
        seen = set()

        for row in reader:
            username = row.get("username", "").strip()
            if not username:
                continue

            key = username.casefold()
            if key in seen:
                continue

            seen.add(key)
            usernames.append(username)

        return usernames


def parse_year_filter(value: str | None) -> list[int] | None:
    """Parse and validate comma-separated last-login years."""
    if value is None or not value.strip():
        return None

    current_year = datetime.datetime.now(datetime.timezone.utc).year
    years = []
    for entry in value.split(","):
        year_text = entry.strip()
        if len(year_text) != 4 or not year_text.isascii() or not year_text.isdigit():
            raise ValueError(
                "DELETE_USERS_BY_LAST_LOGIN_YEAR entries must be four-digit numeric years"
            )

        year = int(year_text)
        if year < 1970 or year > current_year:
            raise ValueError(
                f"DELETE_USERS_BY_LAST_LOGIN_YEAR entries must be between 1970 and {current_year}"
            )
        if year not in years:
            years.append(year)

    return years


def find_users_by_last_login_year(
    gis: GIS, year: int, exclude_username: str | None = None
) -> list[str]:
    """Return usernames of org members whose last login falls in the given year, excluding specified accounts."""
    matches = []
    start = 1
    num = 100
    exclude_key = exclude_username.casefold() if exclude_username else None

    while True:
        users = gis.users.search(query="", max_users=num, start=start)
        if not users:
            break

        for user in users:
            # Ignore administrator account during discovery
            if exclude_key and user.username.casefold() == exclude_key:
                continue

            last_login = getattr(user, "lastLogin", -1)
            if last_login and last_login > 0:
                login_year = datetime.datetime.fromtimestamp(
                    last_login / 1000, tz=datetime.timezone.utc
                ).year
                if login_year == year:
                    matches.append(user.username)

        if len(users) < num:
            break
        start += len(users)

    return matches


def add_users_to_csv(csv_path: str, usernames: list[str]) -> list[str]:
    """Append usernames not already listed in the CSV."""
    existing = set()
    if os.path.isfile(csv_path):
        existing = {username.casefold() for username in read_usernames(csv_path)}

    new_usernames = []
    seen_new = set()

    for username in usernames:
        username = username.strip()
        if not username:
            continue

        key = username.casefold()
        if key in existing or key in seen_new:
            continue

        seen_new.add(key)
        new_usernames.append(username)

    if new_usernames:
        file_exists = os.path.isfile(csv_path)
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists or os.path.getsize(csv_path) == 0:
                writer.writerow(["username"])
            for username in new_usernames:
                writer.writerow([username])

    return new_usernames


def _list_folder_items(gis: GIS, username: str, folder_id: str | None) -> list[dict]:
    path = f"content/users/{username}" + (f"/{folder_id}" if folder_id else "")
    items = []
    start = 1

    while start != -1:
        data = gis._con.post(path, {"f": "json", "num": 100, "start": start})
        if "error" in data:
            raise RuntimeError(f"ArcGIS failed to list content for {username}: {data['error']}")

        items.extend(data.get("items", []))
        start = data.get("nextStart", -1)

    return items


def get_all_owned_items(gis: GIS, username: str) -> list[dict]:
    root = gis._con.post(f"content/users/{username}", {"f": "json", "num": 100, "start": 1})
    if "error" in root:
        raise RuntimeError(f"ArcGIS failed to retrieve content root for {username}: {root['error']}")

    items = _list_folder_items(gis, username, None)

    for folder in root.get("folders", []):
        folder_id = folder.get("id")
        if not folder_id:
            raise RuntimeError(f"ArcGIS returned a folder without an ID for {username}: {folder}")

        items.extend(_list_folder_items(gis, username, folder_id))

    return items


def get_owned_groups(gis: GIS, username: str) -> list[dict]:
    data = gis._con.get(f"community/users/{username}", {"f": "json"})
    if "error" in data:
        raise RuntimeError(f"ArcGIS failed to retrieve groups for {username}: {data['error']}")

    return [
        group for group in data.get("groups", [])
        if group.get("owner", "").casefold() == username.casefold()
    ]


def get_assigned_licenses(gis: GIS, username: str) -> list[str]:
    assigned = []
    license_mgr = gis.admin.license

    for lic in license_mgr.all():
        title = lic.properties.get("listing", {}).get(
            "title", lic.properties.get("itemId", "Unknown license")
        )

        try:
            user_entitlements = lic.user_entitlement(username)
        except Exception as exc:
            if "not licensed by user" in str(exc).lower():
                continue
            if "403" in str(exc) or "permissions" in str(exc).lower():
                logging.warning("Skipping license check for %s: %s", title, exc)
                continue
            raise RuntimeError(f"Unable to verify license {title}: {exc}") from exc

        entitlements = (user_entitlements or {}).get("entitlements", [])
        for entitlement in entitlements:
            assigned.append(f"{title}: {entitlement}")

    return assigned


def delete_owned_content(gis: GIS, username: str, dry_run: bool) -> list[str]:
    notes = []
    root = gis._con.post(f"content/users/{username}", {"f": "json", "num": 100, "start": 1})

    if "error" in root:
        raise RuntimeError(f"ArcGIS failed to retrieve content root for {username}: {root['error']}")

    folders = root.get("folders", [])

    folder_pairs = [(None, "root")]
    for folder in folders:
        folder_id = folder.get("id")
        if not folder_id:
            raise RuntimeError(
                f"ArcGIS returned a folder without an ID for {username}: {folder}"
            )
        folder_pairs.append(
            (
                folder_id,
                folder.get("title", folder_id),
            )
        )

    for folder_id, folder_label in folder_pairs:
        items = _list_folder_items(gis, username, folder_id)
        for item in items:
            label = f'{item["title"]} ({item["id"]}, folder={folder_label})'
            if dry_run:
                notes.append(f"[dry-run] would delete item {label}")
                continue
            try:
                path = (
                    f"content/users/{username}/{folder_id}/items/{item['id']}/delete"
                    if folder_id
                    else f"content/users/{username}/items/{item['id']}/delete"
                )
                resp = gis._con.post(path, {"f": "json", "permanentDelete": True})
                ok = bool(resp.get("success"))
                notes.append(f"deleted item {label}: {'ok' if ok else 'FAILED ' + str(resp)}")
            except Exception as exc:
                notes.append(f"ERROR deleting item {label}: {exc}")

    for f in folders:
        label = f'{f.get("title", f.get("id"))} ({f.get("id")})'
        if dry_run:
            notes.append(f"[dry-run] would delete folder {label}")
            continue
        try:
            resp = gis._con.post(f"content/users/{username}/{f['id']}/delete", {"f": "json"})
            ok = bool(resp.get("success"))
            notes.append(f"deleted folder {label}: {'ok' if ok else 'FAILED ' + str(resp)}")
        except Exception as exc:
            notes.append(f"ERROR deleting folder {label}: {exc}")

    if not notes:
        notes.append("no owned content found")
    return notes


def reassign_owned_groups(gis: GIS, username: str, admin_username: str, dry_run: bool) -> list[str]:
    notes = []
    owned_groups = get_owned_groups(gis, username)

    if not owned_groups:
        notes.append("no owned groups found")
        return notes

    for group in owned_groups:
        label = f'{group["title"]} ({group["id"]})'

        if dry_run:
            notes.append(f"[dry-run] would reassign group {label} to {admin_username}")
            continue

        try:
            resp = gis._con.post(
                f"community/groups/{group['id']}/reassign",
                {"f": "json", "targetUsername": admin_username},
            )
            ok = bool(resp.get("success"))
            notes.append(f"reassigned group {label} to {admin_username}: {'ok' if ok else 'FAILED ' + str(resp)}")
        except Exception as exc:
            notes.append(f"ERROR reassigning group {label}: {exc}")

    return notes


def leave_joined_groups(user, dry_run: bool) -> list[str]:
    """Remove user from groups they belong to but do not own."""
    notes = []
    groups = getattr(user, "groups", [])

    for group in groups:
        if group.owner.casefold() == user.username.casefold():
            continue  # Handled by reassign_owned_groups

        label = f"{group.title} ({group.id})"
        if dry_run:
            notes.append(f"[dry-run] would remove user from group {label}")
            continue

        try:
            ok = group.remove_users([user.username])
            notes.append(f"removed from joined group {label}: {'ok' if ok else 'FAILED'}")
        except Exception as exc:
            notes.append(f"ERROR leaving group {label}: {exc}")

    if not notes:
        notes.append("no joined groups found")
    return notes


def revoke_licenses(gis: GIS, user, dry_run: bool) -> list[str]:
    notes = []
    try:
        license_mgr = gis.admin.license
    except Exception as exc:
        notes.append(f"ERROR accessing license manager: {exc}")
        return notes

    for lic in license_mgr.all():
        title = lic.properties.get("listing", {}).get("title", lic.properties.get("itemId"))
        try:
            user_entitlements = lic.user_entitlement(user.username)
            entitlements = (user_entitlements or {}).get("entitlements", [])
        except Exception as exc:
            if "not licensed by user" in str(exc).lower():
                continue
            if "403" in str(exc) or "permissions" in str(exc).lower():
                notes.append(f"WARNING: skipping license check for {title} (no permission to query entitlements): {exc}")
                continue
            notes.append(f"ERROR reading entitlements for {title}: {exc}")
            continue

        if not entitlements:
            continue

        label = f'{title} [{", ".join(entitlements)}]'
        if dry_run:
            notes.append(f"[dry-run] would revoke license {label}")
            continue

        try:
            ok = lic.revoke(username=user.username, entitlements=entitlements)
            notes.append(f"revoked license {label}: {'ok' if ok else 'FAILED'}")
        except Exception as exc:
            notes.append(f"ERROR revoking license {label}: {exc}")

    if not notes:
        notes.append("no assigned licenses found")
    return notes


def format_last_login(last_login: int | None) -> str:
    if not last_login or last_login <= 0:
        return "Never"
    return datetime.datetime.fromtimestamp(last_login / 1000, tz=datetime.timezone.utc).date().isoformat()


def delete_user(gis: GIS, username: str, dry_run: bool) -> dict:
    result = {
        "username": username,
        "status": "skipped",
        "notes": [],
        "last_login": "unknown",
    }

    admin_username = gis.users.me.username

    if username.casefold() == admin_username.casefold():
        result["status"] = "blocked"
        result["notes"].append("BLOCKED: refusing to delete the authenticated administrator account")
        return result

    user = gis.users.get(username)

    if user is None:
        result["status"] = "not_found"
        result["notes"].append("user not found in organization")
        return result

    result["last_login"] = format_last_login(getattr(user, "lastLogin", None))

    if dry_run:
        result["notes"] += delete_owned_content(gis, username, True)
        result["notes"] += reassign_owned_groups(gis, username, admin_username, True)
        result["notes"] += leave_joined_groups(user, True)
        result["notes"] += revoke_licenses(gis, user, True)

        result["status"] = "dry-run"
        result["notes"].append("[dry-run] would delete user profile")
        return result

    # STEP 1: DELETE CONTENT
    result["notes"] += delete_owned_content(gis, username, False)
    if get_all_owned_items(gis, username):
        result["status"] = "blocked_content"
        result["notes"].append("BLOCKED: content remains after cleanup")
        return result

    # STEP 2: REASSIGN & LEAVE GROUPS
    result["notes"] += reassign_owned_groups(gis, username, admin_username, False)
    result["notes"] += leave_joined_groups(user, False)

    # STEP 3: REVOKE LICENSES
    result["notes"] += revoke_licenses(gis, user, False)

    # ---------------------------------------------------------
    # STEP 4: VERIFY ALL DELETION PREREQUISITES
    # ---------------------------------------------------------
    try:
        # 1. Check Owned Items
        remaining_items = get_all_owned_items(gis, username)
        if remaining_items:
            result["status"] = "blocked_content"
            item_ids = [i.get("id", "unknown") for i in remaining_items]
            result["notes"].append(
                f"BLOCKED: {len(remaining_items)} item(s) remain: {', '.join(item_ids)}"
            )
            return result

        # 2. Check Owned Groups
        remaining_owned_groups = get_owned_groups(gis, username)
        if remaining_owned_groups:
            result["status"] = "blocked_groups"
            group_ids = [g.get("id", "unknown") for g in remaining_owned_groups]
            result["notes"].append(
                f"BLOCKED: {len(remaining_owned_groups)} owned group(s) remain: {', '.join(group_ids)}"
            )
            return result

        # 3. Check Joined (Non-Owned) Groups
        fresh_user = gis.users.get(username)
        joined_groups = [
            g for g in getattr(fresh_user, "groups", [])
            if g.owner.casefold() != username.casefold()
        ]
        if joined_groups:
            result["status"] = "blocked_groups"
            group_titles = [g.title for g in joined_groups]
            result["notes"].append(
                f"BLOCKED: User is still a member of {len(joined_groups)} group(s): {', '.join(group_titles)}"
            )
            return result

        # 4. Check Licenses
        remaining_licenses = get_assigned_licenses(gis, username)
        if remaining_licenses:
            result["status"] = "blocked_licenses"
            result["notes"].append(
                f"BLOCKED: {len(remaining_licenses)} license entitlement(s) remain: {', '.join(remaining_licenses)}"
            )
            return result

    except Exception as exc:  # noqa: BLE001
        result["status"] = "verification_failed"
        result["notes"].append(
            f"BLOCKED: final prerequisite verification failed with error: {exc}"
        )
        return result

    result["notes"].append("verified: all deletion prerequisites satisfied")

    # ---------------------------------------------------------
    # STEP 5: DELETE USER PROFILE
    # ---------------------------------------------------------
    try:
        ok = user.delete()
        if not ok:
            result["status"] = "delete_failed"
            result["notes"].append("delete user profile: FAILED")
            return result
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["notes"].append(f"ERROR deleting user profile: {exc}")
        return result

    # ---------------------------------------------------------
    # STEP 6: VERIFY PROFILE DELETION
    # ---------------------------------------------------------
    time.sleep(1.5)  # Pause for AGOL REST index propagation

    try:
        remaining_user = gis.users.get(username)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "verification_failed"
        result["notes"].append(
            f"User delete call returned success, but post-delete lookup failed: {exc}"
        )
        return result

    if remaining_user is not None:
        result["status"] = "delete_failed"
        result["notes"].append(
            "User delete call returned success, but the account is still present in AGOL"
        )
        return result

    result["status"] = "deleted"
    result["notes"].append("delete user profile: ok")
    result["notes"].append("verified: user account no longer exists")
    return result

def log_deleted_user(deleted_log: str, admin_username: str, username: str, last_login: str) -> None:
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
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url",help="ArcGIS Online org URL (overrides AGO_URL in .env)",)
    parser.add_argument("--input", required=True, help="CSV file with a 'username' column")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without deleting")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--credentials-file", default=".env", help="Path to credentials .env")
    parser.add_argument("--deleted-log", default="deleted_users.csv", help="Deleted users log")
    parser.add_argument(
        "--DELETE_USERS_BY_LAST_LOGIN_YEAR",
        dest="delete_users_by_last_login_year",
        help="Comma-separated last-login years (overrides .env)",
    )
    parser.add_argument("--logs-dir", default="logs", help="Log output directory")

    args = parser.parse_args()
    env_values = read_credentials(args.credentials_file)

    year_value = (
        args.delete_users_by_last_login_year
        if args.delete_users_by_last_login_year is not None
        else env_values.get("DELETE_USERS_BY_LAST_LOGIN_YEAR")
    )
    try:
        years = parse_year_filter(year_value)
    except ValueError as exc:
        parser.error(str(exc))

    # Determine the ArcGIS Online organization URL.
    # Priority: --url argument -> AGO_URL in .env -> console prompt.
    if args.url:
        ago_url = args.url.strip()
    else:
        ago_url = (env_values.get("AGO_URL") or "").strip()
    
    if not ago_url:
        ago_url = input("ArcGIS Online organization URL: ").strip()
    
    if not ago_url:
        parser.error(
            "ArcGIS Online organization URL is required. "
            "Set AGO_URL in .env, use --url, or enter it when prompted."
        )



    log_path = setup_logging(args.logs_dir)
    log.info("Logging to %s", log_path)

    gis = connect(ago_url, args.credentials_file)
    admin_username = gis.users.me.username

    year_matches = []
    if years:
        log.info("Year filter active: DELETE_USERS_BY_LAST_LOGIN_YEAR=%s", ",".join(map(str, years)))
        matches = []
        for year in years:
            matches.extend(
                find_users_by_last_login_year(gis, year, exclude_username=admin_username)
            )
        if args.input.lower().endswith(".csv"):
            added = add_users_to_csv(args.input, matches)
            log.info("Found %d user(s); added %d new user(s) to %s", len(matches), len(added), args.input)
        else:
            year_matches = matches
            log.info("Found %d user(s) matching the year filter", len(matches))

    usernames = read_usernames(args.input)
    seen_usernames = {username.casefold() for username in usernames}
    for username in year_matches:
        key = username.casefold()
        if key not in seen_usernames:
            seen_usernames.add(key)
            usernames.append(username)

    if not usernames:
        log.warning("No usernames queued for deletion. Nothing to do.")
        return

    if any(u.casefold() == admin_username.casefold() for u in usernames):
        log.error("Input CSV contains authenticated admin (%s). Remove it.", admin_username)
        sys.exit(1)

    log.info("%d user(s) queued for deletion: %s", len(usernames), ", ".join(usernames))

    if not args.dry_run and not args.yes:
        confirm = input("Type 'DELETE' to confirm permanent user removal: ")
        if confirm.strip() != "DELETE":
            log.warning("Aborted.")
            sys.exit(1)

    results = []
    for username in usernames:
        log.info("=== Processing %s ===", username)
        try:
            result = delete_user(gis, username, args.dry_run)
        except Exception as exc:
            log.exception("Unhandled error while processing %s", username)
            result = {
                "username": username,
                "status": "error",
                "notes": [f"Unhandled processing error: {exc}"],
                "last_login": "unknown",
            }

        for note in result["notes"]:
            log_note(note)

        log.info("  Status: %s", result["status"])
        if result["status"] == "deleted":
            log_deleted_user(args.deleted_log, admin_username, username, result["last_login"])

        results.append(result)

    report_path = write_report(results, args.input)
    log.info("Report written to %s", report_path)


if __name__ == "__main__":
    main()