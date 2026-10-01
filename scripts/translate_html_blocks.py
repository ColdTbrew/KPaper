from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag, NavigableString


TRANSLATE_SELECTORS = [
    "h1.ltx_title_document",
    "h2.ltx_title_section",
    "h3.ltx_title_subsection",
    "h4.ltx_title_subsubsection",
    "h5.ltx_title_paragraph",
    "h6.ltx_title_abstract",
    "p.ltx_p",
    "figcaption.ltx_caption",
]

SKIP_TAGS = {"script", "style", "pre", "code", "math", "svg"}

SYSTEM_PROMPT = """You are a professional academic paper translator.
Translate English academic paper text into Korean as literally and faithfully as possible.
The input uses placeholder tokens such as ⟦H0001⟧ instead of HTML tags. Preserve every placeholder token exactly and in the same order.
Preserve math, citations, links, figure/table numbers, model names, dataset names, metric names, URLs, filenames, and code-like strings.
Do not summarize. Do not omit details. Do not add explanations.
Return only valid JSON."""

USER_PROMPT = """Translate each masked text fragment from English to Korean.

Rules:
- Literal translation is more important than fluency.
- Preserve every placeholder token like ⟦H0001⟧ exactly and in the same order.
- Do not translate dataset/model/metric names such as MMLongBench-Doc, LongDocURL, MMDocRAG, GPT-4o, Recall@K, F1, BLEU, ROUGE-L.
- Do not translate citations, URLs, math, numbers, or code-like strings.
- Output exactly:
{{"translations":[{{"id":"b0","text":"..."}}, ...]}}

Fragments:
{items_json}
"""


PAPER_CSS = """
<style id="codex-paper-viewer-style">
html { background: #fff; }
body {
  margin: 0;
  background: #fff;
  color: #252525;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", "Helvetica Neue", Arial, sans-serif;
  font-size: 19px;
  line-height: 1.48;
}
.ltx_page_main, .ltx_page_content {
  margin: 0 auto !important;
  padding: 0 !important;
  background: transparent !important;
}
.ltx_document {
  box-sizing: border-box;
  max-width: 900px !important;
  margin: 0 auto !important;
  padding: 54px 46px 96px !important;
  background: #fff;
  min-height: 100vh;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", "Helvetica Neue", Arial, sans-serif !important;
}
.ltx_title_document {
  text-align: center;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif;
  font-size: 30px !important;
  line-height: 1.25 !important;
  font-weight: 600 !important;
  margin: 0 auto 32px !important;
  max-width: 760px;
  overflow-wrap: anywhere;
  word-break: normal;
  white-space: normal;
}
.ltx_authors {
  text-align: center;
  font-size: 16px;
  line-height: 1.45;
  margin-bottom: 58px;
}
.ltx_title_abstract, .ltx_title_section, .ltx_title_subsection, .ltx_title_subsubsection {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif;
  font-weight: 700 !important;
  letter-spacing: 0;
}
.ltx_title_abstract { font-size: 24px !important; margin: 0 0 18px !important; }
.ltx_title_section { font-size: 27px !important; margin: 54px 0 18px !important; }
.ltx_title_subsection { font-size: 22px !important; margin: 34px 0 12px !important; }
.ltx_title_subsubsection { font-size: 20px !important; margin: 28px 0 12px !important; }
.codex_pdf_runin_title { font-weight: 700; }
.ltx_p {
  margin: 0 0 1.05em !important;
  text-align: justify;
  word-break: keep-all;
  overflow-wrap: break-word;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", "Helvetica Neue", Arial, sans-serif !important;
}
.ltx_abstract {
  margin: 0 0 42px !important;
}
.ltx_figure, .ltx_table {
  margin: 42px auto !important;
  max-width: 100%;
  overflow: visible;
  clear: both;
  display: block;
}
.ltx_table {
  display: flow-root !important;
  width: 100% !important;
  text-align: center !important;
  page-break-inside: avoid;
  break-inside: avoid;
}
.ltx_table .ltx_transformed_outer {
  display: block !important;
  width: 100% !important;
  height: auto !important;
  margin-left: auto !important;
  margin-right: auto !important;
  text-align: center !important;
  vertical-align: baseline !important;
}
.ltx_table .ltx_transformed_inner {
  display: block !important;
  width: 100% !important;
  margin-left: auto !important;
  margin-right: auto !important;
  transform: none !important;
}
.ltx_figure:has(.ltx_transformed_outer table),
.ltx_figure_panel:has(.ltx_transformed_outer table),
.ltx_minipage:has(.ltx_transformed_outer table) {
  display: block !important;
  width: 100% !important;
  max-width: 100% !important;
  margin-left: auto !important;
  margin-right: auto !important;
  text-align: center !important;
}
.ltx_transformed_outer:has(table) {
  display: block !important;
  width: 100% !important;
  height: auto !important;
  margin-left: auto !important;
  margin-right: auto !important;
  text-align: center !important;
  vertical-align: baseline !important;
}
.ltx_transformed_outer:has(table) > .ltx_transformed_inner {
  display: block !important;
  width: 100% !important;
  margin-left: auto !important;
  margin-right: auto !important;
  transform: none !important;
}
.ltx_figure img, .ltx_graphics {
  display: block;
  max-width: 100% !important;
  height: auto !important;
  margin: 0 auto;
}
.ltx_picture {
  display: block !important;
  max-width: 100% !important;
  height: auto !important;
  margin: 24px auto !important;
  overflow: hidden !important;
}
svg.ltx_picture {
  font-family: Arial, sans-serif !important;
  font-size: 6px !important;
  line-height: 1.08 !important;
}
svg.ltx_picture .ltx_p {
  display: block;
  margin: 0 0 2px !important;
  text-align: left !important;
  word-break: normal !important;
  overflow-wrap: normal !important;
  font-family: inherit !important;
  font-size: inherit !important;
  line-height: inherit !important;
}
svg.ltx_picture .ltx_text,
svg.ltx_picture .ltx_inline-block {
  font-family: inherit !important;
  font-size: inherit !important;
  line-height: inherit !important;
}
svg.ltx_picture .ltx_inline-block {
  max-width: 100%;
}
.ltx_caption {
  margin: 12px auto 0 !important;
  font-size: 16px !important;
  line-height: 1.45 !important;
  text-align: left !important;
  max-width: 820px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", "Helvetica Neue", Arial, sans-serif !important;
}
table, .ltx_tabular {
  border-collapse: collapse !important;
  margin: 0 auto !important;
  width: 100% !important;
  max-width: 100% !important;
  table-layout: fixed !important;
  font-size: clamp(7px, 0.82vw, 11px) !important;
  line-height: 1.12 !important;
  border-top: 1.5px solid #333 !important;
  border-bottom: 1.5px solid #333 !important;
}
td, th, .ltx_td, .ltx_th {
  padding: 2px 3px !important;
  vertical-align: middle;
  border: 1px solid #8e8e8e !important;
  white-space: normal !important;
  overflow-wrap: anywhere;
  word-break: normal;
}
.ltx_table + *,
.ltx_figure + * {
  clear: both;
  margin-top: 24px !important;
}
thead td, thead th, .ltx_tr:first-child > .ltx_td, .ltx_tr:first-child > .ltx_th {
  border-bottom: 1.5px solid #333 !important;
}
.ltx_tabular .ltx_border_t,
.ltx_tabular .ltx_border_b,
.ltx_tabular .ltx_border_l,
.ltx_tabular .ltx_border_r {
  border-color: #333 !important;
}
.ltx_page_footer, .ltx_page_header, .ltx_page_logo {
  display: none !important;
}
a { color: #174ea6; text-decoration-thickness: 1px; text-underline-offset: 2px; }
.codex_references {
  margin: 36px 0 24px;
  border: 1px solid #ddd;
  border-radius: 10px;
}
.codex_references > summary {
  cursor: pointer;
  padding: 14px 18px;
  font-weight: 600;
}
.codex_references > summary:focus-visible {
  outline: 2px solid #174ea6;
  outline-offset: 3px;
}
.codex_references_content { padding: 8px 18px 18px; }
@media (max-width: 760px) {
  body { font-size: 17px; }
  .ltx_document { padding: 32px 18px 72px !important; }
  .ltx_title_document { font-size: 25px !important; }
}
</style>
"""


