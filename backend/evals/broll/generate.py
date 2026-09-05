"""Regenerate the checked-in labeled B-roll cases and their tamper-evident manifest.

The corpus is synthetic and sanitized: no real provider was queried, and every candidate is
a plausible normalized result rather than a copy of anybody's metadata. What it measures is
the ranking, so each intent is offered a deliberately awkward pool — three genuinely
relevant clips by different authors, beside an irrelevant one, a culturally mismatched one,
an unsafe one, a repeat by an author already used, and one too wide to crop to vertical.

    uv run python -m evals.broll.generate
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from clipah.broll.evaluation import BROLL_MANIFEST_VERSION

CASES_DIRECTORY = Path(__file__).resolve().parent / "cases"
MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"

PEXELS_LICENSE = {
    "name": "Pexels License",
    "url": "https://www.pexels.com/license/",
    "attribution_required": False,
    "snapshot": "Pexels License. Free to use for commercial and non-commercial purposes.",
}
PIXABAY_LICENSE = {
    "name": "Pixabay Content License",
    "url": "https://pixabay.com/service/license-summary/",
    "attribution_required": False,
    "snapshot": "Pixabay Content License. Free to use without attribution.",
}

#: Each entry is one labeled intent: an Indonesian or English concept, the words a planner
#: would have produced for it, and the picture that genuinely illustrates it.
SUBJECTS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    (
        "id",
        "warung-queue",
        "a queue outside a small warung",
        "customers waiting to order at a street food stall",
        "a busy Jakarta street at dusk",
        "antrean warung makan",
        "street food stall queue",
    ),
    (
        "id",
        "ojek-helmet",
        "a rider fastening a green helmet",
        "a motorbike courier preparing to set off",
        "a city street with parked motorbikes",
        "ojek online helm hijau",
        "motorbike courier helmet",
    ),
    (
        "id",
        "pasar-scale",
        "a vendor weighing chillies on a scale",
        "hands placing chillies onto a market scale",
        "a wet market stall under strip lighting",
        "timbangan cabai pasar",
        "market vendor weighing chillies",
    ),
    (
        "id",
        "sawah-drone",
        "terraced rice fields at sunrise",
        "mist lifting off flooded paddies",
        "a hillside in rural Java",
        "sawah terasering pagi",
        "terraced rice fields sunrise",
    ),
    (
        "id",
        "qris-payment",
        "a phone scanning a QRIS code",
        "a customer paying by scanning a printed code",
        "a small shop counter",
        "pembayaran qris warung",
        "phone scanning payment code",
    ),
    (
        "id",
        "kereta-commuter",
        "commuters boarding a packed train",
        "passengers stepping through opening train doors",
        "a Jakarta commuter line platform",
        "penumpang krl commuter",
        "commuters boarding train",
    ),
    (
        "id",
        "kopi-pour",
        "coffee poured over ice",
        "a barista pouring espresso into a glass",
        "a small independent coffee shop",
        "kopi susu gula aren",
        "iced coffee pour",
    ),
    (
        "id",
        "banjir-street",
        "a flooded residential street",
        "residents wading through shallow floodwater",
        "a low-lying neighbourhood after rain",
        "banjir jalan perumahan",
        "flooded residential street",
    ),
    (
        "id",
        "batik-hand",
        "a hand drawing wax onto cloth",
        "an artisan applying hot wax with a canting",
        "a workshop bench with dyed fabric",
        "membatik tulis canting",
        "batik artisan wax",
    ),
    (
        "id",
        "umkm-packing",
        "a seller packing online orders",
        "hands taping parcels shut beside a laptop",
        "a home workspace stacked with boxes",
        "umkm packing pesanan online",
        "small seller packing parcels",
    ),
    (
        "en",
        "signup-form",
        "a shortened signup form",
        "a hand deleting fields from a form",
        "a laptop screen on a desk",
        "formulir pendaftaran singkat",
        "signup form on laptop",
    ),
    (
        "en",
        "activation-chart",
        "a rising activation chart",
        "a line climbing across a dashboard",
        "an analytics screen in a dim office",
        "grafik aktivasi naik",
        "rising analytics chart",
    ),
    (
        "en",
        "whiteboard-plan",
        "a team planning on a whiteboard",
        "a person drawing boxes and arrows",
        "a meeting room with glass walls",
        "rapat tim papan tulis",
        "team whiteboard planning",
    ),
    (
        "en",
        "server-rack",
        "a row of server racks",
        "status lights blinking in a cold aisle",
        "a data centre corridor",
        "ruang server rak",
        "server racks data centre",
    ),
    (
        "en",
        "handwritten-notes",
        "handwritten notes in a notebook",
        "a pen underlining a written line",
        "a wooden desk beside a window",
        "catatan tangan buku",
        "handwritten notebook pen",
    ),
    (
        "en",
        "warehouse-scan",
        "a worker scanning a barcode",
        "a handheld scanner reading a shelf label",
        "a warehouse aisle of stacked crates",
        "gudang pindai barcode",
        "warehouse barcode scanner",
    ),
    (
        "en",
        "video-editing",
        "a timeline being trimmed",
        "a cursor dragging a clip edge",
        "an editing suite with two monitors",
        "mengedit timeline video",
        "video editing timeline",
    ),
    (
        "en",
        "solar-panels",
        "solar panels on a rooftop",
        "sunlight moving across panel rows",
        "a flat roof above a city",
        "panel surya atap",
        "rooftop solar panels",
    ),
    (
        "en",
        "night-market",
        "food stalls lit at night",
        "steam rising from a grill",
        "a crowded night market lane",
        "pasar malam jajanan",
        "night market food stalls",
    ),
    (
        "en",
        "bank-queue",
        "a customer at a service counter",
        "a teller sliding a document across",
        "a bank branch interior",
        "antrean nasabah bank",
        "bank service counter",
    ),
    (
        "id",
        "sekolah-kelas",
        "students raising their hands",
        "a teacher pointing at a whiteboard",
        "a bright primary classroom",
        "siswa angkat tangan kelas",
        "students raising hands classroom",
    ),
    (
        "id",
        "nelayan-jaring",
        "a fisher hauling a net",
        "hands pulling a wet net over a gunwale",
        "a small boat at first light",
        "nelayan tarik jaring",
        "fisher hauling net",
    ),
    (
        "id",
        "konstruksi-helm",
        "a site worker checking plans",
        "a gloved hand tracing a drawing",
        "a half-built concrete frame",
        "pekerja konstruksi helm",
        "construction worker plans",
    ),
    (
        "id",
        "apotek-obat",
        "a pharmacist reading a label",
        "hands turning a medicine box",
        "a pharmacy counter with shelves behind",
        "apoteker baca label obat",
        "pharmacist reading label",
    ),
    (
        "id",
        "bengkel-motor",
        "a mechanic tightening a bolt",
        "a wrench turning on an engine",
        "a roadside motorbike workshop",
        "bengkel motor kunci pas",
        "mechanic tightening bolt",
    ),
    (
        "en",
        "podcast-mic",
        "a microphone on a boom arm",
        "a hand adjusting a pop filter",
        "a padded recording booth",
        "mikrofon podcast rekaman",
        "podcast microphone booth",
    ),
    (
        "en",
        "airport-board",
        "a departures board updating",
        "letters flipping to a new destination",
        "an airport concourse",
        "papan keberangkatan bandara",
        "airport departures board",
    ),
    (
        "en",
        "library-shelf",
        "a hand pulling a book from a shelf",
        "fingers tilting a spine outward",
        "a quiet library aisle",
        "rak buku perpustakaan",
        "library bookshelf hand",
    ),
    (
        "en",
        "kitchen-prep",
        "vegetables sliced on a board",
        "a knife working through spring onions",
        "a stainless commercial kitchen",
        "dapur potong sayur",
        "kitchen vegetable prep",
    ),
    (
        "en",
        "cyclist-commute",
        "a cyclist waiting at lights",
        "a foot resting on a pedal",
        "a city junction in morning traffic",
        "pesepeda lampu merah",
        "cyclist waiting traffic light",
    ),
)


def build_cases() -> list[dict[str, Any]]:
    """Build every labeled case deterministically, so a regeneration is reproducible."""
    return [_case(index, entry) for index, entry in enumerate(SUBJECTS)]


def _case(index: int, entry: tuple[str, str, str, str, str, str, str]) -> dict[str, Any]:
    """Build one intent and the deliberately awkward pool offered against it."""
    language, slug, subject, action, setting, term_id, term_en = entry
    case_id = f"{language}-{slug}"
    relevant_words = f"{subject} {action} {setting}"
    return {
        "case_id": case_id,
        "language": language,
        "intent": {
            "subject": subject,
            "action": action,
            "setting": setting,
            "mood": "observational",
            "search_terms_id": [term_id],
            "search_terms_en": [term_en],
            "portrait_suitable": True,
            "exclusions": ["corporate handshake"],
            "factual_risk_flags": [],
            "confidence": 0.85,
        },
        "candidates": [
            _candidate(
                case_id=case_id,
                suffix="relevant-a",
                author=f"Author {index}A",
                description=f"{relevant_words}, {term_id}",
                tags=_tags(term_id, term_en),
                query=term_id,
                label="relevant",
            ),
            _candidate(
                case_id=case_id,
                suffix="relevant-b",
                provider="pixabay",
                author=f"Author {index}B",
                description=f"{relevant_words} seen from the side",
                tags=_tags(term_en, term_id),
                query=term_en,
                label="relevant",
            ),
            _candidate(
                case_id=case_id,
                suffix="relevant-c",
                author=f"Author {index}G",
                description=f"{relevant_words}, closer in",
                tags=_tags(term_en, term_id),
                query=term_id,
                label="relevant",
            ),
            _candidate(
                case_id=case_id,
                suffix="irrelevant",
                author=f"Author {index}C",
                description="an empty beach at sunset with nobody on it",
                tags=("beach", "sunset", "sand"),
                query="beach sunset",
                label="irrelevant",
            ),
            _candidate(
                case_id=case_id,
                suffix="mismatched",
                author=f"Author {index}D",
                description="a corporate handshake in a glass lobby",
                tags=("handshake", "corporate", "lobby"),
                query="corporate handshake",
                label="culturally_mismatched",
            ),
            _candidate(
                case_id=case_id,
                suffix="unsafe",
                author=f"Author {index}E",
                description=f"{relevant_words}, flagged by safe search",
                tags=_tags(term_id, term_en),
                query=term_id,
                safe=False,
                label="unsafe",
            ),
            _candidate(
                case_id=case_id,
                suffix="repetitive",
                author=f"Author {index}A",
                description=f"{relevant_words}, another take",
                tags=_tags(term_id, term_en),
                query=term_id,
                label="repetitive",
            ),
            _candidate(
                case_id=case_id,
                suffix="panorama",
                author=f"Author {index}F",
                description=f"{relevant_words}, ultrawide",
                tags=_tags(term_id, term_en),
                query=term_id,
                width=5120,
                height=1080,
                label="portrait_incompatible",
            ),
        ],
    }


def _tags(*terms: str) -> tuple[str, ...]:
    """Split search terms into the individual words a provider would have tagged."""
    words: list[str] = []
    for term in terms:
        for word in term.split():
            if word not in words:
                words.append(word)
    return tuple(words)


def _candidate(
    *,
    case_id: str,
    suffix: str,
    label: str,
    author: str,
    description: str,
    tags: tuple[str, ...],
    query: str,
    provider: str = "pexels",
    width: int = 1080,
    height: int = 1920,
    safe: bool = True,
) -> dict[str, Any]:
    """Build one complete labeled candidate in the normalized shape retrieval produces."""
    asset_id = f"{case_id}-{suffix}"
    licence = PEXELS_LICENSE if provider == "pexels" else PIXABAY_LICENSE
    host = "www.pexels.com" if provider == "pexels" else "pixabay.com"
    return {
        "provider": provider,
        "provider_asset_id": asset_id,
        "media_kind": "video",
        "source_url": f"https://{host}/video/{asset_id}/",
        "download_url": f"https://{host}/download/{asset_id}.mp4",
        "author": author,
        "author_url": f"https://{host}/@{author.replace(' ', '-').lower()}",
        "license": dict(licence),
        "width": width,
        "height": height,
        "duration_ms": 9_000,
        "attribution_text": f"Video by {author} on {provider.title()}",
        "query": query,
        "safe": safe,
        "description": description,
        "tags": list(tags),
        "label": label,
    }


def write() -> None:
    """Write every case file and the manifest that records its digest."""
    CASES_DIRECTORY.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for case in build_cases():
        path = CASES_DIRECTORY / f"{case['case_id']}.json"
        body = json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path.write_text(body, encoding="utf-8")
        entries.append(
            {
                "case_id": case["case_id"],
                "language": case["language"],
                "path": f"cases/{path.name}",
                "sha256": sha256(body.encode("utf-8")).hexdigest(),
            }
        )
    manifest = {
        "version": BROLL_MANIFEST_VERSION,
        "description": (
            "Sanitized synthetic B-roll intents and labeled candidate pools. No provider "
            "was queried to produce them and no real provider metadata is reproduced."
        ),
        "cases": entries,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    write()
