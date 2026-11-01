# Security Policy

OpenPhotoAgent touches people's personal photo libraries and moves files on their disks, so we treat security reports as a top priority.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**
Report privately through [GitHub Security Advisories](../../security/advisories/new). We aim to acknowledge reports within 72 hours.

## Especially in scope

- **Path sandbox escapes:** reading or writing outside the allowed photo roots (path traversal, symlink tricks)
- **Data egress:** originals or thumbnails leaving the worker against the workspace's privacy level
- **MCP / agent abuse:** an MCP client applying file changes without human approval, scope escalation, or prompt injection through image text or EXIF that triggers actions
- **Worker pairing and auth:** token theft or replay, impersonating a worker or control plane
- **Data loss:** any path where move or undo can lose a file

## Supported versions

Pre-1.0: only the latest release on `main` receives fixes.
