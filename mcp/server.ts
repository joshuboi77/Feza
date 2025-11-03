#!/usr/bin/env node
/**
 * Feza MCP Server
 * 
 * Model Context Protocol server that exposes Feza CLI commands as tools
 * for use in Cursor and other MCP-compatible IDEs.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  ErrorCode,
  McpError,
} from "@modelcontextprotocol/sdk/types.js";
import { spawn } from "node:child_process";
import { z } from "zod";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const server = new Server(
  {
    name: "feza-mcp",
    version: "0.5.10",
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

/**
 * Run Feza command and return result
 */
function runFeza(
  args: string[],
  cwd?: string
): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const cmd = "feza";
    const workingDir = cwd || process.cwd();
    const childProcess = spawn(cmd, args, {
      cwd: workingDir,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";

    childProcess.stdout?.on("data", (data: Buffer) => {
      stdout += data.toString();
    });

    childProcess.stderr?.on("data", (data: Buffer) => {
      stderr += data.toString();
    });

    childProcess.on("close", (code: number | null) => {
      resolve({
        code: code ?? 1,
        stdout: stdout.trim(),
        stderr: stderr.trim(),
      });
    });

    childProcess.on("error", (error: Error) => {
      resolve({
        code: 1,
        stdout: "",
        stderr: `Failed to spawn feza: ${error.message}`,
      });
    });
  });
}

// Tool registry
const tools: Array<{
  name: string;
  description: string;
  schema: z.ZodTypeAny;
  toArgs: (input: any) => string[];
}> = [];

/**
 * Register a tool with the MCP server
 */
function registerTool(
  name: string,
  description: string,
  schema: z.ZodTypeAny,
  toArgs: (input: any) => string[]
) {
  tools.push({ name, description, schema, toArgs });
}

// Tool schemas using Zod
const PlanSchema = z.object({
  tag: z.string().describe("Release tag (e.g., v1.2.3)"),
  name: z.string().describe("Tool name"),
  targets: z
    .string()
    .optional()
    .describe(
      "Comma-separated targets (default: macos-arm64,macos-amd64,linux-amd64)"
    ),
  cwd: z.string().optional().describe("Working directory (default: current)"),
});

const BuildSchema = z.object({
  tag: z.string().describe("Release tag (e.g., v1.2.3)"),
  name: z.string().describe("Tool name"),
  repo: z
    .string()
    .optional()
    .describe("GitHub repository (org/repo format)"),
  dist: z
    .string()
    .optional()
    .describe("Distribution directory (default: dist)"),
  artifactsDir: z
    .string()
    .optional()
    .describe("Artifacts directory (default: build)"),
  noAutoPython: z
    .boolean()
    .optional()
    .describe("Disable automatic Python wrapper creation"),
  cwd: z.string().optional().describe("Working directory (default: current)"),
});

const GitHubSchema = z.object({
  tag: z.string().describe("Release tag (e.g., v1.2.3)"),
  name: z.string().describe("Tool name"),
  repo: z
    .string()
    .optional()
    .describe("GitHub repository (org/repo format)"),
  dist: z
    .string()
    .optional()
    .describe("Distribution directory (default: dist)"),
  cwd: z.string().optional().describe("Working directory (default: current)"),
});

const TapSchema = z.object({
  tag: z.string().describe("Release tag (e.g., v1.2.3)"),
  name: z.string().describe("Tool name"),
  formula: z.string().describe("Formula name (e.g., Feza, Crow)"),
  tap: z
    .string()
    .optional()
    .describe(
      "Homebrew tap repo (org/name). Auto-detected if not provided."
    ),
  branch: z
    .string()
    .optional()
    .describe("Branch name (default: feza/{tag})"),
  openPr: z
    .boolean()
    .optional()
    .describe("Open PR after push (default: false)"),
  auto: z
    .boolean()
    .optional()
    .describe(
      "Auto-mode: non-interactive with automatic tap creation (for CI/agents)"
    ),
  nonInteractive: z
    .boolean()
    .optional()
    .describe("Disable interactive prompts (fail if no token found)"),
  dryRun: z
    .boolean()
    .optional()
    .describe("Render formula and show git commands without pushing"),
  formulaTemplate: z
    .string()
    .optional()
    .describe("Formula template path (default: templates/formula.rb.j2)"),
  repo: z.string().optional().describe("Homepage repo for formula"),
  desc: z.string().optional().describe("Formula description"),
  homepage: z.string().optional().describe("Formula homepage URL"),
  cwd: z.string().optional().describe("Working directory (default: current)"),
});

// Register all tools
registerTool(
  "feza_plan",
  "Plan a release: create manifest from tag and targets",
  PlanSchema,
  (input) => {
    const args = ["plan", input.tag, "--name", input.name];
    if (input.targets) args.push("--targets", input.targets);
    return args;
  }
);

registerTool(
  "feza_build",
  "Build and package binaries: compute checksums and update manifest",
  BuildSchema,
  (input) => {
    const args = ["build", input.tag, "--name", input.name];
    if (input.repo) args.push("--repo", input.repo);
    if (input.dist) args.push("--dist", input.dist);
    if (input.artifactsDir) args.push("--artifacts-dir", input.artifactsDir);
    if (input.noAutoPython) args.push("--no-auto-python");
    return args;
  }
);

registerTool(
  "feza_github",
  "Create or update GitHub release with assets from manifest",
  GitHubSchema,
  (input) => {
    const args = ["github", input.tag, "--name", input.name];
    if (input.repo) args.push("--repo", input.repo);
    if (input.dist) args.push("--dist", input.dist);
    return args;
  }
);

registerTool(
  "feza_tap",
  "Render and push Homebrew formula to tap repository",
  TapSchema,
  (input) => {
    const args = [
      "tap",
      input.tag,
      "--name",
      input.name,
      "--formula",
      input.formula,
    ];
    if (input.tap) args.push("--tap", input.tap);
    if (input.branch) args.push("--branch", input.branch);
    if (input.openPr) args.push("--open-pr");
    if (input.auto) args.push("--auto");
    if (input.nonInteractive) args.push("--non-interactive");
    if (input.dryRun) args.push("--dry-run");
    if (input.formulaTemplate)
      args.push("--formula-template", input.formulaTemplate);
    if (input.repo) args.push("--repo", input.repo);
    if (input.desc) args.push("--desc", input.desc);
    if (input.homepage) args.push("--homepage", input.homepage);
    return args;
  }
);

// Start the server
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("Feza MCP server running on stdio");
}

main().catch((error) => {
  console.error("Fatal error in main():", error);
  process.exit(1);
});
