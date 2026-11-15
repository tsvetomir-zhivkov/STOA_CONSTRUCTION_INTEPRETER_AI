"""The translator pieces pipes reuses, imported in one place (never copied, never edited there).

The scan route needs the translator's raster preprocessing (translator.raster.preprocess) and its Modal OCR class
(translator.modal_app.Ocr). Those live on the translator branch merge-v03 and are not on main yet. Without them this
module still imports: HAVE_PREPROCESS / have_ocr() say what is missing, and every call that needs them raises
TranslatorMissing with that message instead of an ImportError at import time. The DXF route, the tools, the agent
and the schema do not need them.
"""

from translator import router  # noqa: F401  (router.route, vector-PDF check)

MISSING = (
    "the pipe scan route needs the translator's preprocessing and OCR (translator.raster.preprocess, "
    "translator.modal_app.Ocr), which are on branch merge-v03 and not on main yet; the DXF route works without them"
)


class TranslatorMissing(RuntimeError):
    """A scan-route step needs a translator module that this checkout does not have."""


try:
    from translator.raster.preprocess import DARK_MEDIAN, Preprocessed, load_image, preprocess

    HAVE_PREPROCESS = True
except ImportError:
    HAVE_PREPROCESS = False
    DARK_MEDIAN = None
    Preprocessed = None

    def load_image(*args, **kwargs):
        raise TranslatorMissing(MISSING)

    def preprocess(*args, **kwargs):
        raise TranslatorMissing(MISSING)


def require_preprocess() -> None:
    if not HAVE_PREPROCESS:
        raise TranslatorMissing(MISSING)


def translator_ocr():
    """(Ocr, app) from translator.modal_app, or TranslatorMissing."""
    try:
        from translator.modal_app import Ocr, app
    except ImportError as e:
        raise TranslatorMissing(MISSING) from e
    return Ocr, app


def have_ocr() -> bool:
    try:
        translator_ocr()
    except TranslatorMissing:
        return False
    return True
