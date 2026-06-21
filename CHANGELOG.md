# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
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
