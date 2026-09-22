# Assessments

Engineering take-homes and interview prep, one self-contained folder per company.

| Folder | Assessment | Start here |
| --- | --- | --- |
| [`interface-ai/`](interface-ai/) | **interface.ai — Computer-Use Automation System.** An LLM discovers how to operate a legacy banking UI; the run compiles into a typed capability that replays deterministically, with human takeover of the live session. | [README](interface-ai/README.md) · [REPORT](interface-ai/REPORT.md) · [evidence](interface-ai/evidence/) · [video](interface-ai/evidence/demo.mp4) · [compliance](interface-ai/COMPLIANCE.md) |
| [`upstart/`](upstart/) | **Upstart — project interview boilerplate.** A React + TypeScript page over a FastAPI + SQLite API, with the full CRUD slice already working so the interview hour goes on the exercise, not on setup. | [README](upstart/README.md) · [architecture](upstart/agent/ARCHITECTURE.md) |

## Conventions

Each folder owns its rules. `<folder>/AGENTS.md` is the authority for everything inside it — stack,
workflow, testing, commands — and there is no repository-wide rules file to reconcile it against.
Read the one for the folder you are working in.

What holds across folders is only the layout:

```
<folder>/              everything for one company: code, tests, docs, evidence, lockfile
.github/workflows/     one workflow per folder, path-filtered to that folder
.claude/skills/        reusable agent workflows
<folder>/AGENTS.md     the rules for that folder, and the first thing to read in it
```

A folder never imports from another, pins its own dependencies, and explains its own setup from a
fresh clone. Starting a new one: the [`new-assessment`](.claude/skills/new-assessment/) skill
scaffolds the folder, its CI workflow and its rules file.
