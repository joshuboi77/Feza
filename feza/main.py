#!/usr/bin/env python3
"""Feza CLI - plan, build, publish, and tap releases for CLI apps."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

DEFAULT_TARGETS = ["macos-arm64", "macos-amd64", "linux-amd64"]
MANIFEST_PATH = Path("dist/feza_manifest.json")


def validate_tag(tag: str) -> tuple[str, str]:
    """Validate tag format and return (tag, version)."""
    if not re.match(r"^v\d+\.\d+\.\d+$", tag):
        sys.exit(f"Error: tag must match ^v\\d+\\.\\d+\\.\\d+$ (got: {tag})")
    version = tag.lstrip("v")
    return tag, version


def validate_version(version: str) -> str:
    """Validate version format."""
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        sys.exit(f"Error: version must match ^\\d+\\.\\d+\\.\\d+$ (got: {version})")
    return version


def get_current_version() -> str:
    """Get current version from pyproject.toml."""
    pyproject = Path("pyproject.toml")
    if not pyproject.exists():
        sys.exit("Error: pyproject.toml not found")

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        version = data.get("project", {}).get("version", "")
        if not version:
            sys.exit("Error: version not found in pyproject.toml")
        return version
    except Exception as e:
        sys.exit(f"Error: failed to read pyproject.toml: {e}")


def update_version_in_pyproject(new_version: str):
    """Update version in pyproject.toml."""
    pyproject = Path("pyproject.toml")
    content = pyproject.read_text()

    # Find and replace version in [project] section only (not target-version or other version fields)
    # Match: version = "..." but only in the [project] section
    pattern = r'(\[project\]\s*(?:[^\[]|\[[^\]]*\])*?version\s*=\s*")[^"]+(")'

    # First try the more specific pattern
    updated = re.sub(pattern, f"\\g<1>{new_version}\\g<2>", content, flags=re.DOTALL)

    # If that didn't work, try a simpler pattern that matches version right after [project]
    if updated == content:
        pattern = r'(\[project\]\s*(?:[^\[]|\n)*?version\s*=\s*")[^"]+(")'
        updated = re.sub(pattern, f"\\g<1>{new_version}\\g<2>", content, flags=re.DOTALL)

    # If still no match, use line-by-line replacement in [project] section
    if updated == content:
        lines = content.split("\n")
        in_project_section = False
        for i, line in enumerate(lines):
            if line.strip().startswith("[project]"):
                in_project_section = True
            elif line.strip().startswith("[") and not line.strip().startswith("[project."):
                in_project_section = False
            elif in_project_section and re.match(r'^\s*version\s*=\s*"[^"]+"', line):
                lines[i] = re.sub(r'(version\s*=\s*")[^"]+(")', f"\\g<1>{new_version}\\g<2>", line)
                updated = "\n".join(lines)
                break

        if updated == content:
            sys.exit("Error: could not find version in [project] section of pyproject.toml")

    pyproject.write_text(updated)
    print(f'Updated pyproject.toml: version = "{new_version}"')


def ensure_clean_working_tree():
    """Check if working tree is clean, exit if dirty."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit("Error: not in a git repository")
    if result.stdout.strip():
        sys.exit("Error: working tree is dirty. Commit or stash changes before planning.")