BILINGUAL_CSS = """
<style id="codex-bilingual-viewer-style">
body.has_bilingual_view .codex_tabs {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  justify-content: center;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(255,255,255,0.96);
  border-bottom: 1px solid #e5e5e5;
}
body.has_bilingual_view .codex_tab_button {
  appearance: none;
  border: 1px solid #cfcfcf;
  background: #fff;
  color: #222;
  border-radius: 6px;
  padding: 7px 14px;
  font: 600 14px -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif;
  cursor: pointer;
}
body.has_bilingual_view .codex_tab_button[aria-selected="true"] {
  border-color: #222;
  background: #222;
  color: #fff;
}
body.has_bilingual_view .codex_sync_button {
  appearance: none;
  border: 1px solid #cfcfcf;
  background: #fff;
  color: #222;
  border-radius: 6px;
  padding: 7px 12px;
  font: 600 13px -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif;
  cursor: pointer;
}
body.has_bilingual_view .codex_sync_button.is_off {
  color: #666;
}
body.has_bilingual_view .codex_sync_button[hidden] {
  display: none !important;
}
body.has_bilingual_view .codex_panel[hidden] {
  display: none !important;
}
body.has_bilingual_view .codex_parallel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0;
  background: #fff;
  height: calc(100vh - 57px);
  overflow: hidden;
}
body.has_bilingual_view .codex_parallel_column {
  min-width: 0;
  height: 100%;
  overflow-y: auto;
  overscroll-behavior: contain;
  overflow-anchor: none;
  scroll-behavior: auto;
  border-left: 1px solid #eee;
}
body.has_bilingual_view .codex_parallel_column:first-child {
  border-left: 0;
}
body.has_bilingual_view .codex_parallel_label {
  position: sticky;
  top: 0;
  z-index: 10;
  padding: 6px 12px;
  background: rgba(255,255,255,0.96);
  border-bottom: 1px solid #e7e7e7;
  font: 600 13px -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif;
  color: #555;
  text-align: center;
}
body.has_bilingual_view .codex_alignment_spacer {
  display: block !important;
  width: 100% !important;
  margin: 0 !important;
  padding: 0 !important;
  border: 0 !important;
  visibility: hidden !important;
  pointer-events: none !important;
}
body.has_bilingual_view .codex_parallel .ltx_document {
  max-width: 760px !important;
  padding-left: 34px !important;
  padding-right: 34px !important;
}
@media (max-width: 1100px) {
  body.has_bilingual_view .codex_parallel {
    grid-template-columns: 1fr;
    height: auto;
    overflow: visible;
  }
  body.has_bilingual_view .codex_parallel_column {
    height: auto;
    overflow: visible;
    border-left: 0;
    border-top: 1px solid #eee;
  }
  body.has_bilingual_view .codex_parallel_column:first-child {
    border-top: 0;
  }
}
</style>
"""


