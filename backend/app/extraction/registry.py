"""Working out what an uploaded file is, and reading it with the right parser.

The administrator drags in a pile of files and presses Import.  They are not
asked what each file is, because they should not have to be: counties send
whatever their software produces, and the mix changes between counties and
between years.

Detection is by content, not by file extension.  Each profile scores its
confidence that a file is its kind, the best score wins, and a file that no
profile recognises is recorded as a failed import with a readable reason
rather than being silently dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.coverage import DocumentKind
from app.extraction import enforcement_doc, investigation, permit, pur_form, tabular
from app.extraction.base import DataIssue, ExtractionResult
from app.extraction.text_source import load_text, sha256_file

#: Text-based profiles need a sample of the document's text to judge it.  For
#: a scanned PDF that means OCR, which is slow, so detection reads only the
#: first pages and the full read happens once the profile is chosen.
DETECTION_SAMPLE_CHARS = 8000

#: Below this, nothing recognised the file.
MIN_DETECTION_SCORE = 0.3


@dataclass
class Profile:
    """One supported source format."""

    name: str
    document_kind: str
    extract: Callable[..., ExtractionResult]
    #: Scores a file path directly (cheap, for tabular formats).
    sniff_path: Callable[[Path], float] | None = None
    #: Scores the document's text (for PDFs, .docx and plain text).
    sniff_text: Callable[[str], float] | None = None
    suffixes: tuple[str, ...] = ()


PROFILES: list[Profile] = [
    Profile(
        name=tabular.PROFILE_NAME,
        document_kind=DocumentKind.USE_REPORT,
        extract=tabular.extract,
        sniff_path=tabular.sniff,
        suffixes=(".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"),
    ),
    Profile(
        name=permit.PROFILE_NAME,
        document_kind=DocumentKind.PERMIT,
        extract=permit.extract,
        sniff_text=permit.sniff_text,
        suffixes=(".docx", ".pdf", ".txt"),
    ),
    Profile(
        name=investigation.PROFILE_NAME,
        document_kind=DocumentKind.INVESTIGATION,
        extract=investigation.extract,
        sniff_text=investigation.sniff_text,
        suffixes=(".pdf", ".docx", ".txt"),
    ),
    Profile(
        name=enforcement_doc.PROFILE_NAME,
        document_kind=DocumentKind.ENFORCEMENT,
        extract=enforcement_doc.extract,
        sniff_text=enforcement_doc.sniff_text,
        suffixes=(".pdf", ".docx", ".txt"),
    ),
    Profile(
        name=pur_form.PROFILE_NAME,
        document_kind=DocumentKind.USE_REPORT,
        extract=pur_form.extract,
        sniff_text=pur_form.sniff_text,
        suffixes=(".pdf", ".docx", ".txt"),
    ),
]

PROFILES_BY_NAME = {p.name: p for p in PROFILES}


@dataclass
class Detection:
    """Which profile will read a file, and how sure we are."""

    profile: Profile | None
    score: float
    reason: str
    scores: dict[str, float]
    #: The document's text, when detection had to read it. Carried so the
    #: importer can store the text without OCR'ing a scan for a second time.
    text: str | None = None

    @property
    def recognised(self) -> bool:
        return self.profile is not None


def detect(path: str | Path, *, allow_ocr: bool = True) -> Detection:
    """Identify a file's format by looking inside it."""
    path = Path(path)
    suffix = path.suffix.lower()
    scores: dict[str, float] = {}

    # Cheap, structural checks first.
    for profile in PROFILES:
        if profile.sniff_path is None:
            continue
        if profile.suffixes and suffix not in profile.suffixes:
            continue
        try:
            scores[profile.name] = profile.sniff_path(path)
        except Exception:
            scores[profile.name] = 0.0

    # A confident structural match means we never have to OCR to decide.
    best_structural = max(scores.values(), default=0.0)
    if best_structural >= 0.7:
        name = max(scores, key=lambda key: scores[key])
        return Detection(
            PROFILES_BY_NAME[name],
            best_structural,
            f"recognised as a {name.replace('_', ' ')} export from its columns",
            scores,
        )

    text_profiles = [
        p
        for p in PROFILES
        if p.sniff_text is not None and (not p.suffixes or suffix in p.suffixes)
    ]
    if text_profiles:
        try:
            document = load_text(path, allow_ocr=allow_ocr)
            sample = document.text[:DETECTION_SAMPLE_CHARS]
            full_text = document.text
        except Exception as exc:
            return Detection(None, 0.0, f"the file could not be read: {exc}", scores)
        for profile in text_profiles:
            try:
                scores[profile.name] = max(
                    scores.get(profile.name, 0.0), profile.sniff_text(sample)
                )
            except Exception:
                scores.setdefault(profile.name, 0.0)

    if not scores or max(scores.values()) < MIN_DETECTION_SCORE:
        return Detection(
            None,
            max(scores.values(), default=0.0),
            "this file does not look like a pesticide use report, a notice of intent "
            "or a restricted materials permit",
            scores,
        )

    name = max(scores, key=lambda key: scores[key])
    return Detection(
        PROFILES_BY_NAME[name],
        scores[name],
        f"recognised as a {name.replace('_', ' ')}",
        scores,
        text=locals().get("full_text"),
    )


def extract_file(
    path: str | Path,
    *,
    county: str | None = None,
    sha256: str | None = None,
    source_name: str | None = None,
    allow_ocr: bool = True,
    profile_name: str | None = None,
) -> ExtractionResult:
    """Detect a file's format and extract it.

    ``profile_name`` overrides detection, which is how an administrator
    corrects a misread file from the review queue without renaming it.
    """
    path = Path(path)
    name = source_name or path.name
    digest = sha256 or sha256_file(path)

    if profile_name:
        profile = PROFILES_BY_NAME.get(profile_name)
        if profile is None:
            result = ExtractionResult(source_name=name, sha256=digest)
            result.issues.append(
                DataIssue("unknown_profile", f"no extraction profile named {profile_name!r}")
            )
            return result
        detection = Detection(profile, 1.0, "profile chosen by an administrator", {})
    else:
        detection = detect(path, allow_ocr=allow_ocr)

    if not detection.recognised:
        result = ExtractionResult(source_name=name, sha256=digest)
        result.issues.append(DataIssue("unrecognised_format", detection.reason))
        result.notes.append(f"detection scores: {detection.scores}")
        return result

    profile = detection.profile
    assert profile is not None
    kwargs: dict = {"county": county, "sha256": digest, "source_name": name}
    if profile.sniff_text is not None:
        kwargs["allow_ocr"] = allow_ocr

    result = profile.extract(path, **kwargs)
    result.profile = profile.name
    if result.document_text is None:
        result.document_text = detection.text
    result.notes.insert(0, f"{detection.reason} (confidence {detection.score:.2f})")
    for record in result.records:
        record.check_coverage()
    return result


def supported_formats() -> list[dict]:
    """Human-readable list for the admin upload screen."""
    return [
        {
            "profile": p.name,
            "document_kind": p.document_kind,
            "label": p.name.replace("_", " ").title(),
            "extensions": list(p.suffixes),
        }
        for p in PROFILES
    ]
