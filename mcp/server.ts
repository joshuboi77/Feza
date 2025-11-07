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
import { spawn, execSync } from "node:child_process";
import { z } from "zod";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const server = new Server(
  {
    name: "feza-mcp",
    version: "0.5.26",
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

/**
 * TOOL DISCOVERY FOR AI AGENTS:
 * 
 * These MCP tools are PREFERRED over running 'feza' CLI commands directly.
 * They provide better error handling and integration.
 * 
 * Typical workflow:
 * 1. feza_plan - Create release manifest
 * 2. feza_build - Package binaries
 * 3. feza_github - Create GitHub release
 * 4. feza_tap - Update Homebrew (use openPr=true!)
 */

/**
 * Detect project root directory by looking for git root or project indicators.
 * Falls back to current working directory if nothing found.
 */
function detectProjectRoot(): string {
  const startDir = process.cwd();
  
  // Try 1: Git root (most reliable for projects in git repos)
  try {
    const gitRoot = execSync("git rev-parse --show-toplevel", {
      cwd: startDir,
      encoding: "utf-8",
      stdio: ["pipe", "pipe", "pipe"],
    }).trim();
    if (gitRoot && existsSync(gitRoot)) {
      return gitRoot;
    }
  } catch {
    // Git command failed, continue to next method
  }
  
  // Try 2: Walk up directory tree looking for project indicators
  let currentDir = path.resolve(startDir);
  const root = path.parse(currentDir).root;
  
  while (currentDir !== root) {
    // Check for common project files
    const indicators = [
      "pyproject.toml",
      "package.json",
      "Cargo.toml",
      "go.mod",
      ".git",
      "Makefile",
    ];
    
    for (const indicator of indicators) {
      if (existsSync(path.join(currentDir, indicator))) {
        return currentDir;
      }
    }
    
    // Move up one directory
    currentDir = path.dirname(currentDir);
  }
  
  // Fallback: return current working directory
  return startDir;
}

/**
 * Resolve command path, checking PATH and common Homebrew locations
 */
function resolveCommand(cmd: string): string {
  // Try PATH first
  try {
    const whichResult = execSync(`which ${cmd}`, {
      encoding: "utf-8",
      stdio: ["pipe", "pipe", "pipe"],
    }).trim();
    if (whichResult && existsSync(whichResult)) {
      return whichResult;
    }
  } catch {
    // which failed, continue to Homebrew checks
  }

  // Try common Homebrew locations
  const homebrewPaths = [
    "/opt/homebrew/bin", // Apple Silicon
    "/usr/local/bin",    // Intel Mac / Linux with Homebrew
    process.env.HOMEBREW_PREFIX ? `${process.env.HOMEBREW_PREFIX}/bin` : null,
  ].filter(Boolean) as string[];

  for (const basePath of homebrewPaths) {
    const fullPath = path.join(basePath, cmd);
    if (existsSync(fullPath)) {
      return fullPath;
    }
  }

  // Fallback: return original command (spawn will handle the error)
  return cmd;
}

/**
 * Run Feza command and return result
 */
function runFeza(
  args: string[],
  cwd?: string
): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const cmdPath = resolveCommand("feza");
    const workingDir = cwd || detectProjectRoot();
    const childProcess = spawn(cmdPath, args, {
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
      if (error.message.includes("ENOENT")) {
        resolve({
          code: 1,
          stdout: "",
          stderr: `feza command not found. Install with: brew install feza\nOr ensure feza is on your PATH.\nOriginal error: ${error.message}`,
        });
      } else {
        resolve({
          code: 1,
          stdout: "",
          stderr: `Failed to spawn feza: ${error.message}`,
        });
      }
    });
  });
}

/**
 * MCP TOOLS FOR AI AGENTS:
 * 
 * When user asks to "run feza" or "release with feza", use these MCP tools
 * instead of terminal commands. They provide:
 * - Better error handling
 * - Structured output
 * - Auto-detection of project root
 * - Integration with Cursor's tool system
 * 
 * Workflow order: plan → build → github → tap
 * Remember: feza_tap needs openPr=true to create PRs!
 * 
 * ⚠️ CRITICAL WARNINGS FOR AI AGENTS:
 * 
 * 1. AUTOMATED WORKFLOW: If the project has a GitHub Actions release workflow
 *    (`.github/workflows/release.yml`), it will automatically:
 *    - Build packages in CI from the tagged commit
 *    - Compute correct checksums from actual built packages
 *    - Create GitHub release with correct assets
 *    - Generate Homebrew formula with correct checksums
 *    - Create PR automatically
 * 
 * 2. MANUAL INTERVENTION: Only use these tools manually if:
 *    - The automated workflow is not set up
 *    - You need to fix a broken release
 *    - The workflow failed and needs manual recovery
 * 
 * 3. CHECKSUM ERRORS: If you see checksum mismatches:
 *    - DO NOT use checksums from local `dist/feza_manifest.json` (from local builds)
 *    - DO use checksums from actual GitHub release assets
 *    - The workflow computes checksums correctly; manual PRs with wrong checksums cause errors
 * 
 * 4. BEST PRACTICE: For projects with automated workflows, prefer:
 *    - Creating a git tag (triggers workflow automatically)
 *    - Letting the workflow handle everything
 *    - Only manually intervene if workflow fails
 */

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
  cwd: z.string().optional().describe("Working directory (default: auto-detect project root)"),
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
    .describe("Distribution directory (default: dist). ⚠️ WARNING: Local builds create checksums that may not match CI-built packages. Prefer automated workflow if available."),
  artifactsDir: z
    .string()
    .optional()
    .describe("Artifacts directory (default: build)"),
  noAutoPython: z
    .boolean()
    .optional()
    .describe("Disable automatic Python wrapper creation"),
  cwd: z.string().optional().describe("Working directory (default: auto-detect project root)"),
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
    .describe("Distribution directory (default: dist). ⚠️ WARNING: Local manifest may have checksums that don't match CI-built packages. Prefer automated workflow if available."),
  cwd: z.string().optional().describe("Working directory (default: auto-detect project root)"),
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
    .default(true)
    .describe("Open PR after push. IMPORTANT: Set to true for automated workflows (default: true). Without this, only a branch is created."),
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
  cwd: z.string().optional().describe("Working directory (default: auto-detect project root)"),
});

