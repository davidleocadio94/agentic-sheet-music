"""Apply Gemini-suggested fixes to MusicXML. See specs/feature-vlm-autocorrect.md.

Workflow:
  1. Verifier sees an OMR error in the source PDF.
  2. Verifier suggests a structured fix (typed op + args + confidence).
  3. We apply only fixes whose op is supported AND confidence >= threshold.
  4. Mutated XML is written to a NEW file — never overwrites the source.

The fix application code is pure XML manipulation — no music21 — so each
mutator is small, deterministic, and testable in isolation.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

import pymupdf
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, Field

from agentic_sheet_music.omr.vlm_verifier import (
    DEFAULT_DOTENV_PATHS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_RENDER_DPI,
    VerifierError,
    _measures_per_page,
    _render_page_png,
    _resolve_api_key,
    _sheets_for_movement,
    _slice_musicxml,
)
from agentic_sheet_music.types import (
    AppliedFix,
    AutoCorrectionResult,
    MeasureDisagreement,
    PageVerification,
    ScoreVerification,
    SkippedFix,
)

logger = logging.getLogger(__name__)


SUPPORTED_OPS = frozenset(
    {
        "remove_tuplet",
        "change_pitch",
        "change_duration",
        "add_dot",
        "remove_dot",
        "remove_note",
        "change_time_signature",
    }
)


class AutoCorrectionError(Exception):
    pass


# ---------------------------------------------------------------------------
# Pydantic schemas (richer than verifier — adds structured fixes).


class _StructuredFixPydantic(BaseModel):
    op: str = Field(..., description="One of: " + ", ".join(sorted(SUPPORTED_OPS)))
    measure: int = Field(..., ge=1)
    args_json: str = Field(
        default="{}",
        description=(
            "Operation args as a JSON object string. "
            "change_pitch: {\"from_pitch\":\"A3\",\"to_pitch\":\"A4\"}. "
            "change_duration: {\"note_pitch\":\"E5\",\"new_type\":\"16th\",\"new_duration\":1}. "
            "add_dot/remove_dot/remove_note/remove_tuplet: "
            "{\"note_pitch\":\"C4\",\"voice\":2} (voice optional). "
            "change_time_signature: {\"beats\":2,\"beat_type\":4}."
        ),
    )

    @property
    def args(self) -> dict:
        import json

        try:
            return json.loads(self.args_json)
        except Exception:  # noqa: BLE001
            return {}


class _DisagreementWithFixPydantic(BaseModel):
    measure: int = Field(..., ge=1)
    issue: str
    suggested_fix: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    structured_fix: _StructuredFixPydantic | None = Field(
        None,
        description=(
            "If you can express the fix as a single typed operation, fill this in. "
            "Otherwise leave None."
        ),
    )


class _PageVerificationWithFixesPydantic(BaseModel):
    overall_confidence: float = Field(..., ge=0.0, le=1.0)
    observed_key_signature: str | None = None
    observed_time_signature: str | None = None
    disagreements: list[_DisagreementWithFixPydantic] = Field(default_factory=list)


# ---------------------------------------------------------------------------


def autocorrect_score(
    *,
    source_pdf: Path,
    candidate_xml: Path,
    omr_book: Path,
    movement: int = 1,
    auto_apply: bool = False,
    min_confidence: float = 0.95,
    output_xml: Path | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    max_pages: int | None = None,
    render_dpi: int = DEFAULT_RENDER_DPI,
) -> AutoCorrectionResult:
    """Verify, optionally apply structured fixes, and return a typed result."""
    candidate_xml = candidate_xml.resolve()
    if output_xml is not None:
        output_xml = output_xml.resolve()
        if output_xml == candidate_xml:
            raise AutoCorrectionError(
                "output_xml must differ from candidate_xml — won't overwrite source"
            )

    verification, all_fixes = _verify_with_structured_fixes(
        source_pdf=source_pdf,
        candidate_xml=candidate_xml,
        omr_book=omr_book,
        movement=movement,
        api_key=api_key,
        model=model,
        max_pages=max_pages,
        render_dpi=render_dpi,
    )

    applied: list[AppliedFix] = []
    skipped: list[SkippedFix] = []

    if not auto_apply:
        for fix, conf in all_fixes:
            skipped.append(
                SkippedFix(
                    measure=fix.measure,
                    op=fix.op,
                    reason="auto_apply=False",
                    confidence=conf,
                )
            )
        return AutoCorrectionResult(
            original_xml=candidate_xml,
            corrected_xml=None,
            applied_fixes=tuple(applied),
            skipped_fixes=tuple(skipped),
            verification=verification,
        )

    tree = ET.parse(candidate_xml)
    root = tree.getroot()

    for fix, conf in sorted(all_fixes, key=lambda f: (f[0].measure, f[0].op)):
        if conf < min_confidence:
            skipped.append(
                SkippedFix(
                    measure=fix.measure,
                    op=fix.op,
                    reason=f"confidence {conf:.2f} below threshold {min_confidence:.2f}",
                    confidence=conf,
                )
            )
            continue
        if fix.op not in SUPPORTED_OPS:
            skipped.append(
                SkippedFix(
                    measure=fix.measure,
                    op=fix.op,
                    reason="op not in SUPPORTED_OPS",
                    confidence=conf,
                )
            )
            continue
        ok = _dispatch(root, fix)
        if ok:
            applied.append(
                AppliedFix(
                    measure=fix.measure,
                    op=fix.op,
                    description=f"{fix.op}({fix.args})",
                    before="",
                    after="",
                )
            )
        else:
            skipped.append(
                SkippedFix(
                    measure=fix.measure,
                    op=fix.op,
                    reason="no matching XML element",
                    confidence=conf,
                )
            )

    out_path = output_xml or candidate_xml.with_suffix(".corrected.xml")
    if out_path == candidate_xml:
        raise AutoCorrectionError(
            "default output collided with candidate_xml — pass output_xml explicitly"
        )
    tree.write(out_path, xml_declaration=True, encoding="utf-8")

    return AutoCorrectionResult(
        original_xml=candidate_xml,
        corrected_xml=out_path,
        applied_fixes=tuple(applied),
        skipped_fixes=tuple(skipped),
        verification=verification,
    )


# ---------------------------------------------------------------------------
# VLM call (extends verifier with structured-fix schema)


def _verify_with_structured_fixes(
    *,
    source_pdf: Path,
    candidate_xml: Path,
    omr_book: Path,
    movement: int,
    api_key: str | None,
    model: str,
    max_pages: int | None,
    render_dpi: int,
) -> tuple[ScoreVerification, list[tuple[_StructuredFixPydantic, float]]]:
    key = _resolve_api_key(explicit=api_key, dotenv_paths=DEFAULT_DOTENV_PATHS)
    client = genai.Client(api_key=key)

    sheets = _sheets_for_movement(omr_book, movement)
    if not sheets:
        raise VerifierError(f"movement {movement} not found in {omr_book}")

    candidate_root = ET.parse(candidate_xml).getroot()
    per_page = _measures_per_page(omr_book, movement)

    pages: list[PageVerification] = []
    fixes_with_conf: list[tuple[_StructuredFixPydantic, float]] = []

    with pymupdf.open(source_pdf) as doc:
        for i, sheet_number in enumerate(sheets):
            if max_pages is not None and i >= max_pages:
                break
            page_index = sheet_number - 1
            if page_index >= len(doc):
                continue
            ms = per_page.get(sheet_number, [])
            if not ms:
                pages.append(
                    PageVerification(
                        page_index=page_index,
                        overall_confidence=0.0,
                        observed_key_signature=None,
                        observed_time_signature=None,
                        disagreements=(),
                    )
                )
                continue
            png = _render_page_png(doc[page_index], render_dpi)
            xml_slice = _slice_musicxml(candidate_root, ms)
            page_report, page_fixes = _verify_one_page(
                client=client,
                model=model,
                page_index=page_index,
                page_png=png,
                xml_slice=xml_slice,
                measure_range=(ms[0], ms[-1]),
            )
            pages.append(page_report)
            fixes_with_conf.extend(page_fixes)

    total = sum(len(p.disagreements) for p in pages)
    return (
        ScoreVerification(pages=tuple(pages), model=model, total_disagreements=total),
        fixes_with_conf,
    )


def _verify_one_page(
    *,
    client: genai.Client,
    model: str,
    page_index: int,
    page_png: bytes,
    xml_slice: str,
    measure_range: tuple[int, int],
) -> tuple[PageVerification, list[tuple[_StructuredFixPydantic, float]]]:
    start, end = measure_range
    ops = ", ".join(sorted(SUPPORTED_OPS))
    prompt = (
        f"You are verifying an OMR result against the original engraving "
        f"(page {page_index + 1}, classical guitar score).\n\n"
        f"The MusicXML below claims to transcribe measures {start}-{end}. "
        f"Compare it to the page image. For each disagreement you can "
        f"visually verify, fill in `structured_fix` with one of these "
        f"operations: {ops}.\n\n"
        f"Operation arg shapes:\n"
        f"- remove_tuplet: {{note_pitch: 'E5', voice?: 1}}\n"
        f"- change_pitch: {{from_pitch: 'A3', to_pitch: 'A4'}}\n"
        f"- change_duration: {{note_pitch: 'E5', new_type: '16th', new_duration: 1}}\n"
        f"- add_dot / remove_dot: {{note_pitch: 'C4', voice?: 2}}\n"
        f"- remove_note: {{note_pitch: 'A3', voice?: 2}}\n"
        f"- change_time_signature: {{beats: 2, beat_type: 4}}\n\n"
        f"If a fix can't fit one of these ops cleanly, leave structured_fix "
        f"as null and just describe the issue. Don't invent fixes.\n\n"
        f"Candidate MusicXML:\n```xml\n{xml_slice}\n```"
    )

    try:
        resp = client.models.generate_content(
            model=model,
            contents=[
                genai_types.Part.from_bytes(data=page_png, mime_type="image/png"),
                prompt,
            ],
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_PageVerificationWithFixesPydantic,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Gemini call failed for page %d: %s", page_index, e)
        return (
            PageVerification(
                page_index=page_index,
                overall_confidence=0.0,
                observed_key_signature=None,
                observed_time_signature=None,
                disagreements=(),
            ),
            [],
        )

    if not getattr(resp, "text", None):
        return (
            PageVerification(
                page_index=page_index,
                overall_confidence=0.0,
                observed_key_signature=None,
                observed_time_signature=None,
                disagreements=(),
            ),
            [],
        )

    try:
        parsed = _PageVerificationWithFixesPydantic.model_validate_json(resp.text)
    except Exception as e:  # noqa: BLE001
        logger.warning("Gemini JSON parse failed for page %d: %s", page_index, e)
        return (
            PageVerification(
                page_index=page_index,
                overall_confidence=0.0,
                observed_key_signature=None,
                observed_time_signature=None,
                disagreements=(),
            ),
            [],
        )

    fixes: list[tuple[_StructuredFixPydantic, float]] = []
    disagreements: list[MeasureDisagreement] = []
    for d in parsed.disagreements:
        disagreements.append(
            MeasureDisagreement(
                measure=d.measure,
                issue=d.issue,
                suggested_fix=d.suggested_fix,
                confidence=d.confidence,
            )
        )
        if d.structured_fix is not None:
            fixes.append((d.structured_fix, d.confidence))

    return (
        PageVerification(
            page_index=page_index,
            overall_confidence=parsed.overall_confidence,
            observed_key_signature=parsed.observed_key_signature,
            observed_time_signature=parsed.observed_time_signature,
            disagreements=tuple(disagreements),
        ),
        fixes,
    )


# ---------------------------------------------------------------------------
# Mutators (pure XML — testable without API)


def _dispatch(root: ET.Element, fix: _StructuredFixPydantic) -> bool:
    op = fix.op
    args = fix.args
    if op == "remove_tuplet":
        return _apply_remove_tuplet(
            root,
            measure=fix.measure,
            note_pitch=str(args.get("note_pitch", "")),
            voice=_arg_int(args, "voice"),
        )
    if op == "change_pitch":
        return _apply_change_pitch(
            root,
            measure=fix.measure,
            from_pitch=str(args.get("from_pitch", "")),
            to_pitch=str(args.get("to_pitch", "")),
        )
    if op == "change_duration":
        return _apply_change_duration(
            root,
            measure=fix.measure,
            note_pitch=str(args.get("note_pitch", "")),
            new_type=str(args.get("new_type", "")),
            new_duration=_arg_int(args, "new_duration"),
        )
    if op == "add_dot":
        return _apply_add_dot(
            root,
            measure=fix.measure,
            note_pitch=str(args.get("note_pitch", "")),
            voice=_arg_int(args, "voice"),
        )
    if op == "remove_dot":
        return _apply_remove_dot(
            root,
            measure=fix.measure,
            note_pitch=str(args.get("note_pitch", "")),
            voice=_arg_int(args, "voice"),
        )
    if op == "remove_note":
        return _apply_remove_note(
            root,
            measure=fix.measure,
            note_pitch=str(args.get("note_pitch", "")),
            voice=_arg_int(args, "voice"),
        )
    if op == "change_time_signature":
        beats = _arg_int(args, "beats")
        beat_type = _arg_int(args, "beat_type")
        if beats is None or beat_type is None:
            return False
        return _apply_change_time_signature(
            root,
            beats=beats,
            beat_type=beat_type,
            measure=fix.measure,
        )
    return False


def _arg_int(args: dict, key: str) -> int | None:
    v = args.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _find_measure(root: ET.Element, measure: int) -> ET.Element | None:
    for m in root.iter("measure"):
        try:
            if int(m.get("number", "")) == measure:
                return m
        except ValueError:
            continue
    return None


def _matches_pitch(note: ET.Element, pitch: str) -> bool:
    p = note.find("pitch")
    if p is None:
        return False
    step = p.findtext("step", default="")
    octave = p.findtext("octave", default="")
    alter = p.findtext("alter", default="")
    candidate = step
    if alter == "1":
        candidate += "#"
    elif alter == "-1":
        candidate += "b"
    candidate += octave
    return candidate == pitch


def _matches_voice(note: ET.Element, voice: int | None) -> bool:
    if voice is None:
        return True
    return note.findtext("voice", default="") == str(voice)


def _find_notes(
    root: ET.Element,
    measure: int,
    note_pitch: str,
    voice: int | None = None,
) -> list[ET.Element]:
    m = _find_measure(root, measure)
    if m is None:
        return []
    return [
        n
        for n in m.findall("note")
        if _matches_pitch(n, note_pitch) and _matches_voice(n, voice)
    ]


def _apply_remove_tuplet(
    root: ET.Element,
    *,
    measure: int,
    note_pitch: str,
    voice: int | None = None,
) -> bool:
    notes = _find_notes(root, measure, note_pitch, voice)
    if not notes:
        return False
    changed = False
    for n in notes:
        tm = n.find("time-modification")
        if tm is not None:
            n.remove(tm)
            changed = True
        notations = n.find("notations")
        if notations is not None:
            for tup in list(notations.findall("tuplet")):
                notations.remove(tup)
                changed = True
            if len(notations) == 0:
                n.remove(notations)
    return changed


def _apply_change_pitch(
    root: ET.Element,
    *,
    measure: int,
    from_pitch: str,
    to_pitch: str,
) -> bool:
    notes = _find_notes(root, measure, from_pitch)
    if not notes:
        return False
    new = _parse_pitch(to_pitch)
    if new is None:
        return False
    for n in notes:
        p = n.find("pitch")
        if p is None:
            continue
        step = p.find("step")
        if step is not None:
            step.text = new["step"]
        oct_el = p.find("octave")
        if oct_el is not None:
            oct_el.text = str(new["octave"])
        alter_el = p.find("alter")
        if new["alter"] is not None:
            if alter_el is None:
                alter_el = ET.SubElement(p, "alter")
            alter_el.text = str(new["alter"])
        elif alter_el is not None:
            p.remove(alter_el)
    return True


def _parse_pitch(pitch: str) -> dict | None:
    """Parse 'A3', 'C#4', 'Bb5' into step/octave/alter."""
    if not pitch:
        return None
    step = pitch[0].upper()
    if step not in "CDEFGAB":
        return None
    rest = pitch[1:]
    alter: int | None = None
    if rest.startswith("#"):
        alter = 1
        rest = rest[1:]
    elif rest.startswith("b") or rest.startswith("-"):
        alter = -1
        rest = rest[1:]
    try:
        octave = int(rest)
    except ValueError:
        return None
    return {"step": step, "octave": octave, "alter": alter}


def _apply_change_duration(
    root: ET.Element,
    *,
    measure: int,
    note_pitch: str,
    new_type: str,
    new_duration: int | None,
) -> bool:
    if not new_type or new_duration is None:
        return False
    notes = _find_notes(root, measure, note_pitch)
    if not notes:
        return False
    for n in notes:
        t = n.find("type")
        if t is not None:
            t.text = new_type
        d = n.find("duration")
        if d is not None:
            d.text = str(new_duration)
    return True


def _apply_add_dot(
    root: ET.Element,
    *,
    measure: int,
    note_pitch: str,
    voice: int | None = None,
) -> bool:
    notes = _find_notes(root, measure, note_pitch, voice)
    if not notes:
        return False
    changed = False
    for n in notes:
        if n.find("dot") is not None:
            continue
        dot = ET.SubElement(n, "dot")
        # Insert <dot/> right after <type> for tidier MusicXML.
        n.remove(dot)
        type_el = n.find("type")
        if type_el is not None:
            idx = list(n).index(type_el) + 1
            n.insert(idx, ET.Element("dot"))
        else:
            n.append(ET.Element("dot"))
        d = n.find("duration")
        if d is not None and d.text and d.text.isdigit():
            d.text = str(int(int(d.text) * 1.5))
        changed = True
    return changed


def _apply_remove_dot(
    root: ET.Element,
    *,
    measure: int,
    note_pitch: str,
    voice: int | None = None,
) -> bool:
    notes = _find_notes(root, measure, note_pitch, voice)
    if not notes:
        return False
    changed = False
    for n in notes:
        dots = list(n.findall("dot"))
        if not dots:
            continue
        for d in dots:
            n.remove(d)
        # Restore base duration: dotted is original × 1.5; reverse with × 2/3.
        d_el = n.find("duration")
        if d_el is not None and d_el.text and d_el.text.isdigit():
            d_el.text = str(int(int(d_el.text) * 2 // 3))
        changed = True
    return changed


def _apply_remove_note(
    root: ET.Element,
    *,
    measure: int,
    note_pitch: str,
    voice: int | None = None,
) -> bool:
    m = _find_measure(root, measure)
    if m is None:
        return False
    targets = [
        n
        for n in m.findall("note")
        if _matches_pitch(n, note_pitch) and _matches_voice(n, voice)
    ]
    if not targets:
        return False
    for n in targets:
        m.remove(n)
    return True


def _apply_change_time_signature(
    root: ET.Element,
    *,
    beats: int,
    beat_type: int,
    measure: int = 1,
) -> bool:
    target = _find_measure(root, measure)
    if target is None:
        return False
    attrs = target.find("attributes")
    if attrs is None:
        attrs = ET.SubElement(target, "attributes")
        target.insert(0, attrs)
    time_el = attrs.find("time")
    if time_el is None:
        time_el = ET.SubElement(attrs, "time")
    for child in list(time_el):
        time_el.remove(child)
    ET.SubElement(time_el, "beats").text = str(beats)
    ET.SubElement(time_el, "beat-type").text = str(beat_type)
    return True
