# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
## [2.4.0] - 2026-11-21

Re-added config-by-file option


### Added

- Settings can be read from a `KEY=VALUE` config file, `~/.shconfig` by
  default, or a path given with `--config` or `SUBHOARD_CONFIG`.
  Command-line arguments and environment variables take precedence over it.

## [2.3.0] - 2026-11-21

Documentation cleanup


### Added

- `install.sh` downloads a private copy of Python when no Python 3.11 or
  newer is installed, so a stock Mac no longer needs Homebrew or a separate
  Python install. The download uses a pinned, checksum-verified uv release
  and stays inside `~/.local/share/subhoard`.

## [2.2.1] - 2026-11-21

Added an install script.


### Added

- `install.sh` installs, updates, or uninstalls (`--uninstall`) Subhoard in
  one command, finding a suitable Python 3.11+ interpreter automatically.

### Changed

- Allow reportlab 5; the lock file now installs reportlab 5.1.0.

## [2.2.0] - 2026-11-19

Fixed install steps and problem with Substack API


### Fixed

- Archive fetching works again. Substack's archive API now rejects the
  `offset` parameter and limits above 25, so every run reported "No posts
  found". Subhoard now follows the API's `nextCursor` pagination and reports
  API errors instead of treating them as an empty archive. Servers that still
  return the older list format are paged by offset, and a cursor that repeats
  stops the fetch with an error instead of looping forever.
- Publications on a `www.` custom domain are named after the domain instead
  of "Www" in digest and PDF filenames, titles, and email headers.

### Added

- `requirements.lock` with exact, hash-verified dependency versions, used by
  the documented install and by CI.

### Changed

- Python 3.11 is now the minimum supported version (3.9 and 3.10 have reached
  end of life). CI also tests Python 3.14.
- Installation instructions use the latest release tag, explain what
  `camoufox fetch` downloads, and finish with an installation check.
- Subhoard is now installed into its own environment under
  `~/.local/share/subhoard` with the command linked into `~/.local/bin`, so
  it runs without activating a virtual environment or keeping the clone.
  The README also covers updating and uninstalling.

## [2.1.0] - 2026-06-21
Versioning for all CLI commands
Security improvements and fixes
Project artifact and documentation improvements


### Added

- Tag-driven GitHub release automation for wheel and source distributions.
- A development dependency extra for consistent contributor setup.
- Bug report, feature request, and pull request templates.
- Code of conduct, support policy, and maintainer release documentation.

### Changed

- CI now builds and installs the wheel with runtime dependencies before smoke
  testing the installed command.
- Runtime dependencies now have explicit compatibility ranges.
- `pyproject.toml` is now the sole dependency source.
- Removed lifecycle-status package metadata.
- Security and contribution documentation now use consistent project
  policies.

## [2.0.1] - 2026-06-21

### Changed

- Corrected project badges and release presentation.

## [2.0.0] - 2026-06-20

### Added

- Installable Python package with the `subhoard` console command.
- Public-only and authorized subscriber-content modes.
- Markdown, PDF, and HTML email output.
- Resumable private cache, environment configuration, and dry-run support.
- Security controls for URLs, cookies, generated files, HTML, and email.

## [1.0.0] - 2026-06-18

### Added

- Initial Substack archive script.

[Unreleased]: https://github.com/pabooth/subhoard/compare/v2.0.1...HEAD
[2.0.1]: https://github.com/pabooth/subhoard/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/pabooth/subhoard/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/pabooth/subhoard/releases/tag/v1.0.0