def read_manifest() -> dict:
    """Read manifest, exit if missing."""
    if not MANIFEST_PATH.exists():
        sys.exit(f"Error: manifest not found at {MANIFEST_PATH}")
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def write_manifest(manifest: dict):
    """Write manifest to disk."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)


def target_to_filename(target: str, name: str) -> str:
    """Convert target to filename: macos-arm64 -> {name}-darwin-arm64.tar.gz."""
    parts = target.split("-")
    if len(parts) != 2:
        sys.exit(f"Error: invalid target format: {target}")
    os_part, arch_part = parts
    os_name = "darwin" if os_part == "macos" else os_part
    return f"{name}-{os_name}-{arch_part}.tar.gz"


def cmd_plan(args):
    """Plan command: create manifest from tag and targets."""
    tag, version = validate_tag(args.tag)
    ensure_clean_working_tree()

    targets = args.targets.split(",") if args.targets else DEFAULT_TARGETS
    targets = [t.strip() for t in targets]

    assets = []
    for target in targets:
        filename = target_to_filename(target, args.name)
        assets.append(
            {
                "target": target,
                "filename": filename,
                "sha256": "",
                "url": "",
            }
        )

    # Auto-generate create_python_binaries.sh if Python project detected
    if not getattr(args, "no_auto_python", False):
        entry = detect_python_entry_point(args.name)
        if entry:
            module, func = entry
            script_path = generate_python_binaries_script(args.name, module, func, targets, "build")
            print(
                f"\nPython project detected: {args.name} ({module}:{func})\n"
                f"Generated: {script_path}\n"
                f"This script can be run in CI to create wrapper binaries before packaging."
            )

    manifest = {
        "tag": tag,
        "version": version,
        "name": args.name,
        "assets": assets,
    }

    write_manifest(manifest)
    print(f"Created manifest: {MANIFEST_PATH}")
    print(f"  Tag: {tag}")
    print(f"  Targets: {', '.join(targets)}")


def cmd_build(args):
    """Build command: package binaries, compute checksums, update manifest."""
    tag, version = validate_tag(args.tag)
    manifest = read_manifest()

    if manifest["tag"] != tag:
        sys.exit(f"Error: manifest tag ({manifest['tag']}) does not match CLI arg ({tag})")

    artifacts_dir = Path(args.artifacts_dir)
    dist_dir = Path(args.dist)
    dist_dir.mkdir(parents=True, exist_ok=True)

    repo = args.repo or get_repo_from_env()

    # Filter assets by target if --target is specified
    assets_to_process = manifest["assets"]
    if args.target:
        assets_to_process = [a for a in manifest["assets"] if a["target"] == args.target]
        if not assets_to_process:
            sys.exit(f"Error: target '{args.target}' not found in manifest")

    # Auto-run create_python_binaries.sh if needed
    if not getattr(args, "no_auto_python", False):
        # Check if any binaries are missing
        missing_targets = []
        for asset in assets_to_process:
            target = asset["target"]
            target_dir = artifacts_dir / target
            binary_exists = False
            if target_dir.exists():
                for item in target_dir.iterdir():
                    if item.is_file() and item.name.startswith(manifest["name"]):
                        binary_exists = True
                        break
            if not binary_exists:
                missing_targets.append(target)

        # If binaries are missing, try to auto-run the script
        if missing_targets:
            script_path = Path("create_python_binaries.sh").resolve()
            entry = detect_python_entry_point(manifest["name"])

            if entry and script_path.exists():
                module, func = entry
                print(
                    f"\nPython project detected: {manifest['name']} ({module}:{func})", flush=True
                )
                print(f"Binaries missing for targets: {', '.join(missing_targets)}", flush=True)
                print(f"Auto-running {script_path} to create wrapper binaries...\n", flush=True)

                # Ensure script is executable
                script_path.chmod(0o755)

                result = subprocess.run(
                    ["/bin/bash", str(script_path)],
                    capture_output=False,
                    check=False,
                )

                if result.returncode != 0:
                    sys.exit(f"Error: failed to run {script_path} (exit code: {result.returncode})")

                print(f"Successfully created wrapper binaries via {script_path}\n", flush=True)

    for asset in assets_to_process:
        target = asset["target"]
        filename = asset["filename"]

        # Find binary
        target_dir = artifacts_dir / target
        if not target_dir.exists():
            sys.exit(f"Error: artifacts directory not found: {target_dir}")

        binary_path = None
        for item in target_dir.iterdir():
            if item.is_file() and item.name.startswith(manifest["name"]):
                binary_path = item
                break

        if not binary_path:
            sys.exit(
                f"Error: binary not found in {target_dir} (looking for {manifest['name']}*). "
                "Ensure binaries are built and placed in the artifacts directory, "
                "or run create_python_binaries.sh manually for Python projects. "
                "Use --no-auto-python to disable automatic script execution."
            )

        # Package to tarball
        package_path = dist_dir / filename

        with tarfile.open(package_path, "w:gz") as tar:
            tar.add(binary_path, arcname=manifest["name"])

        # Compute SHA256
        sha256 = hashlib.sha256(package_path.read_bytes()).hexdigest()

        # Set URL
        url = f"https://github.com/{repo}/releases/download/{tag}/{filename}"

        asset["sha256"] = sha256
        asset["url"] = url

        print(f"Packaged: {package_path.name} (SHA256: {sha256[:16]}...)")

    write_manifest(manifest)
    print(f"Updated manifest: {MANIFEST_PATH}")


def detect_python_entry_point(name: str) -> tuple[str, str] | None:
    """Detect Python project and return (module, function) entry point, or None."""
    pyproject = Path("pyproject.toml")
    if not pyproject.exists():
        return None

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)

        # Check [project.scripts]
        scripts = data.get("project", {}).get("scripts", {})
        if name in scripts:
            entry = scripts[name]
            # Parse "module:function" or "module" format
            if ":" in entry:
                module, func = entry.split(":", 1)
                return (module, func)
            else:
                # Assume "main" function if not specified
                return (entry, "main")

        # Check [project.entry-points.console_scripts] (alternative)
        entry_points = data.get("project", {}).get("entry-points", {})
        console_scripts = entry_points.get("console_scripts", {})
        if name in console_scripts:
            entry = console_scripts[name]
            if ":" in entry:
                module, func = entry.split(":", 1)
                return (module, func)
            else:
                return (entry, "main")
    except Exception:
        # If parsing fails, silently return None
        return None

    return None


def generate_python_binaries_script(
    name: str, module: str, function: str, targets: list[str], target_dir: str
) -> Path:
    """Generate create_python_binaries.sh script for a Python project."""
    script_path = Path("create_python_binaries.sh")

    # Generate the script content
    script_content = f"""#!/bin/bash
