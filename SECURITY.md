# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature for this
repository. Do not open a public issue containing credentials, cookie data,
subscriber content, or exploit details.

## Sensitive local data

Subscriber mode uses an exported browser session. Treat the cookie file,
`post_cache/`, generated archives, and logs as sensitive:

- never commit or share them;
- restrict cookie files to the current user (`chmod 600 cookies.txt`);
- revoke the exported session from Substack if it may have been exposed;
- delete generated content when it is no longer needed.

Subhoard creates caches and output files with owner-only permissions on POSIX
systems, but backups and cloud-synced folders may apply different controls.
