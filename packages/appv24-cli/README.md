# appv24

Thin npm launcher for the appv24 Docker sandbox.

## Usage

Run with `npx`:

```bash
npx @htooayelwinict/appv24 --cwd .
```

Or install globally:

```bash
npm install -g @htooayelwinict/appv24
appv24 --cwd .
```

The launcher pulls and runs:

```text
ghcr.io/htooayelwinict/appv24:production
```

It mounts only the selected `--cwd` as `/workspace`, stores sandbox state in `~/.appv24/sandbox-home`, copies host `~/.agents/AGENTS.md` into the sandbox agent context, and copies host `~/.agents/skills` into the sandbox.

On startup, the package restores compact default agent files only when they are missing:

- `~/.agents/AGENTS.md`
- `~/.agents/skills/web-search/SKILL.md`
- bundled package skills such as `subagent-delegation`

Existing user files are never overwritten.

## Options

```bash
appv24 --cwd /path/to/workspace
appv24 --cwd . --dry-run
appv24 --cwd . --no-pull
appv24 --cwd . --image ghcr.io/htooayelwinict/appv24:production
```

The host `.env` file is not mounted or passed automatically. Use `/login` inside the TUI for API keys.
