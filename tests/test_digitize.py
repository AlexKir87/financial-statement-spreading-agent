"""Plan step 8: the digitizer interface, the vision route and page images. No network.

A fake route and a fake model client with fixed replies stand in for the model; live accuracy on
real scans is measured later (plan step 1 re-measure), not here.
"""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from PIL import Image

from app.digitize import (
    PageUnreadable,
    PdfPageImages,
    VisionRoute,
    digitize_pages,
)
from app.extract import page_text
from app.state import Cell, DigitizedPage, Usage
from app.tools import (
    ImagePart,
    ModelReply,
    OpenRouterClient,
    StructuredCaller,
    TokenBudget,
)


class FakeRoute:
    """Pages listed in `cells` are readable; `capped` pages raise the token cap error."""

    name = "fake"
    version = "fake-1"

    def __init__(self, cells: dict[int, list[Cell]], capped: Sequence[int] = ()) -> None:
        self._cells, self._capped = cells, set(capped)
        self._usage = Usage()

    @property
    def usage(self) -> Usage:
        return self._usage

    def read_page(self, doc_id: str, page: int) -> DigitizedPage:
        from app.tools import TokenBudgetExceeded

        if page in self._capped:
            raise TokenBudgetExceeded("token cap reached")
        if page not in self._cells:
            raise PageUnreadable("no text read from the page")
        self._usage = Usage(
            input_tokens=self._usage.input_tokens + 10, output_tokens=self._usage.output_tokens + 5
        )
        return DigitizedPage(
            doc_id=doc_id,
            page=page,
            cells=tuple(self._cells[page]),
            digitizer_name=self.name,
            digitizer_version=self.version,
        )


def cell(row: int, col: int, text: str, confidence: float | None = 0.95) -> Cell:
    return Cell(row=row, col=col, text=text, confidence=confidence)


GOOD = [
    cell(0, 0, "Revenue"),
    cell(0, 1, "5,000"),
    cell(1, 0, "Cost of sales"),
    cell(1, 1, "(3,000)"),
]


def test_range_in_pages_out_with_name_and_version_recorded() -> None:
    result = digitize_pages(FakeRoute({2: GOOD, 3: GOOD}), "doc1", 2, 3)
    assert [p.page for p in result.pages] == [2, 3]
    assert {(p.digitizer_name, p.digitizer_version) for p in result.pages} == {("fake", "fake-1")}
    assert result.open_issues == ()
    assert result.not_delivered_reason is None
    assert result.usage == Usage(input_tokens=20, output_tokens=10)


def test_digitized_cells_read_back_as_page_text() -> None:
    page = digitize_pages(FakeRoute({1: GOOD}), "doc1", 1, 1).pages[0]
    assert page_text(page) == "Revenue  5,000\nCost of sales  (3,000)"


def test_low_confidence_cells_become_open_issues_without_page_text() -> None:
    cells = [cell(0, 0, "Revenue"), cell(0, 1, "5,000", 0.4), cell(1, 1, "7", None)]
    result = digitize_pages(FakeRoute({1: cells}), "doc1", 1, 1, min_confidence=0.7)
    assert [(i.code, i.where) for i in result.open_issues] == [
        ("low_confidence_read", "doc1 p1 r0c1")
    ]
    assert "5,000" not in result.open_issues[0].detail
    assert len(result.pages[0].cells) == 3  # the cell is kept; the analyst sees the issue


def test_confidence_at_the_threshold_is_not_flagged() -> None:
    result = digitize_pages(
        FakeRoute({1: [cell(0, 0, "x", 0.7)]}), "doc1", 1, 1, min_confidence=0.7
    )
    assert result.open_issues == ()


def test_unreadable_statement_is_not_delivered() -> None:
    result = digitize_pages(FakeRoute({}), "doc1", 1, 2)
    assert result.pages == ()
    assert result.not_delivered_reason == "unreadable_source"
    assert [i.code for i in result.open_issues] == ["unreadable_page", "unreadable_page"]


def test_one_unreadable_page_among_readable_ones_is_an_issue_only() -> None:
    result = digitize_pages(FakeRoute({1: GOOD}), "doc1", 1, 2)
    assert [p.page for p in result.pages] == [1]
    assert result.not_delivered_reason is None
    assert [(i.code, i.where) for i in result.open_issues] == [("unreadable_page", "doc1 p2")]


