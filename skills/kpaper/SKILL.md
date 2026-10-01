# Paper Structure Translator Skill

Use this skill when a user wants to run, modify, debug, or extend the structure-preserving paper translation pipeline in this repository.

## Trigger Phrases

- "Translate this ar5iv paper into Korean HTML"
- "Make the paper viewer"
- "Fix the bilingual reader"
- "Restore tables without retranslating"
- "Import this PDF"
- "Translate this Hugging Face PDF"
- "Run the KPaper repo"
- "Use this repo to translate papers"

## Purpose

Generate faithful Korean paper-reader HTML from ar5iv HTML while preserving the paper's structure. The pipeline is for literal translation and readable viewing, not summarization.

## First Steps

1. Read `AGENTS.md`.
2. Read `README.md` or `README.ko.md` depending on the user's language.
3. Check repo state:

```bash
git status -sb
```

4. Set up dependencies:

```bash
uv sync
```

5. Confirm `.env` exists for real translation. It must contain:

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=http://host:port/v1
```

If `.env` is missing, do not invent credentials. Run `--dry-run` or ask the user for local credentials.

## Main Commands

Check readiness:

```bash
./kpaper doctor --json
```

Import an arXiv paper from any abs/pdf/html/ar5iv link or id (arxiv.org/html, then ar5iv, then PDF):

```bash
./kpaper import https://arxiv.org/abs/1706.03762 --json
```

Fetch source HTML:

```bash
./kpaper fetch \
  --paper-id mmdocrag \
  --source-url https://ar5iv.labs.arxiv.org/html/2505.16470v2 \
  --json
```

Import PDF-only sources:

```bash
./kpaper pdf-import \
  --paper-id deepseek-v4 \
  --pdf-url https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf \
  --title "DeepSeek V4" \
  --json
```

Notes:

- Prefer ar5iv HTML when available.
- `pdf-import` requires the Python `liteparse` package. If it is missing, ask for approval before installing and then run `uv add liteparse`.
- Agents may add the LiteParse skill instructions with `npx skills add run-llama/llamaparse-agent-skills --skill liteparse`.
- Hugging Face `/blob/...` PDF URLs are normalized to `/resolve/...`.
- PDF import preserves page screenshots and extracts text blocks with LiteParse; it is less structure-rich than ar5iv HTML.

One-paper dry run:

```bash
./kpaper translate \
  --paper-id mmdocrag \
  --json \
  --dry-run
```

One-paper real run:

```bash
./kpaper translate --paper-id mmdocrag
```

Style/table refresh without LLM calls:

```bash
./kpaper restyle --paper-id mmdocrag
```

## Editing Rules

- Use `./kpaper` as the primary interface.
- Use `scripts/translate_html_blocks.py` only when editing translation, masking, bilingual viewer, and CSS internals.
- Use `scripts/apply_paper_viewer_style.py` only when editing restyle internals.
- Prefer `./kpaper translate --source-url ... --paper-id ...` over editing source URL lists.
- Keep `figure.ltx_table` out of model calls.
- Preserve HTML tags with the mask/restore path.
- Keep generated HTML under `outputs/`; it is ignored by git.

## Verification

Always run:

```bash
uv run python -m py_compile scripts/*.py
```

For layout changes, verify in a browser:

```bash
./kpaper serve --port 8799
```

Open:

```text
http://127.0.0.1:8799/outputs/mmlongbench-doc.ko-en.paper.html
```

Check:

- The unified reader opens in Korean view.
- `원본 보기` shows English left and Korean right.
- Scroll sync off/on works.
- Turning scroll sync on does not jump the current view.
- Tables have borders and do not overlap text.
- Figures render.

## Before Committing

Check for secrets:

```bash
rg -n -e 'sk-[A-Za-z0-9]' -e 'OPENAI_API_KEY=.*sk-[A-Za-z0-9]' README.md README.ko.md AGENTS.md CLAUDE.md skills scripts kpaper .env.example pyproject.toml uv.lock || true
```

Do not commit:

- `.env`
- `.venv/`
- `inputs/`
- `outputs/`
- `outputs/cache/`
- full translated paper outputs unless the user explicitly requests it and redistribution is permitted

README preview screenshots under `docs/*.png` may be committed.

## Reporting Back

Report:

- Which outputs were generated or refreshed.
- Whether API calls were made.
- Which browser checks passed.
- Any missing credentials or skipped verification.

Generate only `*.ko-en.paper.html`; keep legacy Korean-only files readable without rewriting them.