BILINGUAL_SCRIPT = """
<script id="codex-bilingual-viewer-script">
document.addEventListener("DOMContentLoaded", function () {
  const buttons = Array.from(document.querySelectorAll(".codex_tab_button"));
  const panels = Array.from(document.querySelectorAll(".codex_panel"));
  const syncButton = document.querySelector(".codex_sync_button");
  const columns = Array.from(document.querySelectorAll(".codex_parallel_column"));
  const tabs = document.querySelector(".codex_tabs");
  let syncEnabled = true;
  let isSyncing = false;
  let syncGuardVersion = 0;
  let lastScrolledColumn = null;
  let alignmentTimer = null;
  let scrollFrame = null;
  let pendingSource = null;
  let scrollMap = [];
  const expectedScrolls = new WeakMap();
  const scrollPositions = new Map();
  const stableViewSnapshots = new Map();
  let layoutAlignmentPending = false;
  const alignmentAnchorSelector = [
    ".ltx_para[id]",
    "figure[id]",
    "h1[id], h2[id], h3[id], h4[id], h5[id], h6[id]",
    "section.ltx_section[id]",
    "section.ltx_subsection[id]",
    "section.ltx_subsubsection[id]"
  ].join(",");
  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(value);
    }
    return String(value).replace(/["\\\\]/g, "\\\\$&");
  }
  function captureScrollPositions() {
    columns.forEach((column) => {
      scrollPositions.set(column, column.scrollTop);
      if (!layoutAlignmentPending && isParallelActive()) {
        stableViewSnapshots.set(column, snapshotFromContainer(column, column));
      }
    });
  }
  function clampScrollTop(element, value) {
    const max = element.scrollHeight - element.clientHeight;
    return Math.max(0, Math.min(max, value));
  }
  function isParallelActive() {
    const panel = document.getElementById("codex-panel-parallel");
    return Boolean(panel && !panel.hidden && columns.length >= 2);
  }
  function removeAlignmentSpacers() {
    document.querySelectorAll(".codex_alignment_spacer").forEach((spacer) => spacer.remove());
  }
  function collectAlignmentAnchors(column) {
    const anchors = new Map();
    const article = column ? column.querySelector("article.ltx_document") : null;
    if (!article) return anchors;
    Array.from(article.querySelectorAll(alignmentAnchorSelector)).forEach((element) => {
      if (element.id && !String(element.id).startsWith("codex-")) {
        // Closed references and nested heading IDs must not become duplicate
        // or invisible alignment points.
        if (!element.getClientRects().length || element.getBoundingClientRect().height === 0) return;
        if (element.matches("section") && element.querySelector(alignmentAnchorSelector)) return;
        if (element.matches("h1,h2,h3,h4,h5,h6") && element.closest(".ltx_para[id]")) return;
        const title = Array.from(element.children).find((child) =>
          child.classList && child.classList.contains("ltx_title")
        );
        anchors.set(element.id, {
          measure: title || element,
          insertBefore: element
        });
      }
    });
    return anchors;
  }
  function scrollContentTop(element, column) {
    return element.getBoundingClientRect().top + column.scrollTop - column.getBoundingClientRect().top;
  }
  function insertAlignmentSpacerBefore(element, height) {
    if (!element || !element.parentNode || height < 2) return;
    const spacer = document.createElement("div");
    spacer.className = "codex_alignment_spacer";
    spacer.setAttribute("aria-hidden", "true");
    spacer.style.height = `${Math.ceil(height)}px`;
    element.parentNode.insertBefore(spacer, element);
  }
  function releaseSyncGuard() {
    const version = ++syncGuardVersion;
    window.setTimeout(() => {
      if (version !== syncGuardVersion) return;
      captureScrollPositions();
      isSyncing = false;
    }, 120);
  }
  function performNavigation(operation) {
    if (alignmentTimer) window.clearTimeout(alignmentTimer);
    alignmentTimer = null;
    layoutAlignmentPending = false;
    ++syncGuardVersion;
    isSyncing = true;
    if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame);
    scrollFrame = null;
    pendingSource = null;
    columns.forEach((column) => expectedScrolls.delete(column));
    try {
      operation();
    } finally {
      captureScrollPositions();
      rebuildScrollMap();
      releaseSyncGuard();
    }
  }
  // Hosts use this transaction for atomic movement of both panes. The normal
  // user-scroll handler must not reinterpret a heading jump as scroll deltas.
  window.kpaperViewer = { performNavigation };
  function alignParallelColumns() {
    if (!isParallelActive()) return;
    removeAlignmentSpacers();
    const [leftColumn, rightColumn] = columns;
    const leftAnchors = collectAlignmentAnchors(leftColumn);
    const rightAnchors = collectAlignmentAnchors(rightColumn);
    const ids = Array.from(leftAnchors.keys()).filter((id) => rightAnchors.has(id));
    for (let pass = 0; pass < 3; pass += 1) {
      let changed = false;
      ids.forEach((id) => {
        const left = leftAnchors.get(id);
        const right = rightAnchors.get(id);
        if (!left || !right) return;
        const leftTop = scrollContentTop(left.measure, leftColumn);
        const rightTop = scrollContentTop(right.measure, rightColumn);
        const delta = Math.round(rightTop - leftTop);
        if (Math.abs(delta) < 2) return;
        changed = true;
        if (delta > 0) {
          insertAlignmentSpacerBefore(left.insertBefore, delta);
        } else {
          insertAlignmentSpacerBefore(right.insertBefore, -delta);
        }
      });
      if (!changed) break;
    }
    captureScrollPositions();
    rebuildScrollMap();
  }
  function rebuildScrollMap() {
    if (!isParallelActive()) return;
    const [leftColumn, rightColumn] = columns;
    const left = collectAlignmentAnchors(leftColumn);
    const right = collectAlignmentAnchors(rightColumn);
    const leftMax = Math.max(0, leftColumn.scrollHeight - leftColumn.clientHeight);
    const rightMax = Math.max(0, rightColumn.scrollHeight - rightColumn.clientHeight);
    scrollMap = [[0, 0]];
    left.forEach((anchor, id) => {
      if (!right.has(id)) return;
      const pair = [
        clampScrollTop(leftColumn, scrollContentTop(anchor.measure, leftColumn) - 46),
        clampScrollTop(rightColumn, scrollContentTop(right.get(id).measure, rightColumn) - 46)
      ];
      const previous = scrollMap[scrollMap.length - 1];
      if (pair[0] > previous[0] && pair[1] > previous[1]) scrollMap.push(pair);
    });
    const last = scrollMap[scrollMap.length - 1];
    if (leftMax > last[0] && rightMax > last[1]) scrollMap.push([leftMax, rightMax]);
  }
  function mappedScrollTop(source, value) {
    const from = source === columns[0] ? 0 : 1;
    const to = 1 - from;
    if (scrollMap.length < 2) return value;
    for (let index = 1; index < scrollMap.length; index += 1) {
      const before = scrollMap[index - 1];
      const after = scrollMap[index];
      if (value <= after[from] || index === scrollMap.length - 1) {
        const progress = Math.max(0, Math.min(1, (value - before[from]) / (after[from] - before[from])));
        return before[to] + progress * (after[to] - before[to]);
      }
    }
    return value;
  }
  function restoreParallelSnapshot(snapshot) {
    if (!snapshot) return;
    const escapedId = snapshot.id ? cssEscape(snapshot.id) : "";
    const targetPairs = escapedId
      ? columns
          .map((column) => ({ column, target: column.querySelector(`#${escapedId}`) }))
          .filter((item) => item.target)
      : [];
    if (targetPairs.length) {
      isSyncing = true;
      targetPairs.forEach((item) => {
        const nextTop = scrollContentTop(item.target, item.column) - 46 - snapshot.offset;
        item.column.scrollTop = clampScrollTop(item.column, nextTop);
      });
      releaseSyncGuard();
      return;
    }
    isSyncing = true;
    const maxScroll = Math.min(
      ...columns.map((column) => Math.max(0, column.scrollHeight - column.clientHeight))
    );
    const nextTop = Math.max(0, maxScroll * snapshot.ratio);
    columns.forEach((column) => {
      column.scrollTop = clampScrollTop(column, nextTop);
    });
    releaseSyncGuard();
  }
  function scheduleParallelAlignment(preservePosition = true) {
    if (!isParallelActive()) return;
    if (alignmentTimer) window.clearTimeout(alignmentTimer);
    // resize fires after reflow. Re-measuring here would save the newly shifted
    // content rather than the paragraph the reader was looking at beforehand.
    const snapshots = preservePosition
      ? columns.map((column) => stableViewSnapshots.get(column) || snapshotFromContainer(column, column)) : null;
    layoutAlignmentPending = true;
    ++syncGuardVersion;
    isSyncing = true;
    if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame);
    scrollFrame = null;
    pendingSource = null;
    columns.forEach((column) => expectedScrolls.delete(column));
    alignmentTimer = window.setTimeout(() => {
      alignmentTimer = null;
      alignParallelColumns();
      if (snapshots) columns.forEach((column, index) => restoreColumnSnapshot(column, snapshots[index]));
      layoutAlignmentPending = false;
      captureScrollPositions();
      releaseSyncGuard();
    }, 80);
  }
  function syncFrom(source) {
    if (!source || !syncEnabled || isSyncing || columns.length < 2) return;
    const previousTop = scrollPositions.get(source) ?? source.scrollTop;
    const delta = mappedScrollTop(source, source.scrollTop) - mappedScrollTop(source, previousTop);
    if (!delta) {
      captureScrollPositions();
      return;
    }
    columns.forEach((column) => {
      if (column !== source) {
        const nextTop = clampScrollTop(column, column.scrollTop + delta);
        expectedScrolls.set(column, nextTop);
        column.scrollTop = nextTop;
      }
    });
    captureScrollPositions();
  }
  function viewportTop() {
    return tabs ? tabs.getBoundingClientRect().bottom : 0;
  }
  function snapshotFromContainer(container, scrollElement) {
    if (!container) return null;
    const top = scrollElement ? scrollElement.getBoundingClientRect().top + 46 : viewportTop();
    let candidates = Array.from(collectAlignmentAnchors(scrollElement || {
      querySelector: () => container.querySelector("article.ltx_document")
    }).values()).map((anchor) => anchor.insertBefore);
    if (!candidates.length) {
      candidates = Array.from(container.querySelectorAll("[id]")).filter(
        (element) => !String(element.id).startsWith("codex-")
      );
    }
    let best = null;
    for (const element of candidates) {
      const rect = element.getBoundingClientRect();
      if (rect.bottom < top) continue;
      const distance = Math.abs(rect.top - top);
      if (!best || distance < best.distance) {
        best = { id: element.id, offset: rect.top - top, distance };
      }
      if (rect.top >= top) break;
    }
    const maxScroll = scrollElement
      ? scrollElement.scrollHeight - scrollElement.clientHeight
      : document.documentElement.scrollHeight - window.innerHeight;
    const currentScroll = scrollElement ? scrollElement.scrollTop : window.scrollY;
    return {
      id: best ? best.id : "",
      offset: best ? best.offset : 0,
      ratio: maxScroll > 0 ? currentScroll / maxScroll : 0,
    };
  }
  function captureViewSnapshot() {
    const active = panels.find((panel) => !panel.hidden);
    if (!active) return null;
    if (active.id === "codex-panel-parallel") {
      const column = lastScrolledColumn || columns[1] || columns[0];
      return snapshotFromContainer(column, column);
    }
    return snapshotFromContainer(active, null);
  }
  function restoreWindowSnapshot(snapshot) {
    if (!snapshot) return;
    const escapedId = snapshot.id ? cssEscape(snapshot.id) : "";
    const target = escapedId ? document.querySelector(`#codex-panel-ko #${escapedId}`) : null;
    if (target) {
      const nextY = window.scrollY + target.getBoundingClientRect().top - viewportTop() - snapshot.offset;
      window.scrollTo(0, Math.max(0, nextY));
      return;
    }
    const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
    window.scrollTo(0, Math.max(0, maxScroll * snapshot.ratio));
  }
  function restoreColumnSnapshot(column, snapshot) {
    if (!column || !snapshot) return;
    const escapedId = snapshot.id ? cssEscape(snapshot.id) : "";
    const target = escapedId ? column.querySelector(`#${escapedId}`) : null;
    if (target) {
      const columnTop = column.getBoundingClientRect().top + 46;
      column.scrollTop = clampScrollTop(
        column,
        column.scrollTop + target.getBoundingClientRect().top - columnTop - snapshot.offset
      );
      return;
    }
    column.scrollTop = clampScrollTop(
      column,
      (column.scrollHeight - column.clientHeight) * snapshot.ratio
    );
  }
  function restoreViewSnapshot(target, snapshot) {
    if (!snapshot) return;
    if (target === "codex-panel-parallel") {
      restoreParallelSnapshot(snapshot);
      return;
    }
    restoreWindowSnapshot(snapshot);
  }
  function setSyncEnabled(enabled) {
    if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame);
    scrollFrame = null;
    pendingSource = null;
    syncEnabled = enabled;
    if (!syncButton) return;
    syncButton.textContent = enabled ? "스크롤 동기화 끄기" : "스크롤 동기화 켜기";
    syncButton.classList.toggle("is_off", !enabled);
    syncButton.setAttribute("aria-pressed", enabled ? "true" : "false");
    captureScrollPositions();
    rebuildScrollMap();
  }
  function activate(target, preservePosition = true) {
    const snapshot = preservePosition ? captureViewSnapshot() : null;
    buttons.forEach((button) => {
      const selected = button.dataset.target === target;
      button.setAttribute("aria-selected", selected ? "true" : "false");
    });
    panels.forEach((panel) => {
      panel.hidden = panel.id !== target;
    });
    if (syncButton) {
      syncButton.hidden = target !== "codex-panel-parallel";
    }
    if (preservePosition) {
      window.setTimeout(() => {
        if (target === "codex-panel-parallel") {
          alignParallelColumns();
        }
        restoreViewSnapshot(target, snapshot);
      }, 0);
    } else if (target === "codex-panel-parallel") {
      window.setTimeout(() => alignParallelColumns(), 0);
    }
  }
  buttons.forEach((button) => {
    button.addEventListener("click", () => activate(button.dataset.target));
  });
  columns.forEach((column) => {
    ["wheel", "touchstart", "pointerdown", "keydown"].forEach((eventName) => {
      column.addEventListener(eventName, () => expectedScrolls.delete(column), { passive: true });
    });
    column.addEventListener("scroll", () => {
      if (isSyncing) return;
      if (expectedScrolls.has(column)) {
        const expected = expectedScrolls.get(column);
        expectedScrolls.delete(column);
        if (Math.abs(column.scrollTop - expected) < 1) return;
      }
      lastScrolledColumn = column;
      if (!syncEnabled || !isParallelActive()) {
        captureScrollPositions();
        return;
      }
      pendingSource = column;
      if (scrollFrame !== null) return;
      scrollFrame = window.requestAnimationFrame(() => {
        scrollFrame = null;
        const source = pendingSource;
        pendingSource = null;
        syncFrom(source);
      });
    }, { passive: true });
  });
  columns.forEach((column) => {
    column.querySelectorAll("details").forEach((details) => {
      details.addEventListener("toggle", () => scheduleParallelAlignment(true));
    });
  });
  if (syncButton) {
    syncButton.addEventListener("click", () => setSyncEnabled(!syncEnabled));
    setSyncEnabled(true);
  }
  columns.forEach((column) => {
    column.querySelectorAll("img").forEach((image) => {
      if (!image.complete) {
        image.addEventListener("load", () => scheduleParallelAlignment(true), { once: true });
        image.addEventListener("error", () => scheduleParallelAlignment(true), { once: true });
      }
    });
  });
  window.addEventListener("resize", () => scheduleParallelAlignment(true));
  activate("codex-panel-ko", false);
});
</script>
"""


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def block_hash(fragment: str) -> str:
    return hashlib.sha256(fragment.encode("utf-8")).hexdigest()


