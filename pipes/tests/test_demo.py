from PIL import Image

from pipes.demo import render
from pipes.tests.test_tools import CHAIN, idx


def blank(tmp_path, w=400, h=100):
    p = tmp_path / "sheet.png"
    Image.new("RGB", (w, h), "white").save(p)
    return p


def test_render_draws_highlights_on_the_page_shifted_by_the_caption_band(tmp_path):
    r = idx(CHAIN).path_between("node_1", "node_4")
    out = render(r, blank(tmp_path), tmp_path / "out.png")
    img = Image.open(out)
    assert img.width == 400 and img.height > 100
    band = img.height - 100
    # the pipe centre line (y=20 on the page) is tinted, the empty page below is not
    on_pipe, off_pipe = img.getpixel((60, band + 20)), img.getpixel((60, band + 80))
    assert on_pipe != (255, 255, 255) and off_pipe == (255, 255, 255)


def test_render_handles_unreliable_and_error_results(tmp_path):
    bad = idx(CHAIN).connected_to("node_99")
    assert render(bad, blank(tmp_path), tmp_path / "e.png").exists()
    empty = idx(CHAIN).count_symbols(region=[500, 0, 600, 100])
    assert render(empty, blank(tmp_path), tmp_path / "c.png").exists()
