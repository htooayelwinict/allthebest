from __future__ import annotations

from pathlib import Path


DEFAULT_MODEL = "google/gemini-3.1-flash-lite"
DEFAULT_RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "web_search_ts"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "test" / "web_research_result.json"
DEFAULT_MARKDOWN = Path(__file__).resolve().parents[1] / "test" / "web_research_result.md"
RESULT_MARKER = "WEB_RESEARCH_RESULT:"
PLAYWRIGHT_READY_MARKER = ".playwright-browsers.chromium.ready"

PACKAGE_JSON = """\
{
  "name": "appv3-web-research-playwright",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "devDependencies": {
    "@playwright/test": "^1.59.1"
  }
}
"""

PLAYWRIGHT_CONFIG_TS = """\
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 90000,
  use: {
    headless: true,
    viewport: { width: 1365, height: 900 },
    locale: 'en-US',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  reporter: 'list',
});
"""