# Helper script to create "binary" wrappers for Python CLI tools
# Auto-generated by Feza
# Usage: ./create_python_binaries.sh

NAME="{name}"
MODULE="{module}"
FUNCTION="{function}"
TARGET_DIR="{target_dir}"

"""

    # Add mkdir for each target
    for target in targets:
        script_content += f'mkdir -p "${{TARGET_DIR}}/{target}"\n'

    script_content += "\n# Create wrapper scripts for each target\n"

    # Generate wrapper for each target
    for target in targets:
        wrapper_code = f"""#!/usr/bin/env python3
import sys
from {module} import {function}
if __name__ == "__main__":
    sys.exit({function}())"""
        script_content += f"cat > \"${{TARGET_DIR}}/{target}/${{NAME}}\" << 'EOFWRAPPER'\n"
        script_content += wrapper_code + "\n"
        script_content += "EOFWRAPPER\n\n"

    # Make them executable
    for target in targets:
        script_content += f'chmod +x "${{TARGET_DIR}}/{target}/${{NAME}}"\n'

    script_content += '\necho "Created wrapper scripts for ${NAME} in ${TARGET_DIR}/"\necho "These will be packaged by Feza as \'binaries\'"\n'

    script_path.write_text(script_content)
    script_path.chmod(0o755)  # Make executable

    return script_path


def cmd_bump(args):
    """Bump command: update version in pyproject.toml."""
    current_version = get_current_version()
    current_tag = f"v{current_version}"

    print(f"Current version: {current_version} (tag: {current_tag})")

    if args.version:
        new_version = validate_version(args.version)
    else:
        # Interactive prompt
        new_version_input = input("Enter new version (e.g., 0.2.0): ").strip()
        if not new_version_input:
            sys.exit("Error: version is required")
        new_version = validate_version(new_version_input)

    if new_version == current_version:
        sys.exit(f"Error: new version ({new_version}) is same as current version")

    print(f"\nUpdating version: {current_version} → {new_version}")

    # Update pyproject.toml
    update_version_in_pyproject(new_version)

    # Commit if requested
    if args.commit or args.push:
        result = subprocess.run(
            ["git", "add", "pyproject.toml"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            sys.exit(f"Error: failed to stage pyproject.toml: {result.stderr}")

        tag = f"v{new_version}"
        result = subprocess.run(
            ["git", "commit", "-m", f"chore: bump version to {new_version}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            sys.exit(f"Error: failed to commit: {result.stderr}")
        print("Committed version change")

    # Push if requested
    if args.push:
        if not args.commit:
            sys.exit("Error: --push requires --commit")

        result = subprocess.run(
            ["git", "push", "origin", "HEAD"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            sys.exit(f"Error: failed to push: {result.stderr}")
        print(f"Pushed to origin. Auto-tag workflow will create tag {tag} automatically")

    print(f"\n✓ Version updated to {new_version}")
    if not args.commit:
        print("Run 'feza bump --commit --push' to commit and push (triggers auto-release)")


def get_repo_from_env() -> str:
    """Get repo from GITHUB_REPOSITORY env var."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        sys.exit("Error: --repo required or set GITHUB_REPOSITORY environment variable")
    return repo


