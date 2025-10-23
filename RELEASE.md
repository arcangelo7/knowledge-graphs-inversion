# Release Process

This project uses automated semantic versioning for releases.

## How it works

The release workflow is triggered automatically when you push a commit to the `master` branch with `[release]` in the commit message.

## Semantic commit format

Use conventional commits to automatically determine the version bump:

### Version bumps

- **Major version** (1.0.0 → 2.0.0): Breaking changes
  ```
  feat!: redesign API endpoints

  BREAKING CHANGE: removed old authentication method
  ```

- **Minor version** (1.0.0 → 1.1.0): New features
  ```
  feat: add JSON template support
  ```

- **Patch version** (1.0.0 → 1.0.1): Bug fixes and improvements
  ```
  fix: correct SPARQL query generation for blank nodes
  ```
  ```
  perf: optimize database connection pooling
  ```

### Commit types

- `feat:` - New feature (triggers minor version bump)
- `fix:` - Bug fix (triggers patch version bump)
- `perf:` - Performance improvement (triggers patch version bump)
- `docs:` - Documentation changes
- `style:` - Code style changes (formatting, whitespace)
- `refactor:` - Code refactoring
- `test:` - Adding or updating tests
- `build:` - Build system changes
- `ci:` - CI/CD changes
- `chore:` - Other changes