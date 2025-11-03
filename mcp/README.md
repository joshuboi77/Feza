# Feza MCP Server

Model Context Protocol (MCP) server that exposes Feza CLI commands as tools for use in Cursor and other MCP-compatible IDEs.

## Overview

This MCP server allows you to use Feza directly within Cursor, making release workflows accessible to AI agents and automation. The server exposes four main tools:

- **feza_plan** - Create release manifest from tag and targets
- **feza_build** - Package binaries and compute checksums
- **feza_github** - Create/update GitHub releases
- **feza_tap** - Render and push Homebrew formulas

## Installation

### Prerequisites

- Node.js 18+ 
- Feza installed (Homebrew: `brew install feza` or `pip install feza`)
- Feza must be in your PATH

### Setup

1. Install dependencies:
```bash
cd mcp
npm install
```

2. Build the TypeScript code:
```bash
npm run build
```

3. The server will be available at `dist/server.js`

## Cursor Configuration

Add Feza MCP server to your Cursor configuration:

### macOS/Linux

Edit or create `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "feza": {
      "command": "node",
      "args": ["/absolute/path/to/Feza/mcp/dist/server.js"],
      "env": {
        "GH_TOKEN": "your-github-token-here"
      }
    }
  }
}
```

### Windows

Edit or create `%USERPROFILE%\.cursor\mcp.json`:

```json
{
  "mcpServers": {
    "feza": {
      "command": "node",
      "args": ["C:\\full\\path\\to\\Feza\\mcp\\dist\\server.js"],
      "env": {
        "GH_TOKEN": "your-github-token-here"
      }
    }
  }
}
```

### Environment Variables

The MCP server passes through environment variables to Feza. Feza supports:

- `GH_TOKEN` or `GITHUB_TOKEN` - GitHub token for releases (recommended)
- `TAP_PAT` - Personal Access Token for cross-repo tap writes
- `GITHUB_REPOSITORY` - Default repository (org/repo format)

Feza automatically uses `gh auth token` if available, so you may not need to set tokens explicitly.

## Usage in Cursor

After configuring and restarting Cursor:

1. Open Cursor's tools panel
2. You should see `feza_plan`, `feza_build`, `feza_github`, and `feza_tap` available
3. Use them in chat or agent mode to automate releases

### Example Agent Workflow

```
1. feza_plan(tag="v1.0.0", name="mytool")
2. feza_build(tag="v1.0.0", name="mytool", repo="org/repo")
3. feza_github(tag="v1.0.0", name="mytool", repo="org/repo")
4. feza_tap(tag="v1.0.0", name="mytool", formula="Mytool", 
            tap="org/homebrew-tap", openPr=true, auto=true, 
            nonInteractive=true)
```

## Development

### Running in Development Mode

```bash
npm run dev
```

This uses `tsx` to run TypeScript directly without building.

### Building

```bash
npm run build
```

### Testing Locally

You can test the server manually using the MCP inspector or by connecting from Cursor.

## Tool Reference

### feza_plan

Creates a release manifest from tag and tool name.

**Parameters:**
- `tag` (string, required) - Release tag (e.g., "v1.2.3")
- `name` (string, required) - Tool name
- `targets` (string, optional) - Comma-separated targets
- `cwd` (string, optional) - Working directory

### feza_build

Packages binaries, computes checksums, and updates manifest.

**Parameters:**
- `tag` (string, required) - Release tag
- `name` (string, required) - Tool name
- `repo` (string, optional) - GitHub repository
- `dist` (string, optional) - Distribution directory
- `artifactsDir` (string, optional) - Artifacts directory
- `noAutoPython` (boolean, optional) - Disable auto Python wrapper creation
- `cwd` (string, optional) - Working directory

### feza_github

Creates or updates GitHub release with assets from manifest.

**Parameters:**
- `tag` (string, required) - Release tag
- `name` (string, required) - Tool name
- `repo` (string, optional) - GitHub repository
- `dist` (string, optional) - Distribution directory
- `cwd` (string, optional) - Working directory

### feza_tap

Renders and pushes Homebrew formula to tap repository.

**Parameters:**
- `tag` (string, required) - Release tag
- `name` (string, required) - Tool name
- `formula` (string, required) - Formula name (PascalCase)
- `tap` (string, optional) - Tap repository (auto-detected if not provided)
- `branch` (string, optional) - Branch name (default: feza/{tag})
- `openPr` (boolean, optional) - Open PR after push
- `auto` (boolean, optional) - Auto-mode (non-interactive + auto-create tap)
- `nonInteractive` (boolean, optional) - Disable interactive prompts
- `dryRun` (boolean, optional) - Show commands without executing
- `formulaTemplate` (string, optional) - Custom template path
- `repo` (string, optional) - Homepage repository
- `desc` (string, optional) - Formula description
- `homepage` (string, optional) - Formula homepage URL
- `cwd` (string, optional) - Working directory

## Troubleshooting

### "feza: command not found"

Ensure Feza is installed and in your PATH:
- Homebrew: `brew install feza`
- pip: `pip install feza`
- Check: `which feza`

### Authentication Errors

Feza uses an authentication cascade:
1. `gh auth token` (if gh CLI is authenticated)
2. `GITHUB_TOKEN` environment variable
3. `TAP_PAT` environment variable
4. Interactive prompt (not suitable for agents)

For agents, set `GH_TOKEN` or `GITHUB_TOKEN` in the Cursor MCP config.

### MCP Server Not Appearing

1. Check that the path in `mcp.json` is absolute and correct
2. Restart Cursor after changing `mcp.json`
3. Check Cursor's MCP server logs for errors
4. Verify Node.js version: `node --version` (needs 18+)

## License

MIT (same as Feza)
