"""MHTML conversion boundary retained while the v1 knowledge module exists."""

from knowledge import _mhtml_to_text as _v1_mhtml_to_text


def mhtml_to_text(raw: bytes) -> str:
    """Extract readable text from an MHTML archive."""
    return _v1_mhtml_to_text(raw)
