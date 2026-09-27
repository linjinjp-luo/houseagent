# Install, upgrade and uninstall (spec 10 / 15.10)

**Install**: run `scripts\build.ps1`, then copy `dist\HouseAgent\` anywhere (e.g. `%LOCALAPPDATA%\Programs\HouseAgent`)
and double-click `HouseAgent.exe`. It starts the local service on `http://127.0.0.1:8765` (or a free port) and opens
the UI. No administrator rights, cloud server or domain are needed. Microsoft Edge must be present (standard on
Windows 10/11).

**Program vs. user data**: the program folder holds only program files. All user data is under
`%LOCALAPPDATA%\HouseAgent\` (database, browser profiles, backups, logs, config).

**Upgrade**: quit HouseAgent (tray → Quit), replace the program folder with the new build, start it again. The
database is backed up automatically before any schema migration; if a migration fails the original database is
kept and the app starts read-only so you can restore. Upgrades never touch profiles, backups or config.

**Uninstall**: `uninstall.ps1` (copied next to `HouseAgent.exe`) removes only the program by default.
`uninstall.ps1 -DeleteUserData` also removes all user data after you type `DELETE`.

A classic installer (MSI/Inno Setup) is not required for the V1.0 MVP; it can wrap the same folder later with
these same rules.
