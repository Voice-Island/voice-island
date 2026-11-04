# Contributing to Voice Island

Thanks for helping. Bug reports, tests on other Macs and ideas are all welcome.

## Pull requests: not yet

Voice Island is dual-licensed: [AGPL-3.0](LICENSE) for everyone, and a [commercial license](COMMERCIAL.md) for closed
products. To keep that possible, code contributions need a Contributor License Agreement, which is being prepared.
Until it is published, pull requests cannot be merged. Issues, bug reports, tests on other Macs and ideas are
welcome now.

## What we look for

- One change per pull request, with a short description of what it does and how you checked it.
- Tests for behaviour changes (`cd web/agent && uv run pytest`).
- No personal data: no logs, recordings, voiceprints, `settings.json`, `memory.json` or `learned.json`.
- New dependencies or models: say which license they use. Default installs stay free of non-commercial and
  restricted components (see [NOTICE](NOTICE)).

## Security issues

Please do not open a public issue. See [SECURITY.md](SECURITY.md).