def cmd_github(args):
    """GitHub command: create/update draft release and upload assets."""
    tag, version = validate_tag(args.tag)
    manifest = read_manifest()

    if manifest["tag"] != tag:
        sys.exit(f"Error: manifest tag ({manifest['tag']}) does not match CLI arg ({tag})")

    # Validate all assets have sha256
    for asset in manifest["assets"]:
        if not asset.get("sha256"):
            sys.exit(f"Error: asset {asset['filename']} missing sha256 in manifest")

    repo = args.repo or get_repo_from_env()
    dist_dir = Path(args.dist)

    # Check if release exists
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        # Create release (publish if --draft not specified, otherwise draft)
        cmd = ["gh", "release", "create", tag, "--repo", repo, "--title", tag]
        if args.draft:
            cmd.append("--draft")
        if args.release_notes and Path(args.release_notes).exists():
            notes = render_release_notes(args.release_notes, manifest)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
                f.write(notes)
                notes_path = f.name
            cmd.extend(["--notes-file", notes_path])
        subprocess.run(cmd, check=True)
        print(f"Created draft release: {tag}")
    else:
        print(f"Release {tag} already exists (updating)")

    # Upload assets
    for asset in manifest["assets"]:
        file_path = dist_dir / asset["filename"]
        if not file_path.exists():
            sys.exit(f"Error: asset file not found: {file_path}")

        # Check if asset already uploaded
        result = subprocess.run(
            ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
            capture_output=True,
            text=True,
            check=True,
        )
        release_data = json.loads(result.stdout)
        existing_names = [a["name"] for a in release_data.get("assets", [])]

        if asset["filename"] in existing_names:
            print(f"  Asset already uploaded: {asset['filename']}")
            continue

        subprocess.run(
            ["gh", "release", "upload", tag, str(file_path), "--repo", repo],
            check=True,
        )
        print(f"  Uploaded: {asset['filename']}")


def resolve_github_token(interactive=True):
    """
    Resolve a usable GitHub token for cross-repo operations.
    Order of precedence:
      1. gh auth token
      2. GITHUB_TOKEN (env)
      3. TAP_PAT (env)
    If none found and interactive=True, prompt the user.
    """
    token = None
    source = None

    # 1. Try gh CLI
    try:
        gh_token = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
        )
        if gh_token.returncode == 0 and gh_token.stdout.strip():
            token = gh_token.stdout.strip()
            source = "gh"
    except Exception:
        pass

    # 2. Try environment variables
    if not token and os.getenv("GITHUB_TOKEN"):
        token = os.getenv("GITHUB_TOKEN")
        source = "GITHUB_TOKEN"
    if not token and os.getenv("TAP_PAT"):
        token = os.getenv("TAP_PAT")
        source = "TAP_PAT"

    # 3. Interactive fallback
    if not token and interactive:
        print("⚠️  No GitHub token found.")
        print("Feza can use your authenticated gh CLI or a Personal Access Token (PAT).")
        print("Options:")
        print("  [1] Use gh login (if already authenticated)")
        print("  [2] Enter a Personal Access Token manually")
        print("  [3] Cancel")
        choice = input("Select option [1/2/3]: ").strip()
        if choice == "1":
            print("Attempting to use gh auth token...")
            try:
                gh_token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
                if gh_token.returncode == 0:
                    token = gh_token.stdout.strip()
                    source = "gh"
            except Exception:
                print("❌ gh auth token failed.")
        elif choice == "2":
            token = input("Enter your GitHub Personal Access Token: ").strip()
            source = "manual"
        else:
            sys.exit("❌ Aborted by user.")

    if not token:
        sys.exit(
            "❌ No usable GitHub token found. Please authenticate with `gh auth login` or set TAP_PAT."
        )
    else:
        print(f"[auth] Using {source} token for tap operations.")
    return token


