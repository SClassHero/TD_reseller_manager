# Windows Portable Packaging

This is the lay-user-friendly local option: build a folder that contains an `.exe`,
opens the browser automatically, and stores user data beside the app.

The end user does **not** need Python or command-line knowledge. The developer
builds the portable folder once, then shares the folder or a ZIP of it.

## Build Requirements

On the developer machine only:

- Windows
- Python installed
- Internet access for `pip install pyinstaller`

## Build

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1
```

The build output is:

```text
dist\TDResellerManager\
```

That folder contains:

```text
TDResellerManager.exe
Start TD Reseller Manager.bat
data\
```

## How a Non-Technical User Runs It

1. Unzip/copy `TDResellerManager`.
2. Double-click **Start TD Reseller Manager.bat**.
3. The app opens in the browser at:

```text
http://127.0.0.1:5000
```

Default login:

```text
username: admin
password: admin123
```

Change the password immediately.

## Where Data Lives

All user data stays inside:

```text
TDResellerManager\data\
```

That includes:

- `inventory.db`
- `.secret_key`
- `backups\`
- `uploads\products\`

To move the app to another PC, copy the whole `TDResellerManager` folder.

## Versioning

When releasing a new portable build:

1. Update `version.py`.
2. Add an entry to `CHANGELOG.md`.
3. Build a fresh portable folder.
4. Keep the old `data\` folder when upgrading an existing user.

The app version is separate from `db.SCHEMA_VERSION`. Schema version only tracks
database structure compatibility.
