# Test Fixtures

Files already committed (no setup needed):
- `sample.ipynb` — Jupyter notebook with sales analysis
- `sample.html` — Confluence-style HTML export

---

## Files you need to add (tests skip gracefully if absent)

Drop these into this folder to unlock the full test suite:

| File | What to use | Tests it enables |
|---|---|---|
| `sample.pdf` | Any short PDF — a certificate, report, or spec doc | `TestPdfExtractor` — 4 tests |
| `sample.m4a` | Any voice recording — even 10 seconds | `TestAudioExtractor` — 4 tests |
| `sample.mp3` | Alternative to .m4a | Same as above |
| `sample.png` | Any image — diagram, screenshot, photo | `TestImageExtractor` — 3 tests |
| `sample.jpg` | Alternative to .png | Same as above |

**Good sources from your own work:**
- A certificate PDF from a training course
- A short standup or voice note recording
- A screenshot of an architecture diagram or system design

Files don't need to be long. A 1-page PDF and a 30-second audio clip are ideal —
they run fast and are real enough to catch extraction bugs.

---

## Running live AI tests

Some tests call the real AI model (end-to-end quality verification).
These are skipped by default to avoid API costs.

To enable:
```bash
# Windows
set MEMORIA_TEST_LIVE=1
pytest tests/

# Mac/Linux
MEMORIA_TEST_LIVE=1 pytest tests/
```

You need a valid `~/.memoria/config.yaml` with a working API key.
