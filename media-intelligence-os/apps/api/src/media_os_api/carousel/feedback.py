"""The owner's review notes, fed back to the writer so the next drafts avoid the same problems.

When the owner rejects a carousel with a note ("too salesy", "wrong honorific for a Muslim
wedding"), that note is saved on the REJECTED state transition. Before writing a new carousel,
the brand's most recent notes are put at the top of the prompt as standing instructions.

Notes come only from the signed-in owner, so they're trusted instructions, but they're still
trimmed and capped so an accidental paste can't swamp the prompt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.db.models import ContentPackage, ContentStateTransition

DEFAULT_REASON = "Rejected in review"  # stored when the owner leaves the note empty
PROMPT_NOTES = 8
NOTE_MAX = 240


@dataclass(frozen=True)
class OwnerNote:
    title: str
    note: str


def owner_notes(
    session: Session, brand_id: uuid.UUID, limit: int = PROMPT_NOTES
) -> list[OwnerNote]:
    """The brand's latest rejection notes, newest first (empty notes are skipped)."""
    rows = session.execute(
        select(ContentStateTransition.reason, ContentPackage.title, ContentPackage.hook)
        .join(ContentPackage, ContentPackage.id == ContentStateTransition.content_package_id)
        .where(
            ContentStateTransition.brand_profile_id == brand_id,
            ContentStateTransition.to_status == "REJECTED",
            ContentPackage.format == "CAROUSEL",
            ContentStateTransition.reason.is_not(None),
            ContentStateTransition.reason != DEFAULT_REASON,
        )
        .order_by(ContentStateTransition.created_at.desc())
        .limit(limit)
    ).all()
    out = []
    for reason, title, hook in rows:
        note = " ".join(str(reason or "").split())[:NOTE_MAX]
        if note:
            label = " ".join(str(title or hook or "a draft").replace("*", "").split())[:100]
            out.append(OwnerNote(title=label, note=note))
    return out


def notes_block(notes: list[OwnerNote]) -> str:
    if not notes:
        return ""
    lines = "\n".join(f"- On '{n.title}': {n.note}" for n in notes)
    return (
        "THE OWNER REJECTED RECENT DRAFTS FOR THESE REASONS. Treat each one as a standing rule "
        "and make sure this draft doesn't repeat the problem:\n"
        f"{lines}\n\n"
    )