// Register all tools
registerTool(
  "feza_plan",
  "[MCP TOOL - USE THIS] Plan a release: create manifest from tag and targets. Preferred over 'feza plan' CLI.",
  PlanSchema,
  (input) => {
    const args = ["plan", input.tag, "--name", input.name];
    if (input.targets) args.push("--targets", input.targets);
    return args;
  }
);

registerTool(
  "feza_build",
  "[MCP TOOL - USE THIS] Build and package binaries: compute checksums and update manifest. Preferred over 'feza build' CLI. ⚠️ WARNING: If project has automated GitHub Actions workflow, prefer creating a git tag to trigger the workflow instead. Manual builds create local manifest checksums that may not match CI-built packages.",
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
  "[MCP TOOL - USE THIS] Create or update GitHub release with assets from manifest. Preferred over 'feza github' CLI. ⚠️ WARNING: If project has automated GitHub Actions workflow, prefer creating a git tag to trigger the workflow instead. Manual releases may use local manifest checksums that don't match CI-built packages.",
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
  "[MCP TOOL - USE THIS] Render and push Homebrew formula to tap repository. Use after feza_github. IMPORTANT: openPr defaults to true for agents. ⚠️ CRITICAL WARNING: If project has automated GitHub Actions workflow, DO NOT manually create PRs using local manifest checksums. The workflow automatically creates PRs with correct checksums from CI-built packages. Only use this tool manually if: (1) workflow is not set up, (2) fixing a broken release, or (3) workflow failed. When manually fixing, use checksums from actual GitHub release assets, NOT from local dist/feza_manifest.json.",
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

// Helper to convert Zod schema to JSON Schema
function zodToJsonSchema(zodSchema: z.ZodTypeAny): any {
  // Basic implementation that handles ZodObject schemas
  const shape = zodSchema._def;
  if (shape.typeName === "ZodObject") {
    const jsonSchema: any = {
      type: "object",
      properties: {},
      required: [],
    };
    const objShape = shape.shape();
    for (const [key, value] of Object.entries(objShape)) {
      const field = value as z.ZodTypeAny;
      let fieldDef = field._def;
      let isOptional = false;
      
      // Handle ZodOptional
      if (fieldDef.typeName === "ZodOptional") {
        isOptional = true;
        fieldDef = fieldDef.innerType._def;
      }
      
      // Handle ZodDefault (which wraps ZodOptional sometimes)
      if (fieldDef.typeName === "ZodDefault") {
        isOptional = true;
        fieldDef = fieldDef.innerType._def;
        // Handle nested ZodOptional
        if (fieldDef.typeName === "ZodOptional") {
          fieldDef = fieldDef.innerType._def;
        }
      }
      
      // Extract description
      const description = fieldDef.description || (field as any).description;
      
      if (fieldDef.typeName === "ZodString") {
        jsonSchema.properties[key] = { type: "string", description };
      } else if (fieldDef.typeName === "ZodNumber") {
        jsonSchema.properties[key] = { type: "number", description };
      } else if (fieldDef.typeName === "ZodBoolean") {
        jsonSchema.properties[key] = { type: "boolean", description };
      } else {
        jsonSchema.properties[key] = { description };
      }
      
      if (!isOptional) {
        jsonSchema.required.push(key);
      }
    }
    return jsonSchema;
  }
  return { type: "object" };
}

// Register request handlers
server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: tools.map((tool) => {
      const schema = zodToJsonSchema(tool.schema);
      return {
        name: tool.name,
        description: tool.description,
        inputSchema: schema,
      };
    }),
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  const tool = tools.find((t) => t.name === name);
  if (!tool) {
    throw new McpError(
      ErrorCode.MethodNotFound,
      `Tool not found: ${name}`
    );
  }

  try {
    // Validate input with Zod schema
    const validatedInput = tool.schema.parse(args || {});
    
    // Convert to Feza CLI args
    const fezaArgs = tool.toArgs(validatedInput);
    const cwd = validatedInput.cwd;

    // Execute Feza command
    const result = await runFeza(fezaArgs, cwd);

    if (result.code !== 0) {
      return {
        content: [
          {
            type: "text",
            text: `Error: ${result.stderr || "Unknown error"}\n${result.stdout}`,
          },
        ],
        isError: true,
      };
    }

    return {
      content: [
        {
          type: "text",
          text: result.stdout || "Success",
        },
      ],
    };
  } catch (error: any) {
    if (error instanceof z.ZodError) {
      throw new McpError(
        ErrorCode.InvalidParams,
        `Invalid parameters: ${error.errors.map((e) => e.message).join(", ")}`
      );
    }
    throw new McpError(
      ErrorCode.InternalError,
      `Tool execution failed: ${error.message}`
    );
  }
});

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