def ensure_tap_repo_exists(
    tap_repo: str, token: str, create_if_missing: bool = False, interactive: bool = True
) -> bool:
    """
    Check if tap repository exists, create if missing.
    Returns True if repo exists (or was created), False if not.
    """
    # Check if repo exists
    result = subprocess.run(
        ["gh", "repo", "view", tap_repo],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        return True  # Repo exists

    # Repo doesn't exist
    if not create_if_missing and not interactive:
        return False  # Fail silently in non-interactive mode

    if interactive:
        print(f"⚠️  Tap repository '{tap_repo}' doesn't exist.")
        print("Feza can create it for you automatically.")
        choice = input("Create tap repository? [y/N]: ").strip().lower()
        if choice != "y":
            return False

    # Create the repo
    print(f"Creating tap repository: {tap_repo}")

    # Create repo with gh CLI
    result = subprocess.run(
        [
            "gh",
            "repo",
            "create",
            tap_repo,
            "--public",  # Homebrew taps should be public
            "--description",
            "Homebrew tap",
            # Note: --clone is a boolean flag. Omit it to avoid cloning.
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        sys.exit(f"Error: Failed to create tap repository: {result.stderr}")

    # Initialize the repo structure
    with tempfile.TemporaryDirectory() as tmpdir:
        init_dir = Path(tmpdir) / "tap-init"
        init_dir.mkdir()

        # Create Formula directory
        (init_dir / "Formula").mkdir()

        # Extract package name from tap repo (homebrew-<name> -> <name>)
        if "/" in tap_repo:
            _, repo_name = tap_repo.split("/", 1)
            package_name = repo_name.replace("homebrew-", "")
        else:
            package_name = "packages"

        # Create README.md
        org = tap_repo.split("/")[0] if "/" in tap_repo else "unknown"
        readme_content = f"""# {tap_repo.split("/")[-1]}

Homebrew tap for {org} packages.

## Installation

```bash
brew tap {org}/{package_name}
brew install <package>
```
"""
        (init_dir / "README.md").write_text(readme_content)

        # Initialize git and push
        subprocess.run(["git", "init", "-b", "main"], cwd=init_dir, check=True)
        subprocess.run(
            ["git", "config", "user.name", "github-actions[bot]"],
            cwd=init_dir,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
            cwd=init_dir,
            check=True,
        )

        subprocess.run(["git", "add", "."], cwd=init_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit: tap repository setup"],
            cwd=init_dir,
            check=True,
        )

        # Push to remote
        repo_url = f"https://{token}@github.com/{tap_repo}.git"
        subprocess.run(
            ["git", "remote", "add", "origin", repo_url],
            cwd=init_dir,
            check=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=init_dir,
            check=True,
        )

    print(f"✅ Created tap repository: {tap_repo}")
    return True


def cmd_tap(args):
    """Tap command: render Homebrew formula and push to tap repo."""
    tag, version = validate_tag(args.tag)
    manifest = read_manifest()

    if manifest["tag"] != tag:
        sys.exit(f"Error: manifest tag ({manifest['tag']}) does not match CLI arg ({tag})")

    if not args.tap:
        # Auto-detect tap repo if not provided
        main_repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
        if main_repo:
            org = main_repo.split("/")[0]
            tool_name = manifest["name"].lower()
            args.tap = f"{org}/homebrew-{tool_name}"
            print(f"Auto-detected tap repository: {args.tap}")
        else:
            sys.exit("Error: --tap required or set GITHUB_REPOSITORY environment variable")

    if not args.formula:
        sys.exit("Error: --formula required")

    # Determine token automatically (interactive for local runs)
    # --auto flag enables fully automatic mode (non-interactive + auto-create tap)
    auto_mode = getattr(args, "auto", False)
    ci_mode = os.getenv("CI") or getattr(args, "non_interactive", False) or auto_mode
    tap_pat = resolve_github_token(interactive=not ci_mode)

    # Check/create tap repo if needed
    create_tap = getattr(args, "create_tap", False) or auto_mode
    if not ensure_tap_repo_exists(args.tap, tap_pat, create_tap, interactive=not ci_mode):
        sys.exit(
            f"Error: Tap repository '{args.tap}' doesn't exist. "
            "Create it manually or use --create-tap to auto-create."
        )

    # Render formula
    formula_content = render_formula(args.formula_template, manifest, args.formula, args)

    # Clone tap repo
    with tempfile.TemporaryDirectory() as tmpdir:
        tap_dir = Path(tmpdir) / "tap"
        repo_url = f"https://{tap_pat}@github.com/{args.tap}.git"
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(tap_dir)],
            check=True,
        )

        branch = args.branch or f"feza/{tag}"
        subprocess.run(["git", "checkout", "-b", branch], cwd=tap_dir, check=True)

        # Configure git user for commits
        subprocess.run(
            ["git", "config", "user.name", "github-actions[bot]"],
            cwd=tap_dir,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
            cwd=tap_dir,
            check=True,
        )

        # Write formula
        formula_dir = tap_dir / "Formula"
        formula_dir.mkdir(parents=True, exist_ok=True)
        formula_path = formula_dir / f"{args.formula}.rb"
        formula_path.write_text(formula_content)

        # Commit and push
        subprocess.run(["git", "add", str(formula_path)], cwd=tap_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"Update {args.formula} to {tag}"],
            cwd=tap_dir,
            check=True,
        )

        # Try using gh auth setup-git to configure git to use gh CLI authentication
        # This might allow GITHUB_TOKEN to work for cross-repo if permissions allow
        env = os.environ.copy()
        if "GH_TOKEN" in env:
            env["GH_TOKEN"] = tap_pat
        else:
            env["GH_TOKEN"] = tap_pat

        # Try configuring git to use gh CLI for authentication
        subprocess.run(
            ["gh", "auth", "setup-git"],
            cwd=tap_dir,
            check=False,  # Don't fail if this doesn't work
            env=env,
        )

        # Set remote URL (without embedded token, let gh handle auth)
        subprocess.run(
            ["git", "remote", "set-url", "origin", f"https://github.com/{args.tap}.git"],
            cwd=tap_dir,
            check=True,
        )

        # Dry-run: skip push and PR operations
        if args.dry_run:
            print("[dry-run] Skipping git push. Formula generated successfully.")
            print(f"[dry-run] Would push branch {branch} to {args.tap}")
            if args.open_pr:
                print(f"[dry-run] Would open PR for branch {branch}")
            return

        # Try pushing - gh auth setup-git might make this work with GITHUB_TOKEN
        # If not, fall back to embedded token
        result = subprocess.run(
            ["git", "push", "origin", branch],
            cwd=tap_dir,
            capture_output=True,
            text=True,
            env=env,
        )

        if result.returncode != 0:
            # Fallback: try with embedded token in URL
            push_url = f"https://x-access-token:{tap_pat}@github.com/{args.tap}.git"
            subprocess.run(
                ["git", "remote", "set-url", "origin", push_url],
                cwd=tap_dir,
                check=True,
            )
            env["GIT_TERMINAL_PROMPT"] = "0"
            result = subprocess.run(
                ["git", "push", "origin", branch],
                cwd=tap_dir,
                capture_output=True,
                text=True,
                env=env,
            )

            if result.returncode != 0:
                sys.exit(
                    f"Error: Failed to push to {args.tap}. "
                    f"GITHUB_TOKEN may not have permissions. Error: {result.stderr[:200]}"
                )

        print(f"Pushed branch {branch} to {args.tap}")

        # Open PR if requested
        if args.open_pr:
            subprocess.run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    args.tap,
                    "--base",
                    "main",
                    "--head",
                    branch,
                    "--title",
                    f"Update {args.formula} to {tag}",
                    "--body",
                    f"Automated update via Feza for {tag}",
                ],
                check=True,
            )
            print(f"Opened PR: Update {args.formula} to {tag}")


