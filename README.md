# KPaper

[English](README.md) | [한국어](README.ko.md)

Read papers in Korean without losing their original structure. KPaper includes a native macOS workspace, ChatGPT/Codex subscription sign-in, and OpenAI-compatible API support.

The goal is not to summarize a paper. The goal is to keep the paper readable like a normal article page while translating the body text as literally as possible.

## Preview

Import an arXiv/ar5iv link or drop a PDF into the native macOS app.

![KPaper document import](docs/kpaper-lingopaper-v2.png)

The app keeps the full workflow in one place: document import, structure-preserving translation progress, and the final paper reader.

| Translation progress | Built-in paper reader |
| --- | --- |
| ![Structure-preserving translation progress](docs/kpaper-progress.png) | ![KPaper built-in reader](docs/kpaper-reader.png) |

The generated HTML is designed as a quiet paper reader: figures and tables stay in place, citations remain clickable, and the translated body text remains easy to read line by line.

| Korean reader | English/Korean parallel reader |
| --- | --- |
| ![Korean paper reader](docs/korean-reader.png) | ![English and Korean parallel paper reader](docs/parallel-reader.png) |

The bilingual output includes an `원본 보기` mode with English on the left and Korean on the right. Scroll sync is enabled by default, but you can turn it off, adjust either side manually, then turn it back on without moving the current view. Future scrolls continue in sync from that state.

## Features

- Uses ar5iv HTML instead of PDF parsing.
- Classifies PDFs with `pdf-inspector`, preserves native text when available, and uses Unlimited-OCR MXFP8 only for pages that need OCR.
- Masks HTML tags before calling the model, then restores the exact tags after translation.
- Sends only translatable text blocks to the model.
- Keeps `figure.ltx_table` table HTML unchanged to save tokens and avoid breaking tables.
- Preserves links, citations, figures, equations, code/pre/math blocks, and document structure.
- Adds a clean paper-viewer style: centered white page, white background, readable typography.
- Writes one HTML reader with Korean and English/Korean comparison modes.
- Provides a two-column bilingual reader with optional scroll sync.
- Writes JSONL caches so interrupted runs can resume.
- Includes a native SwiftUI macOS workspace for link/PDF import, live progress, and reading outputs.
- Supports ChatGPT/Codex subscription authentication without copying OAuth tokens into the app.
- Keeps OpenAI-compatible API key and custom base URL support as a separate provider.

## Agent Ready

This repository includes instructions for coding agents. If you are using Codex, Claude Code, or a similar agent, give it this repository URL and point it to:

- `AGENTS.md` for the full agent operating guide.
- `CLAUDE.md` for Claude Code-specific defaults.
- `skills/kpaper/SKILL.md` for reusable skill-style instructions.

## Setup

Install the local runtime first:

```bash
uv sync
```

Then choose one authentication method.

### Option A: ChatGPT / Codex Subscription

Install the Codex CLI, then sign in with ChatGPT from the app Settings screen or with:

```bash
codex login
codex login status
```

The app delegates login, credential storage, and refresh to Codex. It does not read or persist the OAuth tokens itself. Select `ChatGPT / Codex subscription` in Settings, then choose a model.

The same provider is available from the CLI:

```bash
./kpaper translate \
  --paper-id mmdocrag \
  --provider codex \
  --model gpt-6-luna
```

Codex translation defaults to `gpt-6-luna` with `low` reasoning effort; `--model` overrides the model. API translation keeps its `gpt-5.4-mini` default. Codex receives the explicit configuration `model_reasoning_effort="low"` for every batch.

### Option B: OpenAI-Compatible API

Create the local environment file:

```bash
cp .env.example .env
```

Fill `.env` locally:

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=http://host:port/v1
```

`.env`, downloaded sources, caches, and generated HTML outputs are ignored by git.

In the macOS app, select `OpenAI-compatible API` to edit the OpenAI Base URL, choose a model, or temporarily enter an API key. Leaving an override empty uses the corresponding `.env` value.

## Quick Start

For the OpenAI-compatible API provider, check the local environment:

```bash
./kpaper doctor
```

Fetch source HTML with explicit CLI flags:

```bash
./kpaper fetch \
  --paper-id mmdocrag \
  --source-url https://ar5iv.labs.arxiv.org/html/2505.16470v2
```

Agent-friendly JSON output is available on every command:

```bash
./kpaper doctor --json
```

Run a dry run to inspect block counts before calling the model:

```bash
./kpaper translate \
  --paper-id mmdocrag \
  --dry-run