def load_cache(path: Path) -> dict[str, str]:
    cache: dict[str, str] = {}
    if not path.exists():
        return cache
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if "ko_masked" in obj:
            cache.setdefault(obj["hash"], obj["ko_masked"])
    return cache


def append_cache(path: Path, source_masked: str, ko_masked: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"hash": block_hash(source_masked), "source_masked": source_masked, "ko_masked": ko_masked},
                ensure_ascii=False,
            )
            + "\n"
        )


def should_translate_tag(tag: Tag) -> bool:
    if any(parent.name in SKIP_TAGS for parent in tag.parents if isinstance(parent, Tag)):
        return False
    if "ltx_table" in (tag.get("class") or []) or tag.find_parent("figure", class_="ltx_table"):
        return False
    text = tag.get_text(" ", strip=True)
    if len(text) < 2:
        return False
    if re.fullmatch(r"[\W\d_]+", text):
        return False
    return True


REFERENCE_SELECTOR = '.ltx_bibliography, .ltx_biblist, [role="doc-bibliography"]'
REFERENCE_HEADING = re.compile(r"^(?:\d+[.\s]+)?(?:references|bibliography|참고\s*문헌)\s*[:.]?$", re.IGNORECASE)


def reference_blocks(soup: BeautifulSoup) -> list[Tag]:
    """Find semantic bibliographies and flattened PDF references across pages."""
    semantic_ids = {
        id(tag)
        for region in soup.select(REFERENCE_SELECTOR)
        for tag in [region, *region.find_all(True)]
    }
    result: list[Tag] = []
    in_references = False
    for tag in soup.select(", ".join(TRANSLATE_SELECTORS)):
        text = tag.get_text(" ", strip=True)
        if REFERENCE_HEADING.fullmatch(text):
            in_references = True
        elif in_references and (
            tag.name in {"h1", "h2", "h3", "h4", "h5", "h6"}
            or re.match(r"^(?:appendix|appendices|supplementary material|부록)(?:\s|[.:]|$)", text, re.IGNORECASE)
        ):
            in_references = False
        if in_references or id(tag) in semantic_ids:
            result.append(tag)
    return result


