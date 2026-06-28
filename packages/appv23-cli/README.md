# appv23

Thin npm launcher for the appv23 Docker sandbox.

## Usage

Run with `npx`:

```bash
npx @htooayelwinict/appv23 --cwd .
```

Or install globally:

```bash
npm install -g @htooayelwinict/appv23
appv23 --cwd .
```

The launcher pulls and runs:

```text
ghcr.io/htooayelwinict/appv23:production
```

It mounts only the selected `--cwd` as `/workspace`, stores sandbox state in `~/.appv23/sandbox-home`, copies host `~/.agents/AGENTS.md` into the sandbox agent context, and copies host `~/.agents/skills` into the sandbox.

On startup, the package restores compact default agent files only when they are missing:

- `~/.agents/AGENTS.md`
- `~/.agents/skills/web-search/SKILL.md`
- bundled package skills such as `subagent-delegation`

Existing user files are never overwritten.

## Options

```bash
appv23 --cwd /path/to/workspace
appv23 --cwd . --dry-run
appv23 --cwd . --no-pull
appv23 --cwd . --image ghcr.io/htooayelwinict/appv23:production
```

The host `.env` file is not mounted or passed automatically. Use `/login` inside the TUI for API keys.
