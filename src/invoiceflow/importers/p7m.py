"""Extracts the XML from a signed invoice (.xml.p7m).

A .p7m file is the XML wrapped in a CAdES signature, which is a PKCS#7 / CMS SignedData
structure: the XML sits inside it as the "encapsulated content". We only take the XML
out; checking that the signature is valid is out of scope for this project.

Real files come in a few shapes, all handled here:
- binary DER (the most common);
- the same bytes encoded in base64, sometimes with -----BEGIN ...----- lines;
- signed more than once: the content of the outer signature is another .p7m.
"""

import base64
import binascii

from asn1crypto import cms

from invoiceflow.importers.xml_parser import InvoiceFormatError

MAX_NESTED_SIGNATURES = 3


def extract_xml(p7m: bytes) -> bytes:
    content = _as_der(p7m)
    for _ in range(MAX_NESTED_SIGNATURES):
        content = _signed_content(content)
        if not _looks_like_der(content):
            return content
    raise InvoiceFormatError("too many nested signatures")


def _signed_content(der: bytes) -> bytes:
    try:
        info = cms.ContentInfo.load(der)
        if info["content_type"].native != "signed_data":
            raise InvoiceFormatError("the .p7m does not contain signed data")
        content = info["content"]["encap_content_info"]["content"].native
    except InvoiceFormatError:
        raise
    except Exception as error:  # asn1crypto raises several types for malformed input
        raise InvoiceFormatError(f"not a valid .p7m signature: {error}") from error
    if not isinstance(content, bytes) or not content:
        raise InvoiceFormatError("the signature does not contain the invoice (detached?)")
    return content


def _as_der(data: bytes) -> bytes:
    if _looks_like_der(data):
        return data
    lines = [line for line in data.splitlines() if not line.startswith(b"-----")]
    try:
        return base64.b64decode(b"".join(lines), validate=False)
    except (binascii.Error, ValueError) as error:
        raise InvoiceFormatError("the .p7m is neither binary nor base64") from error


def _looks_like_der(data: bytes) -> bool:
    """A DER structure starts with 0x30 (SEQUENCE); XML and base64 text never do."""
    return data[:1] == b"\x30"