def restore_source_references(translated: BeautifulSoup, source: BeautifulSoup) -> int:
    restored = 0
    seen: set[str] = set()
    for tag in [*source.select(REFERENCE_SELECTOR), *reference_blocks(source)]:
        # PDF paragraphs carry their stable ID on the enclosing div; ar5iv
        # bibliography items commonly carry it on the enclosing list item.
        container = tag if tag.get("id") else tag.find_parent(["div", "li"], id=True)
        if container is None:
            continue
        element_id = container.get("id")
        if element_id in seen:
            continue
        seen.add(element_id)
        target = translated.find(id=element_id)
        if target:
            replacement = BeautifulSoup(str(container), "lxml").find(container.name)
            target.replace_with(replacement)
            restored += 1
    return restored


def collapse_references(soup: BeautifulSoup) -> None:
    """Keep one accessible, initially closed bibliography, including PDF page continuations."""
    if soup.select_one("details.codex_references"):
        return
    candidates = list(soup.select(REFERENCE_SELECTOR))
    for tag in reference_blocks(soup):
        container = tag if tag.get("id") else tag.find_parent(["div", "li"], id=True)
        candidates.append(container if container is not None else tag)
    candidate_ids = {id(tag) for tag in candidates}
    roots: list[Tag] = []
    seen: set[int] = set()
    for tag in candidates:
        if id(tag) in seen or any(id(parent) in candidate_ids for parent in tag.parents):
            continue
        seen.add(id(tag))
        roots.append(tag)
    if not roots:
        return
    details = soup.new_tag("details")
    details["class"] = "codex_references"
    summary = soup.new_tag("summary")
    summary.string = "참고문헌 (References)"
    details.append(summary)
    content = soup.new_tag("div")
    content["class"] = "codex_references_content"
    details.append(content)
    roots[0].insert_before(details)
    for tag in roots:
        content.append(tag.extract())
    # PDF references can fill several page sections. Remove only sections left
    # completely empty by moving their content into the accordion.
    for section in soup.select("section.codex_pdf_page"):
        if not section.find(True) and not section.get_text(strip=True):
            section.decompose()


def collect_blocks(soup: BeautifulSoup) -> list[Tag]:
    blocks: list[Tag] = []
    seen: set[int] = set()
    excluded = {id(tag) for tag in reference_blocks(soup)}
    for selector in TRANSLATE_SELECTORS:
        for tag in soup.select(selector):
            if id(tag) in seen or id(tag) in excluded or not should_translate_tag(tag):
                continue
            seen.add(id(tag))
            blocks.append(tag)
    blocks.sort(key=lambda t: getattr(t, "sourceline", 0) or 0)
    return blocks


TAG_PATTERN = re.compile(r"<[^>]+>")
PLACEHOLDER_PATTERN = re.compile(r"⟦H\d{4}⟧")


