# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Report them privately via GitHub's [Security Advisories](../../security/advisories/new) feature, or by emailing the repository maintainer directly (see the GitHub profile for contact details).

Include:
- A description of the vulnerability
- Steps to reproduce
- Affected versions
- Any suggested fix

You can expect an acknowledgement within 72 hours and a resolution timeline within 14 days for confirmed issues.

## Scope

This tool processes meeting notes and publishes to a MediaWiki instance. The primary attack surfaces are:

- The Flask web application (authentication, CSRF, session management)
- The wiki bot credentials stored in `.env`
- The SQLite provenance database
- Outbound HTTP calls to the wiki API and Riseup Pad

The tool is designed to run as an internal admin tool for a small group of authenticated users, not as a public-facing service.
