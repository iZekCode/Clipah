"""Regenerate the checked-in evaluation fixtures and their tamper-evident manifests.

The fixtures are sanitized: no real creator audio, no personal data, and no provider output.
Each case is composed deterministically from a small pool of hand-written sentences, so the
same command always produces byte-identical files and a reviewer can read a case end to end.
Run it from ``backend/``::

    uv run python evals/generate_fixtures.py

Every generated case is labeled the way a human reviewer labels one: sentence-aligned
narrative units, an explicit acceptable-overlap range per approved clip, an explicitly risky
cut whose publication needs a context warning, and the named entities transcription must keep.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

WORD_BASE_MS = 180
WORD_PER_CHARACTER_MS = 22
WORD_GAP_MS = 60
SENTENCE_GAP_MS = 320
SILENCE_GAP_MS = 1_500
SILENCE_EVERY = 6
SPEAKER_TURN_EVERY = 4
SPEAKERS = ("speaker_0", "speaker_1")

HIGHLIGHT_SENTENCES = 96
TRANSCRIPTION_SENTENCES = 22
APPROVED_TARGET_MS = 45_000
APPROVED_PER_CASE = 4
RISKY_POSITION = 2
MIN_OVERLAP = 0.5
MAX_OVERLAP = 1.0

HIGHLIGHT_MANIFEST_VERSION = "highlight-eval-cases/1"
TRANSCRIPTION_MANIFEST_VERSION = "transcription-eval-cases/1"

# The accumulated source audio behind the labels. Text-only fixtures carry none, and the
# manifest states the gap so nobody mistakes a green report for a frozen provider choice.
AUDIO_SECONDS_PRESENT = 0
AUDIO_SECONDS_BEFORE_PROVIDER_FREEZE = 7_200
AUDIO_SECONDS_BEFORE_PUBLIC_LAUNCH = 18_000


@dataclass(frozen=True, slots=True)
class Topic:
    """One case's subject, expressed through the fillers its sentences vary over."""

    case_id: str
    language: str
    title: str
    entities: tuple[str, ...]
    subjects: tuple[str, ...]
    numbers: tuple[str, ...]


ENGLISH_TEMPLATES: tuple[str, ...] = (
    "We started {subject} with {number} paying customers and almost no process.",
    "The first month taught us that {subject} breaks long before the team notices.",
    "Everyone told us to hire faster, so we hired {number} people in one quarter.",
    "That decision cost us {number} weeks of onboarding we had not planned for.",
    "Here is the part nobody writes about when they describe {subject}.",
    "Our churn moved from {number} percent to something we could finally explain.",
    "We rebuilt the whole intake flow around {subject} and measured it weekly.",
    "It took {number} attempts before the numbers stopped contradicting each other.",
    "The lesson is simple, and it applies to {subject} in any market.",
    "If you cannot describe {subject} in one sentence, you do not understand it yet.",
    "We wrote {number} pages of documentation and deleted most of them.",
    "The team that owned {subject} was also the team answering support tickets.",
    "That overlap is where the real feedback about {subject} came from.",
    "By the end of the year {subject} was carrying {number} percent of revenue.",
    "I would not repeat the middle six months of {subject} for any amount of money.",
    "What changed was not the strategy, it was how often we looked at {subject}.",
)

INDONESIAN_TEMPLATES: tuple[str, ...] = (
    "Kami memulai {subject} dengan {number} pelanggan pertama dan tanpa proses apa pun.",
    "Bulan pertama mengajarkan bahwa {subject} rusak jauh sebelum tim menyadarinya.",
    "Semua orang menyuruh kami merekrut cepat, jadi kami menambah {number} orang.",
    "Keputusan itu memakan {number} minggu pelatihan yang tidak pernah kami rencanakan.",
    "Ini bagian yang jarang ditulis orang ketika membahas {subject}.",
    "Angka berhenti berlangganan turun dari {number} persen menjadi sesuatu yang jelas.",
    "Kami membangun ulang alur masuk di sekitar {subject} dan mengukurnya tiap minggu.",
    "Butuh {number} percobaan sebelum semua angkanya berhenti saling bertentangan.",
    "Pelajarannya sederhana dan berlaku untuk {subject} di pasar mana pun.",
    "Kalau {subject} belum bisa dijelaskan satu kalimat, berarti belum dipahami.",
    "Kami menulis {number} halaman dokumentasi lalu menghapus sebagian besarnya.",
    "Tim yang memegang {subject} juga tim yang menjawab keluhan pengguna.",
    "Justru dari irisan itulah masukan sebenarnya tentang {subject} datang.",
    "Di akhir tahun {subject} menyumbang {number} persen dari pendapatan kami.",
    "Saya tidak mau mengulang enam bulan tersulit dari {subject} dengan bayaran apa pun.",
    "Yang berubah bukan strateginya, melainkan seberapa sering kami melihat {subject}.",
)