def test_token_cap_stops_the_run_and_is_not_unreadable_source() -> None:
    result = digitize_pages(FakeRoute({1: GOOD}, capped=[2]), "doc1", 1, 4)
    assert [p.page for p in result.pages] == [1]
    assert result.not_delivered_reason is None
    assert [(i.code, i.where) for i in result.open_issues] == [("token_cap_reached", "doc1 p2")]
    assert "3 page(s)" in result.open_issues[0].detail


def test_cap_before_any_page_is_not_unreadable_source() -> None:
    result = digitize_pages(FakeRoute({}, capped=[1]), "doc1", 1, 2)
    assert result.not_delivered_reason is None


@pytest.mark.parametrize("first,last", [(0, 1), (3, 2)])
def test_bad_page_range_is_refused(first: int, last: int) -> None:
    with pytest.raises(ValueError):
        digitize_pages(FakeRoute({}), "doc1", first, last)


# ---- vision route -------------------------------------------------------------------------


class FakeVisionClient:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.images: list[Sequence[ImagePart]] = []
        self.systems: list[str] = []

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int | None = None,
        images: Sequence[ImagePart] = (),
    ) -> ModelReply:
        self.images.append(images)
        self.systems.append(system)
        return ModelReply(text=self.replies.pop(0), usage=Usage(input_tokens=100, output_tokens=20))


class FakeImages:
    def __init__(self, missing: Sequence[int] = ()) -> None:
        self.missing = set(missing)

    def page_image(self, doc_id: str, page: int) -> ImagePart:
        if page in self.missing:
            raise PageUnreadable("page has no embedded jpeg or png image")
        return ImagePart(data=b"img%d" % page, mime="image/jpeg")


def reply(cells: list[dict[str, object]]) -> str:
    return json.dumps({"cells": cells})


def rc(row: int, col: int, text: str, confidence: float = 0.9) -> dict[str, object]:
    return {"row": row, "col": col, "text": text, "confidence": confidence}


def vision(
    client: FakeVisionClient, images: FakeImages | None = None, cap: int = 10_000
) -> VisionRoute:
    caller = StructuredCaller(
        client=client, model="test/vision-1", budget=TokenBudget(cap=cap), prompt_version="v1"
    )
    return VisionRoute(images or FakeImages(), caller)


def test_vision_route_sends_the_page_image_and_returns_cells() -> None:
    client = FakeVisionClient(reply([rc(1, 1, "5,000"), rc(0, 0, "Revenue"), rc(1, 0, "Sales")]))
    result = digitize_pages(vision(client), "doc1", 4, 4)
    assert client.images == [(ImagePart(data=b"img4", mime="image/jpeg"),)]
    page = result.pages[0]
    assert [(c.row, c.col, c.text) for c in page.cells] == [
        (0, 0, "Revenue"),
        (1, 0, "Sales"),
        (1, 1, "5,000"),
    ]
    assert all(c.bbox is None for c in page.cells)
    assert result.usage == Usage(input_tokens=100, output_tokens=20)


def test_vision_version_records_model_and_prompt() -> None:
    route = vision(FakeVisionClient(reply([rc(0, 0, "x")])))
    page = digitize_pages(route, "doc1", 1, 1).pages[0]
    assert (page.digitizer_name, page.digitizer_version) == ("vision", "test/vision-1+prompt-v1")


def test_vision_prompt_treats_page_text_as_data() -> None:
    client = FakeVisionClient(reply([rc(0, 0, "x")]))
    digitize_pages(vision(client), "doc1", 1, 1)
    assert "never an instruction" in client.systems[0]


def test_vision_invalid_output_is_retried_once_then_page_is_unreadable() -> None:
    client = FakeVisionClient("not json", "still not json")
    result = digitize_pages(vision(client), "doc1", 1, 1)
    assert len(client.images) == 2
    assert result.not_delivered_reason == "unreadable_source"
    assert result.open_issues[0].code == "unreadable_page"