def mask_html(fragment: str) -> tuple[str, list[str]]:
    tags: list[str] = []

    def repl(match: re.Match[str]) -> str:
        tags.append(match.group(0))
        return f"⟦H{len(tags) - 1:04d}⟧"

    return TAG_PATTERN.sub(repl, fragment), tags


def unmask_html(masked: str, tags: list[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        idx = int(match.group(0)[2:6])
        return tags[idx] if idx < len(tags) else match.group(0)

    return PLACEHOLDER_PATTERN.sub(repl, masked)


def make_batches(items: list[tuple[str, str]], max_chars: int) -> list[list[tuple[str, str]]]:
    batches: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    chars = 0
    for item in items:
        size = len(item[1])
        if current and chars + size > max_chars:
            batches.append(current)
            current = []
            chars = 0
        current.append(item)
        chars += size
    if current:
        batches.append(current)
    return batches


def extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def call_api(
    base_url: str,
    api_key: str,
    model: str,
    batch: list[tuple[str, str]],
    timeout: int,
    retries: int,
) -> tuple[dict[str, str], int, int]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_PROMPT.format(
                    items_json=json.dumps([{"id": bid, "text": text} for bid, text in batch], ensure_ascii=False)
                ),
            },
        ],
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = extract_json(content)
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            translations = {str(item["id"]): str(item["text"]) for item in parsed["translations"]}
            return translations, input_tokens, output_tokens
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt >= retries:
                break
            time.sleep(2**attempt)
    raise RuntimeError(f"request failed: {last}") from last


def translation_model(provider: str, requested: str | None = None) -> str:
    return requested or ("gpt-6-luna" if provider == "codex" else "gpt-5.4-mini")


def call_codex(
    model: str,
    batch: list[tuple[str, str]],
    timeout: int,
    retries: int,
) -> tuple[dict[str, str], int, int]:
    codex = os.environ.get("CODEX_EXECUTABLE") or shutil.which("codex")
    if not codex:
        raise RuntimeError("codex CLI not found; install Codex and sign in with ChatGPT first")

    schema_path = Path(__file__).with_name("codex_translation_schema.json")
    prompt = "\n\n".join(
        (
            SYSTEM_PROMPT,
            USER_PROMPT.format(
                items_json=json.dumps([{"id": bid, "text": text} for bid, text in batch], ensure_ascii=False)
            ),
            "Return only data that matches the supplied JSON schema. Do not use tools or modify files.",
        )
    )
    last: Exception | None = None
    for attempt in range(retries + 1):
        output_path = Path(tempfile.gettempdir()) / f"kpaper-codex-{os.getpid()}-{time.time_ns()}.json"
        command = [
            codex,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--ignore-user-config",
            "--ignore-rules",
            "--color",
            "never",
            "--model",
            model,
            "-c",
            'model_reasoning_effort="low"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-",
        ]
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                cwd=tempfile.gettempdir(),
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
                raise RuntimeError(detail)
            parsed = extract_json(output_path.read_text(encoding="utf-8"))
            translations = {str(item["id"]): str(item["text"]) for item in parsed["translations"]}
            return translations, 0, 0
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt >= retries:
                break
            time.sleep(2**attempt)
        finally:
            output_path.unlink(missing_ok=True)
    raise RuntimeError(f"Codex request failed: {last}") from last


def log_factory(path: Path | None):
    def log(message: str) -> None:
        stamped = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        print(stamped, flush=True)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(stamped + "\n")

    return log


def format_pdf_titles(soup: BeautifulSoup, source: BeautifulSoup | None = None) -> None:
    """Recover headings flattened by native PDF text extraction, using source labels."""
    excluded = {id(tag) for tag in reference_blocks(soup)}
    for tag in soup.select(".codex_pdf_document .codex_pdf_layout_text > p.ltx_p, .codex_pdf_document h2.ltx_title_section"):
        if id(tag) in excluded:
            continue
        original = tag
        if source is not None:
            stable_id = tag.get("id") or (tag.parent.get("id") if tag.parent else None)
            match = source.find(id=stable_id) if stable_id else None
            if match is None and stable_id and stable_id.endswith("-heading"):
                match = source.find(id=stable_id.removesuffix("-heading"))
            if match:
                original = match.select_one("p.ltx_p, h2, h3, h4") or match
        source_text = original.get_text(" ", strip=True)
        numbered = re.match(r"^(\d+\.(?:\d+\.?)*?)\s+\S", source_text)
        if numbered and len(source_text) <= 160 and not source_text.endswith((".", "!", "?")):
            level = min(4, 1 + len(numbered.group(1).rstrip(".").split(".")))
            tag.name = f"h{level}"
            tag["class"] = ["ltx_title", {2: "ltx_title_section", 3: "ltx_title_subsection", 4: "ltx_title_subsubsection"}[level]]
            if not tag.get("id") and tag.parent.get("id"):
                tag["id"] = tag.parent["id"] + "-heading"
            continue
        if tag.name == "h2" and str(tag.get("id", "")).endswith("-heading") and original.name == "p":
            tag.name = "p"
            tag["class"] = ["ltx_p"]
            del tag["id"]
        # Short Title Case labels ending in a period are PDF run-in headings.
        # Inspect English source so Korean translations need no guessed label list.
        prefix = re.split(r"\.(?:\s+|(?=[A-Z]))", source_text, maxsplit=1)
        if len(prefix) != 2 or len(prefix[0]) > 80:
            continue
        words = prefix[0].split()
        if not 2 <= len(words) <= 8 or not all(
            re.fullmatch(r"[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", word)
            or word in {"and", "of", "in", "for", "to", "with", "on"}
            for word in words
        ):
            continue
        if not tag.contents or not isinstance(tag.contents[0], NavigableString):
            continue
        first = tag.contents[0]
        translated_prefix = re.match(r"^(.{1,90}?\.)\s+", str(first))
        if translated_prefix:
            strong = soup.new_tag("strong")
            strong["class"] = "codex_pdf_runin_title"
            strong.string = translated_prefix.group(1)
            first.replace_with(strong)
            strong.insert_after(NavigableString(str(first)[len(translated_prefix.group(1)):]))


def inject_style(soup: BeautifulSoup) -> None:
    format_pdf_titles(soup)
    collapse_references(soup)
    existing = soup.find(id="codex-paper-viewer-style")
    if existing:
        existing.decompose()
    head = soup.head or soup.new_tag("head")
    if not soup.head:
        soup.html.insert(0, head)
    style_tag = BeautifulSoup(PAPER_CSS, "lxml").find("style")
    if style_tag:
        head.append(style_tag)