MIXED_TEMPLATES: tuple[str, ...] = (
    "Kami mulai {subject} dengan {number} early customers dan hampir tanpa process.",
    "Month pertama mengajarkan bahwa {subject} broken sebelum tim aware.",
    "Semua orang bilang hire faster, jadi kami hire {number} orang dalam satu quarter.",
    "Decision itu costing kami {number} minggu onboarding yang tidak kami rencanakan.",
    "Ini bagian yang nobody writes about kalau bahas {subject}.",
    "Churn kami turun dari {number} persen ke angka yang akhirnya bisa kami explain.",
    "Kami rebuild seluruh intake flow di sekitar {subject} dan review-nya weekly.",
    "Butuh {number} attempts sebelum semua metric berhenti saling contradict.",
    "Lesson-nya simple dan berlaku untuk {subject} di market mana pun.",
    "Kalau {subject} belum bisa dijelaskan in one sentence, berarti belum paham.",
    "Kami menulis {number} halaman docs lalu deleted sebagian besar isinya.",
    "Tim yang own {subject} juga tim yang handle support tickets setiap hari.",
    "Overlap itulah sumber real feedback tentang {subject} buat kami.",
    "Akhir tahun {subject} carrying {number} persen dari total revenue.",
    "Saya tidak akan repeat six months tersulit dari {subject} untuk uang berapa pun.",
    "Yang berubah bukan strategy-nya, tapi seberapa sering kami lihat {subject}.",
)

TEMPLATES: dict[str, tuple[str, ...]] = {
    "en": ENGLISH_TEMPLATES,
    "id": INDONESIAN_TEMPLATES,
    "mixed": MIXED_TEMPLATES,
}

TOPICS: tuple[Topic, ...] = (
    Topic(
        case_id="en-onboarding-rebuild",
        language="en",
        title="Rebuilding onboarding after the first hiring wave",
        entities=("Jakarta", "Series A", "Northwind"),
        subjects=("onboarding", "the Northwind rollout", "our activation funnel"),
        numbers=("twelve", "forty", "three"),
    ),
    Topic(
        case_id="en-pricing-experiment",
        language="en",
        title="A pricing experiment that changed the roadmap",
        entities=("Bandung", "Northwind", "Q3"),
        subjects=("pricing", "the trial experiment", "our enterprise tier"),
        numbers=("nine", "twenty", "five"),
    ),
    Topic(
        case_id="en-support-loop",
        language="en",
        title="Why the support queue became the research team",
        entities=("Surabaya", "Helpdesk", "Northwind"),
        subjects=("support", "the escalation loop", "our response policy"),
        numbers=("seven", "thirty", "two"),
    ),
    Topic(
        case_id="en-remote-team",
        language="en",
        title="Running a remote team across two time zones",
        entities=("Singapore", "Jakarta", "Northwind"),
        subjects=("remote work", "the handover ritual", "our weekly review"),
        numbers=("four", "sixty", "eleven"),
    ),
    Topic(
        case_id="en-content-engine",
        language="en",
        title="Turning one long interview into a content engine",
        entities=("YouTube", "Northwind", "Jakarta"),
        subjects=("content", "the interview series", "our editing pipeline"),
        numbers=("six", "eighty", "fifteen"),
    ),
    Topic(
        case_id="id-onboarding-rebuild",
        language="id",
        title="Membangun ulang proses onboarding setelah rekrutmen besar",
        entities=("Jakarta", "Series A", "Northwind"),
        subjects=("onboarding", "peluncuran Northwind", "alur aktivasi kami"),
        numbers=("dua belas", "empat puluh", "tiga"),
    ),
    Topic(
        case_id="id-pricing-experiment",
        language="id",
        title="Eksperimen harga yang mengubah peta jalan produk",
        entities=("Bandung", "Northwind", "kuartal ketiga"),
        subjects=("penetapan harga", "eksperimen uji coba", "paket perusahaan"),
        numbers=("sembilan", "dua puluh", "lima"),
    ),
    Topic(
        case_id="id-support-loop",
        language="id",
        title="Ketika tim dukungan menjadi tim riset",
        entities=("Surabaya", "Helpdesk", "Northwind"),
        subjects=("dukungan pengguna", "alur eskalasi", "kebijakan respons kami"),
        numbers=("tujuh", "tiga puluh", "dua"),
    ),
    Topic(
        case_id="id-remote-team",
        language="id",
        title="Mengelola tim jarak jauh di dua zona waktu",
        entities=("Singapura", "Jakarta", "Northwind"),
        subjects=("kerja jarak jauh", "ritual serah terima", "tinjauan mingguan kami"),
        numbers=("empat", "enam puluh", "sebelas"),
    ),
    Topic(
        case_id="id-content-engine",
        language="id",
        title="Mengubah satu wawancara panjang menjadi mesin konten",
        entities=("YouTube", "Northwind", "Jakarta"),
        subjects=("konten", "seri wawancara", "alur penyuntingan kami"),
        numbers=("enam", "delapan puluh", "lima belas"),
    ),
    Topic(
        case_id="mixed-onboarding-rebuild",
        language="mixed",
        title="Rebuild onboarding setelah hiring wave pertama",
        entities=("Jakarta", "Series A", "Northwind"),
        subjects=("onboarding", "rollout Northwind", "activation funnel kami"),
        numbers=("dua belas", "empat puluh", "tiga"),
    ),
    Topic(
        case_id="mixed-pricing-experiment",
        language="mixed",
        title="Pricing experiment yang mengubah roadmap",
        entities=("Bandung", "Northwind", "Q3"),
        subjects=("pricing", "trial experiment", "enterprise tier kami"),
        numbers=("sembilan", "dua puluh", "lima"),
    ),
    Topic(
        case_id="mixed-support-loop",
        language="mixed",
        title="Support queue yang berubah jadi tim riset",
        entities=("Surabaya", "Helpdesk", "Northwind"),
        subjects=("support", "escalation loop", "response policy kami"),
        numbers=("tujuh", "tiga puluh", "dua"),
    ),
    Topic(
        case_id="mixed-remote-team",
        language="mixed",
        title="Remote team across dua zona waktu",
        entities=("Singapura", "Jakarta", "Northwind"),
        subjects=("remote work", "handover ritual", "weekly review kami"),
        numbers=("empat", "enam puluh", "sebelas"),
    ),
    Topic(
        case_id="mixed-content-engine",
        language="mixed",
        title="Satu interview panjang jadi content engine",
        entities=("YouTube", "Northwind", "Jakarta"),
        subjects=("content", "interview series", "editing pipeline kami"),
        numbers=("enam", "delapan puluh", "lima belas"),
    ),
)


