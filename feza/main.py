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
from pathlib import Path

DEFAULT_TARGETS = ["macos-arm64", "macos-amd64", "linux-amd64"]
MANIFEST_PATH = Path("dist/feza_manifest.json")


def validate_tag(tag: str) -> tuple[str, str]:
    """Validate tag format and return (tag, version)."""
    if not re.match(r"^v\d+\.\d+\.\d+$", tag):
        sys.exit(f"Error: tag must match ^v\\d+\\.\\d+\\.\\d+$ (got: {tag})")
    version = tag.lstrip("v")
    return tag, version


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

    for asset in manifest["assets"]:
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
            sys.exit(f"Error: binary not found in {target_dir} (looking for {manifest['name']}*)")

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
        # Create draft release
        cmd = ["gh", "release", "create", tag, "--repo", repo, "--draft", "--title", tag]
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


def cmd_tap(args):
    """Tap command: render Homebrew formula and push to tap repo."""
    tag, version = validate_tag(args.tag)
    manifest = read_manifest()

    if manifest["tag"] != tag:
        sys.exit(f"Error: manifest tag ({manifest['tag']}) does not match CLI arg ({tag})")

    if not args.tap:
        sys.exit("Error: --tap required")
    if not args.formula:
        sys.exit("Error: --formula required")

    tap_pat = os.environ.get("TAP_PAT")
    if not tap_pat:
        sys.exit("Error: TAP_PAT environment variable required for tap operations")

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

        env = os.environ.copy()
        env["GIT_ASKPASS"] = "echo"
        env["GIT_TERMINAL_PROMPT"] = "0"
        push_url = f"https://{tap_pat}@github.com/{args.tap}.git"

        subprocess.run(
            ["git", "push", push_url, branch],
            cwd=tap_dir,
            check=True,
            env=env,
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

    # build
    build_parser = subparsers.add_parser("build", help="Package binaries and compute checksums")
    add_shared_args(build_parser)
    build_parser.add_argument("--artifacts-dir", default="build", help="Artifacts directory")
    build_parser.add_argument("--dist", default="dist", help="Distribution directory")
    build_parser.add_argument("--repo", help="GitHub repo (default: GITHUB_REPOSITORY env)")

    # github
    github_parser = subparsers.add_parser("github", help="Create/update GitHub release")
    add_shared_args(github_parser)
    github_parser.add_argument("--repo", help="GitHub repo (default: GITHUB_REPOSITORY env)")
    github_parser.add_argument("--release-notes", help="Release notes template path")
    github_parser.add_argument(
        "--draft",
        action="store_true",
        default=True,
        help="Create as draft release (default: true)",
    )
    github_parser.add_argument("--dist", default="dist", help="Distribution directory")

    # tap
    tap_parser = subparsers.add_parser("tap", help="Render and push Homebrew formula")
    add_shared_args(tap_parser)
    tap_parser.add_argument("--tap", required=True, help="Homebrew tap repo (org/name)")
    tap_parser.add_argument("--formula", required=True, help="Formula name")
    tap_parser.add_argument("--branch", help="Branch name (default: feza/{tag})")
    tap_parser.add_argument("--open-pr", action="store_true", help="Open PR after push")
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
    elif args.command == "tap":
        cmd_tap(args)