```

Then run the translation. The default provider is `api`; pass `--provider codex` to use the signed-in ChatGPT/Codex subscription instead.

```bash
./kpaper translate --paper-id mmdocrag
```

The output files are:

```text
outputs/mmdocrag.ko-en.paper.html
```

`*.ko-en.paper.html` starts with a Korean-only view. Click `원본 보기` to switch to a two-column reader with English on the left and Korean on the right.

## macOS App

This repo includes a native SwiftUI workspace for local desktop use. The interface and built-in reader are native macOS components, while translation runs through the same `uv`-managed Python pipeline used by `./kpaper`.

Prepare the app runtime with:

```bash
uv sync
```

If you specifically need a standalone `.venv` for scripting outside the app, you can still run:

```bash
./scripts/bootstrap_python_env.sh
```

Build the app bundle:

```bash
./scripts/build_macos_app.sh
```

The bundle is written to:

```text
dist/KPaper.app
```

Release changes are documented in [RELEASE_NOTES.md](RELEASE_NOTES.md). The build script uses the native Swift build system, validates the bundle, and applies an ad hoc signature for local use. To choose an installed SDK explicitly, run `KPAPER_SWIFT_SDK="$(xcrun --sdk macosx --show-sdk-path)" ./scripts/build_macos_app.sh`. When the default SDK references missing Swift compiler macros, the script retries another installed SDK; an explicit override is never replaced.

In the app you can:

- Paste an arXiv/ar5iv link or drag and drop a local PDF.
- Follow import, structure analysis, translation, and viewer styling as distinct progress steps.
- Monitor preservation status for figures, tables, equations, and citations.
- Read the Korean output or switch to the English/Korean comparison view.
- Open the generated HTML or the two-column bilingual reader.
- Choose between a ChatGPT/Codex subscription and an OpenAI-compatible API.
- Select GPT-5.6 Sol, Terra, Luna, or another configured model.

The app auto-detects this repository when launched from the repo, and you can edit the project path in Settings.

### Ask the Paper

Open a paper in the native reader and select `논문에 질문`. Ask about the current paper, read the Korean Markdown answer, and select an evidence citation to jump to its paragraph. Chats are saved locally per paper. You can cancel a running question; preparation and execution errors appear in the panel.

This feature requires Codex ChatGPT sign-in and the official `openai-codex` Python SDK installed through `uv sync` with the locked dependencies. It defaults to `gpt-6-luna` with `low` reasoning effort. KPaper sends the question, chat context, parsed paper text, and up to 12 local figure/equation images to Codex. External web search is disabled. For papers exceeding 180,000 characters, the backend selects relevant excerpts and tells the model that the context is partial. Missing or unreadable parsed data produces an explicit error.

### Codex OAuth in Settings

![ChatGPT and Codex subscription sign-in](docs/kpaper-codex-oauth.png)

`ChatGPT / Codex subscription` uses the locally installed Codex runtime. `ChatGPT로 로그인` opens the Codex-managed browser login, and `상태 확인` verifies the current account without exposing tokens to KPaper. The implementation follows the managed authentication boundary documented by the [Codex app-server protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md).

## Translate Another Paper

Pass a new ar5iv URL directly to the CLI. It will derive the default input, output, cache, and bilingual output paths from `--paper-id`.

```bash
./kpaper translate \
  --paper-id your-paper \
  --source-url https://ar5iv.labs.arxiv.org/html/... \
  --dry-run
```

Remove `--dry-run` when the block count looks right.

## PDF-Only Papers

If a paper has no ar5iv HTML and only ships as a PDF, import the PDF into source HTML first. On Apple Silicon, the default layout backend uses `sahilchachra/unlimited-ocr-mxfp8-mlx` page by page to recover reading order and grounded block boxes.

```bash
uv sync
```

Agents can also add the LiteParse skill instructions with:

```bash
npx skills add run-llama/llamaparse-agent-skills --skill liteparse
```

Hugging Face `/blob/...` PDF URLs are normalized to the raw `/resolve/...` PDF URL automatically.

```bash
./kpaper pdf-import \
  --paper-id deepseek-v4 \
  --pdf-url https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf \
  --title "DeepSeek V4" \
  --json
```

This writes:

```text
inputs/pdfs/deepseek-v4.pdf
inputs/assets/deepseek-v4/page-0001.png
inputs/deepseek-v4.source.html
```

Then use the normal translation command:

```bash
./kpaper translate --paper-id deepseek-v4 --dry-run
./kpaper translate --paper-id deepseek-v4
```

The layout backend reflows text in model reading order and crops detected tables, charts, and figures from the original page between surrounding text blocks. It also keeps page screenshots under `inputs/assets/` as a source-of-truth fallback.

To select the model explicitly:

```bash
uv sync
./kpaper pdf-import \
  --paper-id scanned-paper \
  --pdf scan.pdf \
  --layout-backend unlimited-ocr-mlx \
  --layout-model sahilchachra/unlimited-ocr-mxfp8-mlx
```

The default `auto` backend uses `pdf-inspector` for page-level routing: born-digital pages use native PDF text and geometry, while scanned or broken-text pages use Unlimited-OCR. Use `--layout-backend native` or `--layout-backend unlimited-ocr-mlx` only to force one path.

## Re-apply Viewer Style or Restore Tables

If you already have translated HTML and only want to refresh the viewer CSS or restore source tables:

```bash
./kpaper restyle --paper-id mmdocrag
```

## Scripts

- `kpaper`: CLI wrapper that runs `scripts/kpaper.py` through `uv`.
- `scripts/kpaper.py`: agent-aware CLI for `doctor`, `fetch`, `translate`, `restyle`, and `serve`.
- `scripts/translate_html_blocks.py`: masks tags, translates text blocks, restores tags, writes paper-viewer HTML.
- `scripts/codex_translation_schema.json`: constrains Codex subscription translation batches to deterministic `{id, text}` JSON output.
- `scripts/apply_paper_viewer_style.py`: reapplies viewer CSS, fixes ar5iv asset links, optionally restores original table HTML.
- `scripts/bootstrap_python_env.sh`: creates `.venv` without `uv` and installs runtime Python dependencies.
- `scripts/build_macos_app.sh`: builds the SwiftUI desktop wrapper into `dist/KPaper.app`.

## Notes

Use this for documents you have the right to translate. Full translated paper outputs should stay local unless redistribution is permitted.

Only `*.ko-en.paper.html` is generated. Existing legacy `*.ko.paper.html` files remain readable; restyle uses them as input when the unified file is absent and writes only the unified reader.