@dataclass(frozen=True, slots=True)
class GeneratedWord:
    """One generated word with the timing and speaker a fixture records."""

    word_id: str
    text: str
    punctuation: str
    start_ms: int
    end_ms: int
    confidence: float
    speaker: str
    sentence: int


def sentences(topic: Topic, count: int) -> list[str]:
    """Compose one case's sentences deterministically from its topic and templates."""
    templates = TEMPLATES[topic.language]
    composed: list[str] = []
    for position in range(count):
        template = templates[position % len(templates)]
        composed.append(
            template.format(
                subject=topic.subjects[position % len(topic.subjects)],
                number=topic.numbers[(position // len(topic.subjects)) % len(topic.numbers)],
            )
        )
    entity_sentence = _entity_sentence(topic)
    for position in range(0, count, 8):
        composed[position] = entity_sentence
    return composed


ENTITY_SENTENCES: dict[str, str] = {
    "en": "We recorded this in {first} right after {second} closed with {third}.",
    "id": "Kami merekam ini di {first} tepat setelah {second} selesai bersama {third}.",
    "mixed": "Kami record ini di {first} tepat setelah {second} closed bareng {third}.",
}


def _entity_sentence(topic: Topic) -> str:
    """Write the one sentence that carries every named entity transcription must keep."""
    first, second, third = topic.entities
    return ENTITY_SENTENCES[topic.language].format(first=first, second=second, third=third)


def words_for(topic: Topic, count: int) -> list[GeneratedWord]:
    """Lay one case's sentences onto a deterministic clock with alternating speakers."""
    generated: list[GeneratedWord] = []
    clock = 0
    for index, sentence in enumerate(sentences(topic, count)):
        speaker = SPEAKERS[(index // SPEAKER_TURN_EVERY) % len(SPEAKERS)]
        tokens = sentence.split()
        for position, token in enumerate(tokens):
            text = token.rstrip(".,!?")
            punctuation = token[len(text) :]
            duration = WORD_BASE_MS + WORD_PER_CHARACTER_MS * len(text)
            generated.append(
                GeneratedWord(
                    word_id=f"w{len(generated) + 1:06d}",
                    text=text,
                    punctuation=punctuation,
                    start_ms=clock,
                    end_ms=clock + duration,
                    confidence=0.92,
                    speaker=speaker,
                    sentence=index,
                )
            )
            clock += duration + (WORD_GAP_MS if position + 1 < len(tokens) else 0)
        clock += SILENCE_GAP_MS if (index + 1) % SILENCE_EVERY == 0 else SENTENCE_GAP_MS
    return generated


def approved_clips(generated: Sequence[GeneratedWord]) -> list[dict[str, Any]]:
    """Label the sentence-aligned narrative units a reviewer would approve."""
    spans = _spans(generated)[:APPROVED_PER_CASE]
    return [
        {
            "label": f"narrative-unit-{position + 1}",
            "start_word_id": span[0].word_id,
            "end_word_id": span[-1].word_id,
            "min_overlap": MIN_OVERLAP,
            "max_overlap": MAX_OVERLAP,
            "risky": position == RISKY_POSITION,
            "context_warnings": (
                ["opens on a pronoun that refers to the previous answer"]
                if position == RISKY_POSITION
                else []
            ),
        }
        for position, span in enumerate(spans)
    ]


def _spans(generated: Sequence[GeneratedWord]) -> list[list[GeneratedWord]]:
    """Cut the words into reviewable, sentence-aligned units of roughly equal length."""
    spans: list[list[GeneratedWord]] = []
    current: list[GeneratedWord] = []
    for word in generated:
        current.append(word)
        elapsed = word.end_ms - current[0].start_ms
        if elapsed >= APPROVED_TARGET_MS and word.punctuation in {".", "!", "?"}:
            spans.append(current)
            current = []
    return spans


def highlight_case(topic: Topic) -> dict[str, Any]:
    """Build one labeled highlight case document."""
    generated = words_for(topic, HIGHLIGHT_SENTENCES)
    return {
        "case_id": topic.case_id,
        "language": topic.language,
        "title": topic.title,
        "source_seconds": generated[-1].end_ms // 1_000,
        "named_entities": list(topic.entities),
        "approved_clips": approved_clips(generated),
        "words": [
            {
                "word_id": word.word_id,
                "text": word.text,
                "punctuation": word.punctuation,
                "start_ms": word.start_ms,
                "end_ms": word.end_ms,
                "confidence": word.confidence,
                "speaker": word.speaker,
            }
            for word in generated
        ],
    }


def transcription_case(topic: Topic) -> dict[str, Any]:
    """Build one labeled transcription case document."""
    generated = words_for(topic, TRANSCRIPTION_SENTENCES)
    return {
        "case_id": topic.case_id,
        "language": topic.language,
        "title": topic.title,
        "duration_ms": generated[-1].end_ms,
        "audio_key": None,
        "named_entities": list(topic.entities),
        "reference_words": [
            {
                "text": word.text,
                "start_ms": word.start_ms,
                "end_ms": word.end_ms,
                "speaker": word.speaker,
            }
            for word in generated
        ],
    }


def write_set(
    directory: Path,
    *,
    manifest_version: str,
    documents: Sequence[dict[str, Any]],
    duration_field: str,
) -> None:
    """Write every case file and the manifest that records its digest."""
    cases_directory = directory / "cases"
    cases_directory.mkdir(parents=True, exist_ok=True)
    for stale in sorted(cases_directory.glob("*.json")):
        stale.unlink()
    entries: list[dict[str, Any]] = []
    for document in documents:
        relative = f"cases/{document['case_id']}.json"
        payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        (directory / relative).write_text(payload, encoding="utf-8")
        entries.append(
            {
                "case_id": document["case_id"],
                "language": document["language"],
                "path": relative,
                "sha256": sha256(payload.encode("utf-8")).hexdigest(),
                duration_field: document[duration_field],
            }
        )
    manifest = {
        "manifest_version": manifest_version,
        "generated_by": "evals/generate_fixtures.py",
        "audio_seconds_present": AUDIO_SECONDS_PRESENT,
        "audio_seconds_before_provider_freeze": AUDIO_SECONDS_BEFORE_PROVIDER_FREEZE,
        "audio_seconds_before_public_launch": AUDIO_SECONDS_BEFORE_PUBLIC_LAUNCH,
        "cases": entries,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Regenerate both checked-in evaluation case sets."""
    root = Path(__file__).resolve().parent
    write_set(
        root / "highlights",
        manifest_version=HIGHLIGHT_MANIFEST_VERSION,
        documents=[highlight_case(topic) for topic in TOPICS],
        duration_field="source_seconds",
    )
    write_set(
        root / "transcription",
        manifest_version=TRANSCRIPTION_MANIFEST_VERSION,
        documents=[transcription_case(topic) for topic in TOPICS],
        duration_field="duration_ms",
    )


if __name__ == "__main__":
    main()
