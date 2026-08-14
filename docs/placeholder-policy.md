# Final Placeholder Policy

The final scan distinguishes renderer source from publishable output:

- `templates/` contains Jinja expressions by design. Those files are rendered inputs, not
  submission copy. The generated README/Devpost/video/checklist surfaces are scanned instead.
- `docs/05-devpost-submission-draft.md` and `docs/06-video-script.md` are the only allowlisted v1
  planning documents. Both must retain a prominent **Historical planning** banner and are never
  accepted as Devpost or video input.
- Every other Markdown document under `docs/`, plus the public README, final Devpost copy, final
  video script, quality summary, screenshot manifest/captions, and submission checklist, must be
  free of unresolved auto-fill markers and credential-like tokens.

Run the policy exactly as CI/release review does:

```bash
uv run --frozen --no-editable python scripts/check-final-placeholders.py
```

The scanner also fails if the historical allowlist expands silently, a banner disappears, or an
allowlisted file no longer contains legacy tokens and should therefore be removed from the list.
