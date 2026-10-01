# :notebook:  Change Log: ArcGIS Online Member Deletion Tool (AOLMDT)

## Features Heading
- `Added` for new features.
- `Changed` for changes in existing functionality.
- `Fixed` for any bug fixes.
- `Removed` for now removed features.
- `Security` in case of vulnerabilities.
- `Deprecated` for soon-to-be removed features.

[//]: # (Copy paste pallette)
[//]: # (#### Added)
[//]: # (#### Changed)
[//]: # (#### Fixed)
[//]: # (#### Removed)
[//]: # (#### Security)
[//]: # (#### Deprecated)

---

#### Version 1.5.1
#### Fixed
- ArcGIS account lacks permission to read entitlement data for the Snap2Map license, causing a 403 that previously made the tool block the whole user deletion.
- Code fix: 403/permission errors on license entitlement checks are now logged as a warning and skipped, rather than blocking deletion.
#### Changed
- gitignore deleted_users.csv file


---

#### Version 1.5.0

#### Added
- Added support for passing a single username directly to `--input`.

#### Changed
- `--input` values ending in `.csv` are read as CSV files; other values are treated as usernames.
- Year-based matches are combined with a directly supplied username without writing a file.

#### Version 1.4.0
 
#### Added
- Added `DELETE_USERS_BY_LAST_LOGIN_YEAR` configuration variable to `.env`.
- Added optional `--DELETE_USERS_BY_LAST_LOGIN_YEAR` command-line override.
 
#### Changed
- Year-based user targeting now reads `DELETE_USERS_BY_LAST_LOGIN_YEAR` from `.env`.
- Configuration values are consolidated into `.env`.
 
#### Removed
- Removed `delete_users_by_year.txt`.


#### Version 1.3.0
 
#### Added
- Added `AGO_URL` configuration variable to `.env`.
- Added interactive URL prompt when no organization URL is configured.
 
#### Changed
- Made the `--url` parameter optional.
- `--url` now overrides `AGO_URL` when both are provided.
- Organization URL is now resolved from `--url`, `.env`, or an interactive prompt, in that order.

#### Version 1.2.0
#### Changed
- program is now more robust

#### Version 1.1.2
#### Fixed
- python indentation
- Readme referencing
- 
#### Version 1.1.1
#### Fixed
- python indentation
- Readme referencing
  
#### Version 1.1.0
#### Added
- requirements
#### Changed
- txt files to .env files
- Credentials are loaded via `python-dotenv`
- .gitignore list

#### Version 1.0.0 

- First release