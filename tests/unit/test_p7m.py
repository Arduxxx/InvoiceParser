import base64

import pytest
from asn1crypto import cms

from invoiceflow.importers.p7m import extract_xml
from invoiceflow.importers.xml_parser import InvoiceFormatError

XML = b'<?xml version="1.0"?><p:FatturaElettronica versione="FPR12"/>'


def make_p7m(content: bytes | None) -> bytes:
    """A CMS SignedData wrapping content, like a .xml.p7m but without a real signature
    (the importer does not verify signatures). content=None makes a detached signature."""
    encapsulated: dict[str, object] = {"content_type": "data"}
    if content is not None:
        encapsulated["content"] = content
    signed = cms.SignedData(
        {
            "version": "v1",
            "digest_algorithms": [],
            "encap_content_info": encapsulated,
            "signer_infos": [],
        }
    )
    return bytes(cms.ContentInfo({"content_type": "signed_data", "content": signed}).dump())


def test_extracts_the_xml_from_binary_p7m() -> None:
    assert extract_xml(make_p7m(XML)) == XML


def test_extracts_the_xml_from_base64_p7m() -> None:
    encoded = base64.encodebytes(make_p7m(XML))  # split into lines, like real files
    assert extract_xml(encoded) == XML
    pem = b"-----BEGIN PKCS7-----\n" + encoded + b"-----END PKCS7-----\n"
    assert extract_xml(pem) == XML


def test_extracts_the_xml_from_a_file_signed_twice() -> None:
    assert extract_xml(make_p7m(make_p7m(XML))) == XML


def test_a_detached_signature_has_no_invoice() -> None:
    with pytest.raises(InvoiceFormatError, match="does not contain the invoice"):
        extract_xml(make_p7m(None))


def test_garbage_is_reported() -> None:
    with pytest.raises(InvoiceFormatError):
        extract_xml(b"\x30\x03garbage")
