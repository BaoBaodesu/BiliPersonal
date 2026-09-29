# codex-skills

Codex-compatible AI skill set for frontend and UI/UX work. Skills follow the [Anthropic Skills](https://github.com/anthropics/skills) format and are loaded lazily via `AGENTS.md`.

## Skills

| Skill | Trigger | Source |
|---|---|---|
| [`frontend-design`](./skills/frontend-design/SKILL.md) | Build pages, components, applications; aesthetic direction, typography, visual identity | Adapted from [anthropics/skills](https://github.com/anthropics/skills) |
| [`ui-ux-pro-max`](./skills/ui-ux-pro-max/SKILL.md) | Review UI quality, accessibility, interaction, animation, forms, navigation, charts | Adapted from [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) |

## Usage

### With Codex

The `AGENTS.md` file at the root is picked up automatically by Codex. No additional setup needed.

For manual skill discovery:

```bash
python3 list-skills
```

### With Claude Code

```bash
# Install via Claude Code plugin (if published)
claude mcp add-skill frontend-design
claude mcp add-skill ui-ux-pro-max

# Or manually place SKILL.md files in your project:
cp -r skills/ .claude/skills/
```

### With Cursor / Windsurf / other agents

Copy the content of any `SKILL.md` into your agent's rules file (`.cursorrules`, `.windsurfrules`, etc.).

## Structure

```
codex-skills/
├── AGENTS.md                        ← Codex agent config + skill trigger table
├── list-skills                      ← Lazy skill discovery script (Python, executable)
├── README.md
├── LICENSE.txt
└── skills/
    ├── frontend-design/
    │   └── SKILL.md                 ← Aesthetic direction, visual design, code implementation
    └── ui-ux-pro-max/
        └── SKILL.md                 ← UX rules, accessibility, interaction, animation, forms, nav
```

## How skills work together

For a full UI build:

1. **`frontend-design`** → Aesthetic direction, token system (color/type/layout), signature element → code
2. **`ui-ux-pro-max`** → Accessibility, interaction quality, animation timing, layout rules, pre-delivery checks

These skills are complementary. `frontend-design` answers "what should it look like and feel like?" while `ui-ux-pro-max` answers "is it built correctly and accessibly?"

## Skill format

Each skill follows the Anthropic Skills YAML frontmatter format:

```markdown
---
name: skill-name
description: Trigger description used for lazy discovery
---

# Skill Title
...
```

## License

- `frontend-design`: Adapted from [anthropics/skills](https://github.com/anthropics/skills). See [LICENSE.txt](./LICENSE.txt).
- `ui-ux-pro-max`: Adapted from [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) (MIT).
