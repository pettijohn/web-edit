# Repository instructions

- Only make minimal, directly relevant edits to `README.md`. Preserve its concise structure and avoid adding lengthy explanations.
- Keep UI text concise and functional. Do not add marketing copy, slogans, or promotional taglines.
- Run Python code with `uv run` or `uv run python`.
- Keep each prose paragraph on a single source line. Preserve line breaks required by Markdown structure.

## Wheel verification

The wheel must include the bundled CodeMirror JavaScript and all other frontend assets, and work without Node.js or a CDN at runtime. After changing frontend source, run `npm ci` and `npm run build`, then commit the regenerated assets.

Build the distributions and verify the wheel in an isolated environment:

```bash
uv build
uv run scripts/check_wheel.py dist/web_edit-0.1-py3-none-any.whl
```
