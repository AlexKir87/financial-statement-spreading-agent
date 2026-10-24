"""Vision route: one page image per model call, cells returned as printed with a confidence.

Route chosen provisionally from the spike (the digitizer spike notes): it read 15 of 15
cells on one clean synthetic page and has not been measured on a real scan. The model id comes
from settings (`SPREAD_MODEL`). The route returns text only; amounts are parsed in code later and
the extraction node drops any line whose label and value are not printed on one row of this text.
Bounding boxes are not requested (a vision model's boxes are unmeasured), so `Cell.bbox` is None.
"""

from app.digitize.images import PageImageSource
from app.digitize.interface import PageUnreadable
from app.prompts import load_prompt
from app.state import Cell, DigitizedPage, Usage
from app.tools.model_client import StructuredCaller
from app.tools.schemas import PageRead

ROUTE_NAME = "vision"


def _no_duplicate_positions(read: PageRead) -> None:
    seen = {(c.row, c.col) for c in read.cells}
    if len(seen) != len(read.cells):
        raise ValueError("two cells share the same row and col")


class VisionRoute:
    name = ROUTE_NAME

    def __init__(self, images: PageImageSource, caller: StructuredCaller) -> None:
        self._images = images
        self._caller = caller
        # The version records the model and prompt that read the page (Source_Trace column).
        self.version = f"{caller.model}+prompt-{caller.prompt_version}"

    @property
    def usage(self) -> Usage:
        return self._caller.usage

    def read_page(self, doc_id: str, page: int) -> DigitizedPage:
        image = self._images.page_image(doc_id, page)
        read = self._caller.call(
            system=load_prompt("digitize_page", self._caller.prompt_version),
            user="Copy the text and table cells of the attached page.",
            schema=PageRead,
            check=_no_duplicate_positions,
            images=(image,),
        )
        if not read.cells:
            raise PageUnreadable("no text read from the page")
        cells = tuple(
            Cell(row=c.row, col=c.col, text=c.text, confidence=c.confidence)
            for c in sorted(read.cells, key=lambda c: (c.row, c.col))
        )
        return DigitizedPage(
            doc_id=doc_id,
            page=page,
            cells=cells,
            digitizer_name=self.name,
            digitizer_version=self.version,
        )