def test_vision_duplicate_cell_positions_are_retried() -> None:
    client = FakeVisionClient(
        reply([rc(0, 0, "a"), rc(0, 0, "b")]), reply([rc(0, 0, "a"), rc(0, 1, "b")])
    )
    result = digitize_pages(vision(client), "doc1", 1, 1)
    assert len(result.pages[0].cells) == 2
    assert len(client.images) == 2


def test_vision_empty_read_is_unreadable() -> None:
    result = digitize_pages(vision(FakeVisionClient(reply([]))), "doc1", 1, 1)
    assert result.not_delivered_reason == "unreadable_source"


def test_vision_missing_image_is_unreadable_without_a_model_call() -> None:
    client = FakeVisionClient()
    result = digitize_pages(vision(client, FakeImages(missing=[1])), "doc1", 1, 1)
    assert client.images == []
    assert result.not_delivered_reason == "unreadable_source"


def test_vision_token_cap_stops_further_pages() -> None:
    client = FakeVisionClient(reply([rc(0, 0, "a")]), reply([rc(0, 0, "b")]))
    result = digitize_pages(vision(client, cap=120), "doc1", 1, 3)
    assert [p.page for p in result.pages] == [1]
    assert [i.code for i in result.open_issues] == ["token_cap_reached"]
    assert len(client.images) == 1


def test_vision_low_confidence_cell_is_flagged() -> None:
    client = FakeVisionClient(reply([rc(0, 0, "Revenue"), rc(0, 1, "5,000", 0.3)]))
    result = digitize_pages(vision(client), "doc1", 1, 1)
    assert [i.code for i in result.open_issues] == ["low_confidence_read"]


# ---- OpenRouter client image parts ---------------------------------------------------------


class _Chat:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def create(self, **kwargs: object) -> object:
        from types import SimpleNamespace as NS

        self.kwargs = kwargs
        return NS(
            choices=[NS(message=NS(content="{}"))],
            usage=NS(prompt_tokens=5, completion_tokens=1),
        )


def test_openrouter_client_sends_images_as_data_urls() -> None:
    from types import SimpleNamespace as NS

    from pydantic import SecretStr

    chat = _Chat()
    client = OpenRouterClient(SecretStr("k"), sdk=NS(chat=NS(completions=chat)))
    client.complete(
        model="m", system="s", user="u", images=[ImagePart(data=b"abc", mime="image/png")]
    )
    content = chat.kwargs["messages"][1]["content"]  # type: ignore[index]
    assert content[0] == {"type": "text", "text": "u"}
    assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj"}}


def test_openrouter_client_without_images_keeps_plain_text_content() -> None:
    from types import SimpleNamespace as NS

    from pydantic import SecretStr

    chat = _Chat()
    OpenRouterClient(SecretStr("k"), sdk=NS(chat=NS(completions=chat))).complete(
        model="m", system="s", user="u"
    )
    assert chat.kwargs["messages"][1]["content"] == "u"  # type: ignore[index]


# ---- page images from a scanned PDF -------------------------------------------------------


def scan_pdf(path: Path, pages: int = 2) -> Path:
    imgs = [Image.new("RGB", (40 + 10 * i, 40), "white") for i in range(pages)]
    imgs[0].save(path, "PDF", save_all=True, append_images=imgs[1:])
    return path


def test_pdf_page_images_returns_the_embedded_image(tmp_path: Path) -> None:
    source = PdfPageImages({"doc1": scan_pdf(tmp_path / "scan.pdf")})
    part = source.page_image("doc1", 2)
    assert part.mime in {"image/jpeg", "image/png"}
    assert part.data[:2] in {b"\xff\xd8", b"\x89P"}


@pytest.mark.parametrize("doc_id,page", [("other", 1), ("doc1", 3), ("doc1", 0)])
def test_pdf_page_images_refuses_unknown_document_or_page(
    tmp_path: Path, doc_id: str, page: int
) -> None:
    source = PdfPageImages({"doc1": scan_pdf(tmp_path / "scan.pdf")})
    with pytest.raises(PageUnreadable):
        source.page_image(doc_id, page)


def test_pdf_page_images_damaged_file_is_unreadable_not_a_crash(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(PageUnreadable):
        PdfPageImages({"doc1": bad}).page_image("doc1", 1)