def inject_bilingual_assets(soup: BeautifulSoup) -> None:
    for element_id in ("codex-bilingual-viewer-style", "codex-bilingual-viewer-script"):
        existing = soup.find(id=element_id)
        if existing:
            existing.decompose()
    head = soup.head or soup.new_tag("head")
    if not soup.head:
        soup.html.insert(0, head)
    body = soup.body or soup.new_tag("body")
    if not soup.body:
        soup.html.append(body)
    style_tag = BeautifulSoup(BILINGUAL_CSS, "lxml").find("style")
    script_tag = BeautifulSoup(BILINGUAL_SCRIPT, "lxml").find("script")
    if style_tag:
        head.append(style_tag)
    if script_tag:
        body.append(script_tag)


def document_origin(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(["link", "script"]):
        value = tag.get("href") if tag.name == "link" else tag.get("src")
        if isinstance(value, str) and value.startswith("/static/browse/"):
            return "https://arxiv.org"
    for tag in soup.find_all("a", href=True, limit=40):
        href = tag.get("href")
        if isinstance(href, str) and href.startswith("https://arxiv.org/html/"):
            return "https://arxiv.org"
    return "https://ar5iv.labs.arxiv.org"


def document_base_url(soup: BeautifulSoup) -> str:
    base = soup.find("base")
    href = base.get("href") if base else ""
    if not isinstance(href, str) or not href:
        return ""
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith(("/html/", "/assets/", "/static/")):
        absolute = document_origin(soup) + href
        base["href"] = absolute
        return absolute
    return ""


def fix_file_viewer_links(soup: BeautifulSoup) -> None:
    base_url = document_base_url(soup)
    for tag in soup.find_all(["img", "link", "script", "a", "source", "object", "embed"]):
        attr = "data" if tag.name == "object" else "href" if tag.name in {"a", "link"} else "src"
        value = tag.get(attr)
        if not isinstance(value, str):
            continue
        if value.startswith(("http://", "https://", "data:", "#", "mailto:", "javascript:")):
            continue
        if value.startswith("//"):
            tag[attr] = "https:" + value
            continue
        if value.startswith("/html/") or value.startswith("/assets/"):
            tag[attr] = document_origin(soup) + value
            continue
        if value.startswith("/static/"):
            tag[attr] = "https://arxiv.org" + value
            continue
        if base_url:
            tag[attr] = urljoin(base_url, value)
            continue
        if re.match(r"^\d{4}\.\d{4,5}v\d+/", value):
            tag[attr] = "https://arxiv.org/html/" + value


def rebase_local_asset_links(soup: BeautifulSoup, source_dir: Path, output_dir: Path) -> None:
    """Keep generated local assets valid when HTML moves from inputs/ to outputs/."""
    for tag in soup.find_all(["img", "link", "script", "source", "object", "embed"]):
        attr = "data" if tag.name == "object" else "href" if tag.name == "link" else "src"
        value = tag.get(attr)
        if not isinstance(value, str) or not value:
            continue
        if value.startswith(("http://", "https://", "data:", "//", "/", "#")):
            continue
        resolved = (source_dir / value).resolve()
        if not resolved.exists():
            continue
        tag[attr] = Path(os.path.relpath(resolved, output_dir.resolve())).as_posix()


def canonical_output_path(path: Path) -> Path:
    """Normalize legacy Korean filenames to the single bilingual reader output."""
    if path.name.endswith(".ko.paper.html"):
        return path.with_name(path.name[:-len(".ko.paper.html")] + ".ko-en.paper.html")
    return path


def extract_reader_document(soup: BeautifulSoup, selector: str) -> BeautifulSoup:
    article = soup.select_one(selector)
    if not article:
        raise RuntimeError("Reader document not found: " + selector)
    document = BeautifulSoup('<html><head></head><body></body></html>', "lxml")
    if soup.title:
        document.head.append(BeautifulSoup(str(soup.title), "lxml").title)
    document.body.append(BeautifulSoup(str(article), "lxml").article)
    return document


def build_bilingual_view(ko_soup: BeautifulSoup, source_soup: BeautifulSoup) -> BeautifulSoup:
    source_copy = BeautifulSoup(str(source_soup), "lxml")
    inject_style(ko_soup)
    inject_style(source_copy)
    fix_file_viewer_links(ko_soup)
    fix_file_viewer_links(source_copy)

    ko_article = ko_soup.select_one("article.ltx_document")
    en_article = source_copy.select_one("article.ltx_document")
    if not ko_article or not en_article:
        raise RuntimeError("Could not find article.ltx_document in source or translated HTML")

    out = BeautifulSoup("<!doctype html><html lang=\"ko\"><head></head><body class=\"has_bilingual_view\"></body></html>", "lxml")
    meta_charset = out.new_tag("meta", charset="utf-8")
    meta_viewport = out.new_tag("meta")
    meta_viewport["name"] = "viewport"
    meta_viewport["content"] = "width=device-width, initial-scale=1"
    out.head.append(meta_charset)
    out.head.append(meta_viewport)
    title = ko_soup.find("title")
    if title:
        out.head.append(BeautifulSoup(str(title), "lxml").find("title"))
    for style in ko_soup.find_all("style", id=["codex-paper-viewer-style"]):
        out.head.append(BeautifulSoup(str(style), "lxml").find("style"))
    inject_bilingual_assets(out)

    nav = out.new_tag("nav")
    nav["class"] = "codex_tabs"
    nav["aria-label"] = "paper language tabs"
    for label, target, selected in (("한국어", "codex-panel-ko", "true"), ("원본 보기", "codex-panel-parallel", "false")):
        button = out.new_tag("button")
        button["class"] = "codex_tab_button"
        button["data-target"] = target
        button["aria-selected"] = selected
        button.string = label
        nav.append(button)
    sync_button = out.new_tag("button")
    sync_button["class"] = "codex_sync_button"
    sync_button["type"] = "button"
    sync_button["aria-pressed"] = "true"
    sync_button["hidden"] = ""
    sync_button.string = "스크롤 동기화 끄기"
    nav.append(sync_button)

    main = out.new_tag("main")
    ko_panel = out.new_tag("section", id="codex-panel-ko")
    ko_panel["class"] = "codex_panel"
    parallel_panel = out.new_tag("section", id="codex-panel-parallel")
    parallel_panel["class"] = "codex_panel"
    parallel_panel["hidden"] = ""
    parallel = out.new_tag("div")
    parallel["class"] = "codex_parallel"
    en_col = out.new_tag("div")
    en_col["class"] = "codex_parallel_column"
    ko_col = out.new_tag("div")
    ko_col["class"] = "codex_parallel_column"
    en_label = out.new_tag("div")
    en_label["class"] = "codex_parallel_label"
    en_label.string = "English original"
    ko_label = out.new_tag("div")
    ko_label["class"] = "codex_parallel_label"
    ko_label.string = "Korean translation"
    ko_panel.append(BeautifulSoup(str(ko_article), "lxml").find("article"))
    en_col.append(en_label)
    en_col.append(BeautifulSoup(str(en_article), "lxml").find("article"))
    ko_col.append(ko_label)
    ko_col.append(BeautifulSoup(str(ko_article), "lxml").find("article"))
    parallel.append(en_col)
    parallel.append(ko_col)
    parallel_panel.append(parallel)
    main.append(ko_panel)
    main.append(parallel_panel)
    out.body.insert(0, nav)
    out.body.insert(1, main)
    return out


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Translate ar5iv HTML by block while preserving paper layout.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--provider", choices=("api", "codex"), default="api")
    parser.add_argument("--model", default="", help="Codex default: gpt-6-luna; API default: gpt-5.4-mini")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--max-chars", type=int, default=5000)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=10, help="maximum number of translation batches in flight")
    parser.add_argument("--progress-log", default="")
    parser.add_argument("--bilingual-output", default="", help="Optional HTML output with Korean and English tabs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.model = translation_model(args.provider, args.model)

    load_env_file(Path(args.env_file))
    log = log_factory(Path(args.progress_log) if args.progress_log else None)

    input_path = Path(args.input).resolve()
    output_path = canonical_output_path(Path(args.bilingual_output or args.output).resolve())
    cache_path = Path(args.cache).resolve()
    soup = BeautifulSoup(input_path.read_text(encoding="utf-8"), "lxml")
    blocks = collect_blocks(soup)
    cache = load_cache(cache_path)

    todo: list[tuple[str, str]] = []
    translations: dict[str, str] = {}
    id_to_tag: dict[str, Tag] = {}
    id_to_tags: dict[str, list[str]] = {}
    for idx, tag in enumerate(blocks):
        bid = f"b{idx}"
        source_html = str(tag)
        source_masked, html_tags = mask_html(source_html)
        id_to_tag[bid] = tag
        id_to_tags[bid] = html_tags
        cached = cache.get(block_hash(source_masked))
        if cached is not None:
            translations[bid] = cached
        else:
            todo.append((bid, source_masked))

    log(f"input={input_path}")
    log(f"output={output_path}")
    log(f"blocks={len(blocks)} cached={len(translations)} todo={len(todo)}")
    if args.dry_run:
        log(f"dry_run=true chars_to_translate={sum(len(x[1]) for x in todo)}")
        return

    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if args.provider == "api" and (not api_key or not base_url):
        raise SystemExit("OPENAI_API_KEY and OPENAI_BASE_URL are required for the api provider")
    if args.provider == "codex" and not (os.environ.get("CODEX_EXECUTABLE") or shutil.which("codex")):
        raise SystemExit("codex CLI is required for the codex provider")

    batches = make_batches(todo, args.max_chars)
    concurrency = max(1, min(args.concurrency, 3 if args.provider == "codex" else args.concurrency))
    log(f"translating {len(batches)} batches provider={args.provider} concurrency={concurrency}")

    def translate_batch(batch: list[tuple[str, str]]) -> tuple[dict[str, str], int, int]:
        if args.provider == "codex":
            return call_codex(args.model, batch, args.timeout, args.max_retries)
        return call_api(base_url, api_key, args.model, batch, args.timeout, args.max_retries)

    cumulative_input_tokens = 0
    cumulative_output_tokens = 0

    def finalize_batch(
        batch_no: int,
        batch: list[tuple[str, str]],
        response: tuple[dict[str, str], int, int],
    ) -> None:
        nonlocal cumulative_input_tokens, cumulative_output_tokens
        result, input_tokens, output_tokens = response
        missing = [bid for bid, _ in batch if bid not in result]
        if missing:
            log(f"batch {batch_no} missing={len(missing)}; retrying one by one")
            for bid, html_fragment in batch:
                if bid in result:
                    continue
                single, single_input_tokens, single_output_tokens = translate_batch([(bid, html_fragment)])
                result.update(single)
                input_tokens += single_input_tokens
                output_tokens += single_output_tokens
        for bid, source_masked in batch:
            ko_masked = result[bid]
            translations[bid] = ko_masked
            append_cache(cache_path, source_masked, ko_masked)
        cumulative_input_tokens += input_tokens
        cumulative_output_tokens += output_tokens
        log(
            f"completed batch {batch_no}/{len(batches)} translated={len(translations)}/{len(blocks)} "
            f"input_tokens={cumulative_input_tokens} output_tokens={cumulative_output_tokens}"
        )

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="translate") as executor:
        futures = {}
        for batch_no, batch in enumerate(batches, 1):
            log(f"queued batch {batch_no}/{len(batches)} blocks={len(batch)} chars={sum(len(x[1]) for x in batch)}")
            future = executor.submit(translate_batch, batch)
            futures[future] = (batch_no, batch)
        for future in as_completed(futures):
            batch_no, batch = futures[future]
            finalize_batch(batch_no, batch, future.result())

    for bid, tag in id_to_tag.items():
        ko_masked = translations.get(bid)
        if not ko_masked:
            continue
        ko_html = unmask_html(ko_masked, id_to_tags[bid])
        parsed = BeautifulSoup(ko_html, "lxml")
        replacement = parsed.find(tag.name)
        if replacement:
            tag.replace_with(replacement)

    format_pdf_titles(soup, BeautifulSoup(input_path.read_text(encoding="utf-8"), "lxml"))
    inject_style(soup)
    rebase_local_asset_links(soup, input_path.parent, output_path.parent)
    fix_file_viewer_links(soup)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_soup = BeautifulSoup(input_path.read_text(encoding="utf-8"), "lxml")
    rebase_local_asset_links(source_soup, input_path.parent, output_path.parent)
    bilingual = build_bilingual_view(soup, source_soup)
    output_path.write_text(str(bilingual), encoding="utf-8")
    log(f"wrote {output_path}")



if __name__ == "__main__":
    main(sys.argv[1:])
