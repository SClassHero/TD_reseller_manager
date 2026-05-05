# Changelog

App versions follow semantic versioning where practical:

- `MAJOR`: breaking app or deployment changes
- `MINOR`: new user-facing features
- `PATCH`: bug fixes and small documentation/test updates

Database schema compatibility is tracked separately in `SCHEMA_CHANGELOG.md`.

## [0.5.0] - 2026-05-04

First formal versioned checkpoint.

### Added

- Formal app version metadata in `version.py`.
- Synology Container Manager deployment files.
- Tailscale-based remote access documentation.
- Windows portable packaging guide and build script.
- Desktop launcher entry point for local packaged builds.
- README for GitHub users.

### Current Baseline

- 673 documented tests passing.
- Database schema version: 5.
