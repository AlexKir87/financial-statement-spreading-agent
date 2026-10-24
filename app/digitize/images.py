"""Page images for the vision route: the scan's embedded page image, read with pypdf.

No renderer is needed because a scanned PDF page is an image. A page with no embedded image,
or one in a format a vision model does not take, is unreadable here, not guessed at.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

import pypdf

from app.digitize.interface import PageUnreadable
from app.tools.model_client import ImagePart

MAX_IMAGE_BYTES: Final = 20 * 1024 * 1024
_MIME: Final = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


class PageImageSource(Protocol):
    def page_image(self, doc_id: str, page: int) -> ImagePart: ...


@dataclass(frozen=True)
class PdfPageImages:
    """Largest embedded image of each page of the scanned PDFs in `paths` (doc_id to file)."""

    paths: Mapping[str, Path]

    def page_image(self, doc_id: str, page: int) -> ImagePart:
        path = self.paths.get(doc_id)
        if path is None:
            raise PageUnreadable("document is not available to the digitizer")
        try:
            reader = pypdf.PdfReader(path)
            if not 1 <= page <= len(reader.pages):
                raise PageUnreadable("page is outside the document")
            images = list(reader.pages[page - 1].images)
        except PageUnreadable:
            raise
        except Exception:  # pypdf raises many types on damaged files; the cause stays out of logs
            raise PageUnreadable("page image could not be extracted") from None
        usable = [
            (img, _MIME[Path(img.name).suffix.lower()])
            for img in images
            if Path(img.name).suffix.lower() in _MIME and len(img.data) <= MAX_IMAGE_BYTES
        ]
        if not usable:
            raise PageUnreadable("page has no embedded jpeg or png image")
        best, mime = max(usable, key=lambda pair: len(pair[0].data))
        return ImagePart(data=best.data, mime=mime)
