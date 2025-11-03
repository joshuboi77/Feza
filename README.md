# Feza | フェザー | Feather [![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Plan, build, publish, and tap releases for CLI apps with a reproducible workflow as light as a feather.**

Feza automates the entire release workflow for command-line tools: planning target matrices, packaging binaries, creating GitHub releases, and updating Homebrew formulas—all from a single manifest file (`dist/feza_manifest.json`).

## Features

- 📋 **Plan** - Derive target matrix and filenames from a tag and tool name; write a manifest
- 📦 **Build** - Package per-target binaries into tarballs; compute SHA256; update manifest with checksums and URLs
- 🚀 **GitHub** - Create/update draft GitHub releases and upload assets from the manifest
- 🍺 **Tap** - Render and push Homebrew formulas using the manifest's URLs and checksums (optional PR creation)

## Installation

### Homebrew (Recommended)

```bash
brew tap joshuboi77/homebrew-tap
brew install feza
```

### pip

```bash
pip install feza
```

### From Source

```bash
git clone https://github.com/joshuboi77/Feza.git
cd Feza
pip install -e .
```

## Requirements

- Python 3.11+
- [GitHub CLI](https://cli.github.com/) (`gh`) - used if `GITHUB_TOKEN` is not set
- Git - for repository operations

## Quick Start

```bash
# 1. Plan your release
feza plan v1.0.0 --name feza

# 2. Build your binaries or create wrapper scripts (Feza doesn't build—you do)
# For compiled tools: place binaries in build/macos-arm64/feza*, build/linux-amd64/feza*, etc.
# For Python tools: create wrapper scripts (see Python CLI Tools section)

# 3. Package and compute checksums (outputs to dist/*.tar.gz)
feza build v1.0.0 --name feza --repo joshuboi77/Feza

# 4. Create GitHub release (uses gh CLI or GITHUB_TOKEN)
feza github v1.0.0 --name feza --repo joshuboi77/Feza

# 5. Update Homebrew tap (requires TAP_PAT)
export TAP_PAT=ghp_...
feza tap v1.0.0 --name feza --tap joshuboi77/homebrew-tap --formula Feza --open-pr
```

**Note:** Feza packages, checksums, and releases existing binaries—it does not build them. Build your binaries using your project's build system (Makefile, Go builds, Rust `cargo build --release`, etc.). For Python CLI tools, create wrapper scripts (see [Python CLI Tools](#python-cli-tools)), then use Feza to package and release them.

The manifest (`dist/feza_manifest.json`) is created in step 1 and updated at each step—it's your single source of truth.

## Command Reference

### `plan` — Create Release Manifest

```bash
feza plan <tag> --name <tool> [--targets macos-arm64,macos-amd64,linux-amd64]
```

Creates `dist/feza_manifest.json` with empty SHA256/URL fields. Requires clean git working tree.

**Arguments:**
- `tag` - Release tag (format: vX.Y.Z)
- `--name` - Tool name (required)
- `--targets` - Comma-separated targets (default: `macos-arm64,macos-amd64,linux-amd64`)

**Example:** `feza plan v1.2.3 --name feza`

### `build` — Package Binaries

```bash
feza build <tag> --name <tool> [--artifacts-dir build/] [--dist dist/] [--repo org/repo]
```

Finds pre-built binaries in `build/<target>/<name>*`, packages them to `dist/<filename>.tar.gz`, computes SHA256, and updates the manifest with checksums and canonical URLs. **Feza does not build binaries—it packages existing ones.**

**Arguments:**
- `tag` - Release tag (must match manifest)
- `--name` - Tool name (required)
- `--artifacts-dir` - Directory containing binaries (default: `build/`)
- `--dist` - Output directory for tarballs (default: `dist/`)
- `--repo` - GitHub repository (default: `GITHUB_REPOSITORY` env var)

**Example:** `feza build v1.2.3 --name feza --repo joshuboi77/Feza`

### `github` — Create/Update Release

```bash
feza github <tag> --name <tool> [--repo org/repo] [--release-notes path/to/template.md.j2] [--dist dist/]
```

Creates or updates draft GitHub release and uploads assets. Uses `gh` CLI if available, otherwise requires `GITHUB_TOKEN`.

**Arguments:**
- `tag` - Release tag (must match manifest)
- `--name` - Tool name (required)
- `--repo` - GitHub repository (default: `GITHUB_REPOSITORY` env var)
- `--release-notes` - Path to Jinja2 template for release notes (optional)
- `--dist` - Directory containing assets (default: `dist/`)
- `--draft` - Create as draft release (default: true)

**Requirements:** Authenticated `gh` CLI or `GITHUB_TOKEN` environment variable

**Example:** `feza github v1.2.3 --name feza --repo joshuboi77/Feza`

### `tap` — Update Homebrew Formula

```bash
feza tap <tag> --name <tool> --tap org/tap --formula <FormulaName> [--branch feza/vX.Y.Z] [--open-pr]
```

Renders Homebrew formula from template, pushes to tap repository, and optionally opens PR.

**Arguments:**
- `tag` - Release tag (must match manifest)
- `--name` - Tool name (required)
- `--tap` - Homebrew tap repository (required, e.g., `joshuboi77/homebrew-tap`)
- `--formula` - Formula class name (required, e.g., `Feza`)
- `--branch` - Branch name (default: `feza/<tag>`)
- `--open-pr` - Open PR after push (optional)
- `--formula-template` - Custom formula template path (optional)
- `--repo` - Homepage repository for formula metadata
- `--desc` - Formula description
- `--homepage` - Formula homepage URL

**Requirements:** `TAP_PAT` environment variable (GitHub token with tap repo access)

**Example:** `feza tap v1.2.3 --name feza --tap joshuboi77/homebrew-tap --formula Feza --open-pr`

## Manifest File

Feza uses `dist/feza_manifest.json` (created by `plan`, updated by `build`) as the single source of truth:

```json
{
  "tag": "v1.2.3",
  "version": "1.2.3",
  "name": "feza",
  "assets": [
    {
      "target": "macos-arm64",
      "filename": "feza-darwin-arm64.tar.gz",
      "sha256": "abc123...",
      "url": "https://github.com/joshuboi77/Feza/releases/download/v1.2.3/feza-darwin-arm64.tar.gz"
    },
    {
      "target": "macos-amd64",
      "filename": "feza-darwin-amd64.tar.gz",
      "sha256": "def456...",
      "url": "https://github.com/joshuboi77/Feza/releases/download/v1.2.3/feza-darwin-amd64.tar.gz"
    },
    {
      "target": "linux-amd64",
      "filename": "feza-linux-amd64.tar.gz",
      "sha256": "789ghi...",
      "url": "https://github.com/joshuboi77/Feza/releases/download/v1.2.3/feza-linux-amd64.tar.gz"
    }
  ]
}
```

## Templates

Feza uses Jinja2 templates for rendering. Default templates included:

- **Homebrew Formula** (`feza/templates/formula.rb.j2`) - Variables: `formula_name`, `name`, `version`, `desc`, `homepage`; helpers: `url_by(target)`, `sha_by(target)`
- **Release Notes** (`feza/templates/release_notes.md.j2`) - Variables: `name`, `version`, `assets`

Use `--formula-template` or `--release-notes` to customize.

## Environment Variables

- `GITHUB_REPOSITORY` - Default GitHub repository (format: `org/repo`)
- `GITHUB_TOKEN` - GitHub token for release operations (optional if `gh` CLI is authenticated)
- `TAP_PAT` - GitHub token for tap repository operations (required)

## CI/CD Integration

Feza includes a GitHub Actions workflow (`.github/workflows/release.yml`) for automated releases via `workflow_dispatch`:

1. `plan` - Create manifest
2. `build` - Matrix build across targets
3. `github` - Create/update release
4. `tap` - Update Homebrew formula

## Advanced Usage

### Custom Targets

```bash
feza plan v1.0.0 --name feza --targets macos-arm64,linux-amd64,linux-arm64
```

### Custom Release Notes

```bash
feza github v1.0.0 --name feza --repo joshuboi77/Feza --release-notes templates/custom_release_notes.md.j2
```

### Python CLI Tools

Feza works with Python CLI tools, but **does not automatically detect or create Python wrappers**. You must manually create wrapper scripts that act as "binaries" for packaging. This is how Feza releases itself:

```bash
# 1. Create wrapper scripts for each target (manual step)
./create_python_binaries.sh feza feza.main
# Creates: build/macos-arm64/feza, build/linux-amd64/feza, etc.

# 2. Use Feza normally—treats wrappers as binaries
feza plan v1.0.0 --name feza
feza build v1.0.0 --name feza --repo joshuboi77/Feza
feza github v1.0.0 --name feza --repo joshuboi77/Feza
```

**Note:** Feza does not detect Python projects or auto-create wrappers. You need to create wrapper scripts yourself (or use a helper script like `create_python_binaries.sh`). Wrapper scripts are simple Python entry points that use `#!/usr/bin/env python3` and import your package. See [Self-Bootstrapping](#self-bootstrapping) for details.

### Self-Bootstrapping

Feza can release itself using the Python wrapper approach! For Python CLI tools, create wrapper scripts in `build/<target>/` directories that import and run your package. Each wrapper should:
- Start with `#!/usr/bin/env python3` (portable shebang)
- Import your package and call its main function
- Be executable (`chmod +x`)

The `create_python_binaries.sh` script (used in the Feza repo) generates these wrappers automatically.

## Limitations

v0.x does not support:
- macOS notarization/signing
- Windows MSI/winget
- YAML surgery on existing CI workflows
- Bottle building (Homebrew formulas install from tarballs)

## Troubleshooting

**"Error: working tree is dirty"**  
The `plan` command requires a clean git working tree. Commit or stash your changes first.

**"Error: manifest not found"**  
Run `feza plan` before `build`, `github`, or `tap` commands.

**"Error: binary not found"**  
Ensure binaries are placed in `build/<target>/<name>*` directories matching your manifest targets.

**Asset upload fails**  
Verify `GITHUB_TOKEN` is set or `gh` CLI is authenticated: `gh auth status`

**Tap push fails**  
Ensure `TAP_PAT` environment variable is set with a token that has write access to your tap repository.

## FAQ

**Q: Can Feza work with Python CLI tools?**  
A: Yes! Feza works with Python tools, but it does not automatically detect Python or create wrappers. You must manually create wrapper scripts (see [Python CLI Tools](#python-cli-tools)) that import your package—this is how Feza releases itself. Alternatively, use PyInstaller/cx_Freeze to create standalone executables if you prefer.

**Q: Does Feza support Windows?**  
A: Not yet in v0.x. Windows MSI/winget support is planned for future versions.

**Q: Can I customize the Homebrew formula?**  
A: Yes! Use `--formula-template` to point to your custom Jinja2 template.

**Q: How do I update an existing release?**  
A: Feza operations are idempotent. Re-run commands with the same tag to update releases/formulas—no duplicates, no conflicts.

## Contributing

Feza PRs should be small and reproducible—just like its releases. Contributions welcome!

1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push and open a Pull Request

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Links

- **Repository:** [github.com/joshuboi77/Feza](https://github.com/joshuboi77/Feza)
- **Issues:** [github.com/joshuboi77/Feza/issues](https://github.com/joshuboi77/Feza/issues)
- **Releases:** [github.com/joshuboi77/Feza/releases](https://github.com/joshuboi77/Feza/releases)

---

**Made with ❤️ for CLI developers who want reproducible, automated releases.**
