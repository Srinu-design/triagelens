# Rules for coding agents (Codex, Antigravity)

Two agents build this repo at the same time, each in its own git worktree of the same repository:

| Agent | Folder | Branch |
| --- | --- | --- |
| ChatGPT Codex | `~/Documents/Projects_Github/triagelens` | `main` |
| Antigravity | `~/Documents/Projects_Github/triagelens-ag` | `ag/r<round>` |

The manager (Claude) reviews both and merges the branches between rounds. You never merge.

## Scope

- The spec is `docs/SPEC.md`; the Master Guide is `docs/MASTER_GUIDE.md`. Follow the spec literally. Where it is
  silent, pick the simplest option and leave a `# DECISION:` comment.
- Do ONLY what your prompt asks. Create or edit ONLY the files your prompt lists: the other agent owns other files
  right now, and touching them causes merge conflicts. If you need a change in a file you don't own, stop and say so
  in your report.
- Never edit `pyproject.toml`, `uv.lock`, `requirements*.txt`, `AGENTS.md` or `docs/`. Never add a dependency. If
  you need one, say so in the report.
- Copy the spec's reference code exactly (pii.py, extract_codes, decision.py, rules.py, the §3 DDL, the §7 SQL);
  do not "improve" it. `pyproject.toml` already keeps those files out of `ruff format` and E501.
- `app/seed_data.py`, `eval/labeled_set.py` and `eval/code_cases.py` already hold the §12 literals. Never edit them,
  and never tune data, cue lists or thresholds to make a number match. If a spec number doesn't reproduce, report it.
- The non-negotiables in §1 always apply.

## Section map (the spec's headings are unnumbered)

§1 Instructions for the coding agent · §2 Stack and repository layout · §3 Schema and indexes · §4 Ingestion
pipeline · §5 PII scrubber · §6 Embeddings · §7 Hybrid search SQL and ANN lab · §8 Duplicate decision and
calibration · §9 Triage classifier · §10 HTTP API · §11 Frontend · §12 Seed data and labeled set · §13 Benchmark
harness · §14 Tests and acceptance criteria · §15 Deployment · §16 Security, limits and observability · §17
Definition of done

## Environment

- Python 3.12 with uv. The venv already exists: run everything as `uv run …` (`uv run pytest`, `uv run ruff …`).
- The embedding model is already downloaded. `MODEL_DIR` in `.env` points at it; never download it into the repo.
  `HF_HUB_OFFLINE=1` keeps fastembed off the network.
- Each worktree has its own gitignored `.env` with its own ports, so both stacks can run at once:

  | Worktree | Postgres | Redis | App |
  | --- | --- | --- | --- |
  | `triagelens` (Codex) | 5434 | 6381 | 8400 |
  | `triagelens-ag` (Antigravity) | 5435 | 6382 | 8401 |

  Read ports and URLs from `.env`; never hard-code another worktree's ports. Ports 8000, 8100, 3000, 9090, 8300,
  5433 and 6380 belong to other projects (hopper, sift, splitsync): never stop or touch their containers. If
  `docker` can't see containers, run `docker context ls` and use `DOCKER_CONTEXT=default`.
- Integration tests use the database `triagelens_test` only, with the real model.

## Finish

- `uv run ruff check . && uv run ruff format --check . && uv run pytest` must pass. Never skip, xfail or delete a
  failing test to make it green.
- Commit once at the end, on the branch that is checked out, with the message your prompt gives. Never switch
  branches, merge, rebase, amend or push. No `Co-Authored-By` or other AI attribution lines.
- End with a short report: files added or changed, tests passed (count), every `# DECISION:` you made, and anything
  you could not do or could not reproduce.
