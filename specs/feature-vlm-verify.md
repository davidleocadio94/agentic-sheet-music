# Feature spec — vlm-verify

## Problem

Audiveris OMR produces MusicXML with real transcription errors on dense engravings (chord grouping, voice splitting, missed time signatures). We already rely on the existing `music-theory-reviewer` agent for *musical* review downstream, but the first transcription layer has no check against the source image. A vision LLM is excellent at spatial verification even when it's bad at full transcription (Apr 2026 evidence: Gemini 3.1 Pro gets ~99% on "read this fact off the page" VQA but only ~17% on "transcribe the whole page"). We use its strength — verification — not its weakness.

## Approach

For each page of the source PDF:
1. Render the page to PNG.
2. Send (page image + the MusicXML slice covering that page) to Gemini 3.1 Pro with a structured-output schema asking for per-measure disagreements.
3. Parse into `VerificationReport` (one per page). Aggregate across pages into a `ScoreVerification`.
4. Attach the aggregate report to the pipeline's output. **Never auto-apply fixes in v1** — the verifier can hallucinate "fixes" just like it can hallucinate transcriptions. The user / downstream `music-theory-reviewer` agent decides what to do with the report.

## Inputs

- `source_pdf: Path`
- `candidate_xml: Path` — the MusicXML Audiveris wrote (per-movement).
- `omr_book: Path` — needed to map measure numbers → pages.
- `movement: int = 1` — matches our annotation pipeline.
- `api_key: str | None = None` — override; otherwise `GEMINI_API_KEY` env or the slide-converter `.env` fallback (dev convenience for this laptop).
- `model: str = "gemini-3.1-pro-preview"` — overridable for flash-lite fast runs.
- `max_pages: int | None = None` — cap for tests / cost control.

## Outputs

New types (added to `types.py`):

```python
@dataclass(frozen=True)
class MeasureDisagreement:
    measure: int
    issue: str            # "chord group contains two Cs at the same octave"
    suggested_fix: str | None
    confidence: float     # 0..1, VLM-reported

@dataclass(frozen=True)
class PageVerification:
    page_index: int       # 0-based
    overall_confidence: float
    observed_key_signature: str | None
    observed_time_signature: str | None
    disagreements: tuple[MeasureDisagreement, ...]

@dataclass(frozen=True)
class ScoreVerification:
    pages: tuple[PageVerification, ...]
    model: str
    total_disagreements: int
```

## Algorithm

- Key resolution: check `api_key` arg → `GEMINI_API_KEY` env → `~/Documents/deeplearning_ai/slide-converter/.env`.
- Page rendering: reuse `pymupdf` at 200 DPI (enough for visual verification, keeps prompt tokens down).
- MusicXML slicing: for each page, extract only the measures on that page (per `parse_measure_boxes`). Use the existing `.omr` page→measure map. Emit as a per-page XML fragment to keep tokens bounded.
- Gemini call: use the Python `google-genai` SDK with `response_schema=PageVerification` (Pydantic). `thinking_level="high"`. `max_output_tokens=32000` per page.
- Error handling: any single-page failure produces a `PageVerification` with `overall_confidence=0.0`, disagreements `()`, and a disagreement record noting the SDK error. Never crash the whole pass.

## Edge cases

- **No key available** → `VerifierNotConfigured` exception with setup instructions.
- **Model returns malformed JSON** → `PageVerification` with a single `MeasureDisagreement` noting the parse error; rest of pipeline continues.
- **Candidate XML has zero measures for a page** → skip that page, emit an empty `PageVerification`.
- **Response truncation (MAX_TOKENS)** → log and return a partial report; flag `overall_confidence` down.
- **Image too large for the 1M-token context** — unlikely at 200 DPI for one page, but cap output to 32K and log the input token count.
- **No network** → `VerifierNotAvailable` with clear error; rest of pipeline (--annotate / --audio / --playalong) still works without it.

## Test cases

Tests in `tests/verification/test_vlm_verifier.py`:

### Unit tests (fast — no API calls)

- `test_key_resolution_prefers_explicit_arg` — pass `api_key="foo"`; assert resolved value is "foo". No network call because we mock the client.
- `test_key_resolution_env_wins_over_dotenv` — set `GEMINI_API_KEY=bar` in monkeypatched environ, point dotenv at a file with a different key, assert bar wins.
- `test_key_resolution_raises_when_absent` — no env, no dotenv → `VerifierNotConfigured`.
- `test_parses_pydantic_response` — mock `client.models.generate_content` to return a canned `PageVerification` JSON; assert we get back a typed `PageVerification` with the right fields.
- `test_empty_candidate_xml_skipped` — no measures on a page → empty PageVerification, no API call.

### Integration test (real API call, gated by `@pytest.mark.omr_binary` plus env)

- `test_verifier_flags_milonga_m3_chord_grouping` — runs real Gemini call on milonga page 1. We know Audiveris emitted `('B-4', 'C#5')` at m.3 beat 1 — physically implausible for a guitar arpeggio. Assert the verifier flags at least one disagreement on measures 1–15 (page 1). This is a realistic correctness test: we know the OMR made mistakes on that page; verifier should find at least one.

## Non-goals

- Auto-applying verifier suggestions. Verifier output is advisory.
- Whole-score single-call verification. Per-page only.
- Replacing Audiveris entirely with Gemini transcription. Separate non-goal per research.
- Caching responses to disk. Future optimization.
- Multiple VLM backends. v1 is Gemini only; OpenAI / Anthropic / fine-tunes are future.

## Design sketch

```python
# src/agentic_sheet_music/omr/vlm_verifier.py

class VerifierError(Exception): ...
class VerifierNotConfigured(VerifierError): ...
class VerifierNotAvailable(VerifierError): ...

def verify_score(
    *,
    source_pdf: Path,
    candidate_xml: Path,
    omr_book: Path,
    movement: int = 1,
    api_key: str | None = None,
    model: str = "gemini-3.1-pro-preview",
    max_pages: int | None = None,
) -> ScoreVerification: ...
```

CLI: `--verify` attaches a `ScoreVerification` to the pipeline run and prints a summary. Integrates with `--annotate` / `--playalong` for future "highlight verifier-flagged measures in yellow" work (not in v1).

## Correctness stance

Per `.claude/rules/correctness.md`: this module has the specific musical claim test (flagging a known-bad measure on the milonga). We do **not** assert that the verifier is correct in general — only that on our reference piece, it catches at least one error, because the piece definitely has errors. As we expand to more pieces, we'll add per-piece "known-bad measures" correctness tests.
