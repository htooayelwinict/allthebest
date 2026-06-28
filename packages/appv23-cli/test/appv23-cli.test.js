const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const packageRoot = path.resolve(__dirname, "..");
const packageJson = require("../package.json");
const {
  buildDockerCommand,
  buildPullCommand,
  parseArgs,
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
