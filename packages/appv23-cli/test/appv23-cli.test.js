const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const packageRoot = path.resolve(__dirname, "..");
const packageJson = require("../package.json");
const {
  buildDockerCommand,
  buildPullCommand,
  parseArgs,
  prepareSandboxImports,
  shouldUseIsolatedDockerConfig,
} = require("../bin/appv23.js");

test("package exposes appv23 and appv23-sandbox binaries", () => {
  assert.equal(packageJson.name, "@htooayelwinict/appv23");
  assert.equal(packageJson.bin.appv23, "bin/appv23.js");
  assert.equal(packageJson.bin["appv23-sandbox"], "bin/appv23.js");
  assert.equal(fs.existsSync(path.join(packageRoot, packageJson.bin.appv23)), true);
});

test("package defaults to production GHCR image and pull", () => {
  const config = parseArgs([]);

  assert.equal(config.image, "ghcr.io/htooayelwinict/appv23:production");
  assert.equal(config.pull, true);
  assert.deepEqual(buildPullCommand(config), ["docker", "pull", "ghcr.io/htooayelwinict/appv23:production"]);
  assert.equal(shouldUseIsolatedDockerConfig(config, {}), true);
});

test("package builds hardened docker command for npx-style use", () => {
  const workspace = path.join(packageRoot, "fixtures", "workspace");
  const config = parseArgs(["--cwd", workspace, "--", "hello"]);
  const command = buildDockerCommand(config, { uid: 501, gid: 20, pid: 24680 });

  assert.deepEqual(command.slice(0, 5), ["docker", "run", "--rm", "-it", "--name"]);
  assert.ok(command.includes("--cap-drop"));
  assert.ok(command.includes("ALL"));
  assert.ok(command.includes("--security-opt"));
  assert.ok(command.includes("no-new-privileges"));
  assert.ok(command.includes("--pids-limit"));
  assert.ok(command.includes("512"));
  assert.ok(command.includes("--user"));
  assert.ok(command.includes("501:20"));
  assert.ok(command.includes(`${workspace}:/workspace:rw`));
  assert.ok(command.includes("ghcr.io/htooayelwinict/appv23:production"));
  assert.deepEqual(command.slice(-4), ["ghcr.io/htooayelwinict/appv23:production", "--cwd", "/workspace", "hello"]);
});

test("package copies bundled skills into sandbox home", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv23-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledSkill = path.join(syntheticPackageRoot, "skills", "subagent-delegation");
  const hostHome = path.join(root, "host-home");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledSkill, { recursive: true });
  fs.mkdirSync(hostHome, { recursive: true });
  fs.writeFileSync(path.join(bundledSkill, "SKILL.md"), "---\nname: subagent-delegation\n---\nBundled policy\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  assert.equal(
    fs.readFileSync(path.join(agentHome, ".agents", "skills", "subagent-delegation", "SKILL.md"), "utf8"),
    "---\nname: subagent-delegation\n---\nBundled policy\n",
  );
});

test("package user skills override bundled skills", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv23-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledSkill = path.join(syntheticPackageRoot, "skills", "subagent-delegation");
  const hostHome = path.join(root, "host-home");
  const userSkill = path.join(hostHome, ".agents", "skills", "subagent-delegation");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledSkill, { recursive: true });
  fs.mkdirSync(userSkill, { recursive: true });
  fs.writeFileSync(path.join(bundledSkill, "SKILL.md"), "---\nname: subagent-delegation\n---\nBundled policy\n");
  fs.writeFileSync(path.join(userSkill, "SKILL.md"), "---\nname: subagent-delegation\n---\nUser policy\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  assert.equal(
    fs.readFileSync(path.join(agentHome, ".agents", "skills", "subagent-delegation", "SKILL.md"), "utf8"),
    "---\nname: subagent-delegation\n---\nUser policy\n",
  );
});

test("package copies user AGENTS.md into sandbox agent context by default", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv23-cli-"));
  const hostHome = path.join(root, "host-home");
  const userAgentsDir = path.join(hostHome, ".agents");
  const agentHome = path.join(root, "agent-home");
  const syntheticPackageRoot = path.join(root, "package");
  fs.mkdirSync(userAgentsDir, { recursive: true });
  fs.mkdirSync(syntheticPackageRoot, { recursive: true });
  fs.writeFileSync(path.join(userAgentsDir, "AGENTS.md"), "Global appv23 kernel\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  const imported = fs.readFileSync(path.join(agentHome, "agent", "AGENTS.md"), "utf8");
  assert.match(imported, /appv23-sandbox-imported-agents/);
  assert.match(imported, /Global appv23 kernel/);
});