def render_formula(template_path: str | None, manifest: dict, formula_name: str, args) -> str:
    """Render Homebrew formula from Jinja2 template."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    if template_path:
        template_file = Path(template_path)
        env = Environment(
            loader=FileSystemLoader(template_file.parent),
            autoescape=select_autoescape(),
        )
        template = env.get_template(template_file.name)
    else:
        # Use default template
        default_template_dir = Path(__file__).parent / "templates"
        env = Environment(
            loader=FileSystemLoader(default_template_dir),
            autoescape=select_autoescape(),
        )
        template = env.get_template("formula.rb.j2")

    def url_by(target: str) -> str:
        for asset in manifest["assets"]:
            if asset["target"] == target:
                return asset["url"]
        return ""

    def sha_by(target: str) -> str:
        for asset in manifest["assets"]:
            if asset["target"] == target:
                return asset["sha256"]
        return ""

    return template.render(
        formula_name=formula_name,
        name=manifest["name"],
        version=manifest["version"],
        desc=getattr(args, "desc", "CLI tool"),
        homepage=getattr(args, "homepage", f"https://github.com/{args.repo or 'unknown/repo'}"),
        url_by=url_by,
        sha_by=sha_by,
    )


def render_release_notes(template_path: str, manifest: dict) -> str:
    """Render release notes from Jinja2 template."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    template_file = Path(template_path)
    env = Environment(
        loader=FileSystemLoader(template_file.parent),
        autoescape=select_autoescape(),
    )
    template = env.get_template(template_file.name)

    return template.render(
        name=manifest["name"],
        version=manifest["version"],
        assets=manifest["assets"],
    )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Feza - plan, build, publish, and tap releases")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Shared args
    def add_shared_args(p):
        p.add_argument("tag", help="Release tag (e.g., v1.2.3)")
        p.add_argument("--name", required=True, help="Tool name")

    # plan
    plan_parser = subparsers.add_parser("plan", help="Create manifest from tag and targets")
    add_shared_args(plan_parser)
    plan_parser.add_argument(
        "--targets",
        help=f"Comma-separated targets (default: {','.join(DEFAULT_TARGETS)})",
    )
    plan_parser.add_argument(
        "--no-auto-python",
        action="store_true",
        help="Disable automatic Python script generation",
    )

    # build
    build_parser = subparsers.add_parser("build", help="Package binaries and compute checksums")
    add_shared_args(build_parser)
    build_parser.add_argument("--artifacts-dir", default="build", help="Artifacts directory")
    build_parser.add_argument("--dist", default="dist", help="Distribution directory")
    build_parser.add_argument("--repo", help="GitHub repo (default: GITHUB_REPOSITORY env)")
    build_parser.add_argument(
        "--target", help="Filter to only build this target (e.g., macos-arm64)"
    )
    build_parser.add_argument(
        "--no-auto-python",
        action="store_true",
        help="Disable automatic Python wrapper creation",
    )

    # github
    github_parser = subparsers.add_parser("github", help="Create/update GitHub release")
    add_shared_args(github_parser)
    github_parser.add_argument("--repo", help="GitHub repo (default: GITHUB_REPOSITORY env)")
    github_parser.add_argument("--release-notes", help="Release notes template path")
    github_parser.add_argument(
        "--draft",
        action="store_true",
        help="Create as draft release (default: false, publishes immediately)",
    )
    github_parser.add_argument("--dist", default="dist", help="Distribution directory")

    # bump
    bump_parser = subparsers.add_parser("bump", help="Bump version in pyproject.toml")
    bump_parser.add_argument(
        "--version",
        help="New version (e.g., 0.2.0). If not provided, will prompt interactively.",
    )
    bump_parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit the version change",
    )
    bump_parser.add_argument(
        "--push",
        action="store_true",
        help="Push to remote (requires --commit)",
    )

    # tap
    tap_parser = subparsers.add_parser("tap", help="Render and push Homebrew formula")
    add_shared_args(tap_parser)
    tap_parser.add_argument(
        "--tap",
        help="Homebrew tap repo (org/name). Auto-detected from GITHUB_REPOSITORY if not provided.",
    )
    tap_parser.add_argument("--formula", required=True, help="Formula name")
    tap_parser.add_argument("--branch", help="Branch name (default: feza/{tag})")
    tap_parser.add_argument("--open-pr", action="store_true", help="Open PR after push")
    tap_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render formula and show git commands without pushing",
    )
    tap_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable interactive prompts (fail if no token found)",
    )
    tap_parser.add_argument(
        "--create-tap",
        action="store_true",
        help="Automatically create tap repository if it doesn't exist",
    )
    tap_parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto-mode: non-interactive with automatic tap creation (for CI/agents)",
    )
    tap_parser.add_argument(
        "--formula-template",
        help="Formula template path (default: templates/formula.rb.j2)",
    )
    tap_parser.add_argument("--repo", help="Homepage repo for formula")
    tap_parser.add_argument("--desc", help="Formula description")
    tap_parser.add_argument("--homepage", help="Formula homepage")

    args = parser.parse_args()

    if args.command == "plan":
        cmd_plan(args)
    elif args.command == "build":
        cmd_build(args)
    elif args.command == "github":
        cmd_github(args)
    elif args.command == "bump":
        cmd_bump(args)
    elif args.command == "tap":
        cmd_tap(args)
