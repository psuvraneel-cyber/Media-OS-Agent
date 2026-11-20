"""The owner's review notes, fed back to the writer so the next drafts avoid the same problems.

Two kinds of note, both saved on a state transition:
* a rejection with a note ("too salesy", "wrong honorific for a Muslim wedding");
* the owner's own edits to a draft ("slide 1 headline_en 'X' → 'Y'"), which show the wording
  they actually want.

Before writing a new carousel, the brand's most recent notes are put at the top of the prompt as
standing instructions. Notes come only from the signed-in owner, so they're trusted
instructions, but they're still trimmed and capped so an accidental paste can't swamp the prompt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from media_os_api.db.models import ContentPackage, ContentStateTransition

DEFAULT_REASON = "Rejected in review"  # stored when the owner leaves the note empty
EDIT_PREFIX = "Owner edit: "  # carousel.service.EDIT_REASON_PREFIX
PROMPT_NOTES = 8
NOTE_MAX = 260


@dataclass(frozen=True)
class OwnerNote:
    title: str
    note: str


def owner_notes(
    session: Session, brand_id: uuid.UUID, limit: int = PROMPT_NOTES
) -> list[OwnerNote]:
    """The brand's latest rejection notes and edits, newest first (empty notes skipped)."""
    rows = session.execute(
        select(ContentStateTransition.reason, ContentPackage.title, ContentPackage.hook)
        .join(ContentPackage, ContentPackage.id == ContentStateTransition.content_package_id)
        .where(
            ContentStateTransition.brand_profile_id == brand_id,
            ContentPackage.format == "CAROUSEL",
            or_(
                and_(
                    ContentStateTransition.to_status == "REJECTED",
                    ContentStateTransition.reason.is_not(None),
                    ContentStateTransition.reason != DEFAULT_REASON,
                ),
                ContentStateTransition.reason.startswith(EDIT_PREFIX),
            ),
        )
        .order_by(ContentStateTransition.created_at.desc())
        .limit(limit)
    ).all()
    out = []
    for reason, title, hook in rows:
        text = " ".join(str(reason or "").split())
        if not text:
            continue
        if text.startswith(EDIT_PREFIX):
            text = "the owner corrected it: " + text[len(EDIT_PREFIX) :]
        else:
            text = "rejected: " + text
        label = " ".join(str(title or hook or "a draft").replace("*", "").split())[:100]
        out.append(OwnerNote(title=label, note=text[:NOTE_MAX]))
    return out


def notes_block(notes: list[OwnerNote]) -> str:
    if not notes:
        return ""
    lines = "\n".join(f"- On '{n.title}': {n.note}" for n in notes)
    return (
        "THE OWNER'S NOTES ON RECENT DRAFTS (rejections, and corrections they made by hand). "
        "Treat each one as a standing rule: don't repeat a rejected problem, and write the way "
        "the corrections show:\n"
        f"{lines}\n\n"
    )
