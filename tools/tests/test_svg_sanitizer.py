import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.engines.thumbnail_cache import sanitize_provider_svg

PLAIN = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#c00"/></svg>'
ILLUSTRATOR = (b'<?xml version="1.0" encoding="utf-8"?>\n<!-- Generator: Adobe Illustrator -->\n'
               b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n' + PLAIN)


def test_a_plain_logo_and_one_with_the_standard_editor_doctype_are_accepted_and_the_doctype_is_dropped():
    assert b"svg" in sanitize_provider_svg(PLAIN)
    out = sanitize_provider_svg(ILLUSTRATOR)
    assert b"DOCTYPE" not in out and b"rect" in out


@pytest.mark.parametrize("evil", [
    b'<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',            # entity expansion
    b'<!DOCTYPE svg SYSTEM "x.dtd" [ <!ELEMENT svg ANY> ]><svg xmlns="http://www.w3.org/2000/svg"/>',    # a DOCTYPE with its own subset
    b'<svg xmlns="http://www.w3.org/2000/svg"><!ENTITY x "y"></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"/>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="http://evil.test/x.png"/></svg>',
    b'<html><body/></html>',
])
def test_anything_unsafe_is_still_refused(evil):
    with pytest.raises(Exception):
        sanitize_provider_svg(evil)
