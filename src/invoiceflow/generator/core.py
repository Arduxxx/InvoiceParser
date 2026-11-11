"""Supplier profiles and arithmetically correct invoices.

Every invoice built here is correct according to the FatturaPA rules our checks implement:
- PrezzoTotale = Quantita x PrezzoUnitario, rounded half up to the cent;
- one DatiRiepilogo per (AliquotaIVA, Natura), ImponibileImporto = sum of its PrezzoTotale;
- Imposta = ImponibileImporto x AliquotaIVA / 100, rounded half up to the cent;
- ImportoTotaleDocumento = sum of ImponibileImporto + sum of Imposta;
- one bank-transfer payment of the whole total, due after the issue date, to a valid IBAN.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from random import Random

from invoiceflow.generator.ids import random_italian_iban, random_partita_iva
from invoiceflow.model import Invoice, Line, Party, Payment, VatSummary
from invoiceflow.money import from_cents, to_cents

_V22 = Decimal("22.00")
_V10 = Decimal("10.00")
_V4 = Decimal("4.00")
_V0 = Decimal("0.00")


@dataclass(frozen=True)
class _ProductTemplate:
    description: str
    unit: str
    price_range: tuple[int, int]  # in units of 10**-price_decimals euro
    price_decimals: int
    qty_range: tuple[int, int]
    vat_rate: Decimal
    natura: str = ""


_T = _ProductTemplate

# category -> (words used in supplier names, products sold by suppliers of that category)
_CATALOG: dict[str, tuple[tuple[str, ...], tuple[_ProductTemplate, ...]]] = {
    "software": (("Informatica", "Software", "Digital", "Sistemi"), (
        _T("Canone licenza software gestionale", "mese", (1900, 9900), 2, (1, 12), _V22),
        _T("Hosting e dominio", "anno", (4900, 29900), 2, (1, 1), _V22),
        _T("Assistenza tecnica", "ore", (3500, 8000), 2, (1, 10), _V22),
    )),
    "utenze": (("Energia", "Luce e Gas", "Multiservizi"), (
        _T("Fornitura energia elettrica", "kWh", (180000, 320000), 6, (80, 900), _V10),
        _T("Fornitura gas naturale", "Smc", (900000, 1400000), 6, (20, 300), _V22),
        _T("Quota fissa di commercializzazione", "mese", (800, 2500), 2, (1, 2), _V10),
    )),
    "telefonia": (("Telecomunicazioni", "Connect", "Net"), (
        _T("Canone linea fibra", "mese", (2490, 5990), 2, (1, 2), _V22),
        _T("Piano mobile aziendale", "SIM", (790, 1990), 2, (1, 5), _V22),
    )),
    "cancelleria": (("Forniture", "Ufficio", "Cartotecnica"), (
        _T("Carta A4 80g risma 500 fogli", "pz", (350, 690), 2, (5, 50), _V22),
        _T("Toner stampante laser", "pz", (3900, 12900), 2, (1, 4), _V22),
        _T("Manuale tecnico", "pz", (1500, 6500), 2, (1, 5), _V4),
    )),
    "consulenza": (("Consulting", "Studio Associato", "Advisory"), (
        _T("Consulenza fiscale", "ore", (6000, 15000), 2, (1, 8), _V22),
        _T("Elaborazione cedolini", "pz", (1800, 3500), 2, (1, 10), _V22),
    )),
    "formazione": (("Formazione", "Academy"), (
        _T("Corso di formazione professionale", "ore", (4000, 9000), 2, (2, 16), _V0, "N2.2"),
    )),
    "assicurazioni": (("Assicurazioni", "Broker"), (
        _T("Premio polizza RC professionale", "anno", (25000, 120000), 2, (1, 1), _V0, "N4"),
    )),
    "ristorazione": (("Catering", "Ristorazione"), (
        _T("Servizio catering", "coperti", (1500, 4500), 2, (5, 40), _V10),
    )),
    "trasporti": (("Trasporti", "Logistica", "Express"), (
        _T("Spedizione nazionale", "colli", (690, 2490), 2, (1, 20), _V22),
    )),
}

CATEGORIES: tuple[str, ...] = tuple(_CATALOG)

_SURNAMES = ("Rossi", "Bianchi", "Ferrari", "Esposito", "Romano", "Colombo", "Ricci", "Marino",
             "Greco", "Bruno", "Gallo", "Conti", "De Luca", "Mancini", "Costa", "Giordano",
             "Rizzo", "Lombardi", "Moretti", "Barbieri", "Fontana", "Santoro", "Mariani")
_PREFIXES = ("Tecno", "Euro", "Ital", "Nord", "Centro", "Medi", "Alpi", "Adria", "Sud")
_LEGAL_FORMS = ("S.r.l.", "S.r.l.", "S.p.A.", "S.n.c.", "S.a.s.")
_STREETS = ("Via Roma", "Via Garibaldi", "Corso Italia", "Via Mazzini", "Viale Europa",
            "Via Verdi", "Piazza della Repubblica", "Via Dante")
_CITIES = (("Milano", "MI", "20121"), ("Roma", "RM", "00185"), ("Torino", "TO", "10121"),
           ("Napoli", "NA", "80133"), ("Bologna", "BO", "40121"), ("Firenze", "FI", "50123"),
           ("Verona", "VR", "37121"), ("Bari", "BA", "70121"), ("Padova", "PD", "35121"),
           ("Brescia", "BS", "25121"))

# How each supplier formats its invoice numbers; {n} = sequence number, {y} = year.
NUMBER_FORMATS = ("{n}", "{n}/{y}", "{y}/{n:03d}", "FT{n:05d}", "{y}-{n:04d}", "A{n:04d}")


@dataclass(frozen=True)
class Product:
    description: str
    unit: str
    unit_price: Decimal
    qty_range: tuple[int, int]
    vat_rate: Decimal
    natura: str


@dataclass(frozen=True)
class SupplierProfile:
    """A supplier and its usual habits: IBAN, catalog, numbering, payment terms."""

    party: Party
    iban: str
    category: str
    products: tuple[Product, ...]
    number_format: str
    payment_days: int

    @property
    def name(self) -> str:
        return self.party.denominazione


def _random_address(rng: Random) -> dict[str, str]:
    comune, provincia, cap = rng.choice(_CITIES)
    return {
        "indirizzo": f"{rng.choice(_STREETS)} {rng.randint(1, 200)}",
        "cap": cap,
        "comune": comune,
        "provincia": provincia,
    }


def company_party(rng: Random, name: str) -> Party:
    """An Italian company with a fresh, valid partita IVA (used also as codice fiscale)."""
    vat = random_partita_iva(rng)
    return Party(denominazione=name, id_paese="IT", partita_iva=vat, codice_fiscale=vat,
                 nazione="IT", **_random_address(rng))


def _supplier_name(rng: Random, category: str) -> str:
    words = _CATALOG[category][0]
    style = rng.randrange(3)
    if style == 0:
        base = f"{rng.choice(_SURNAMES)} {rng.choice(words)}"
    elif style == 1:
        base = f"{rng.choice(_PREFIXES)}{rng.choice(words).split()[0].lower()}"
    else:
        base = f"{rng.choice(words)} {rng.choice(_SURNAMES)} & C."
    return f"{base} {rng.choice(_LEGAL_FORMS)}"


def make_supplier(rng: Random, category: str | None = None,
                  taken_names: set[str] | None = None) -> SupplierProfile:
    """A new supplier with its own catalog. Names in `taken_names` are avoided."""
    cat = category if category is not None else rng.choice(CATEGORIES)
    taken = taken_names if taken_names is not None else set()
    name = _supplier_name(rng, cat)
    while name in taken:
        name = _supplier_name(rng, cat)
    templates = _CATALOG[cat][1]
    chosen = rng.sample(templates, k=rng.randint(1, len(templates)))
    products = tuple(
        Product(
            description=t.description,
            unit=t.unit,
            unit_price=Decimal(rng.randint(*t.price_range)).scaleb(-t.price_decimals),
            qty_range=t.qty_range,
            vat_rate=t.vat_rate,
            natura=t.natura,
        )
        for t in chosen
    )
    return SupplierProfile(
        party=company_party(rng, name),
        iban=random_italian_iban(rng),
        category=cat,
        products=products,
        number_format=rng.choice(NUMBER_FORMATS),
        payment_days=rng.choice((0, 15, 30, 30, 60, 90)),
    )


def make_client(rng: Random) -> Party:
    """The freelancer / small business that receives all the invoices."""
    return company_party(rng, f"Studio {rng.choice(_SURNAMES)}")


def format_number(number_format: str, n: int, year: int) -> str:
    return number_format.format(n=n, y=year)


def vat_amount_cents(imponibile_cents: int, rate: Decimal) -> int:
    """Imposta for one DatiRiepilogo: taxable x rate / 100, rounded half up to the cent."""
    return to_cents(from_cents(imponibile_cents) * rate / 100)


def make_line(numero_linea: int, product: Product, quantity: Decimal) -> Line:
    return Line(
        numero_linea=numero_linea,
        descrizione=product.description,
        quantita=quantity,
        unita_misura=product.unit,
        prezzo_unitario=product.unit_price,
        prezzo_totale_cents=to_cents(quantity * product.unit_price),
        aliquota_iva=product.vat_rate,
        natura=product.natura,
    )


def summarize(lines: Sequence[Line]) -> list[VatSummary]:
    """One DatiRiepilogo per (rate, natura), with exactly rounded Imposta."""
    groups: dict[tuple[Decimal, str], int] = {}
    for line in lines:
        key = (line.aliquota_iva, line.natura)
        groups[key] = groups.get(key, 0) + line.prezzo_totale_cents
    return [
        VatSummary(
            aliquota_iva=rate,
            natura=natura,
            imponibile_cents=imponibile,
            imposta_cents=vat_amount_cents(imponibile, rate),
            esigibilita_iva="" if natura else "I",
        )
        for (rate, natura), imponibile in sorted(groups.items())
    ]


def assemble_invoice(*, cedente: Party, cessionario: Party, data: date, numero: str,
                     lines: Sequence[Line], iban: str, due_date: date) -> Invoice:
    """A complete, consistent invoice: summaries, total and payment derived from the lines."""
    summaries = summarize(lines)
    total = sum(s.imponibile_cents + s.imposta_cents for s in summaries)
    payment = Payment(modalita="MP05", data_scadenza=due_date, importo_cents=total, iban=iban)
    return Invoice(
        tipo_documento="TD01",
        divisa="EUR",
        data=data,
        numero=numero,
        importo_totale_cents=total,
        cedente=cedente,
        cessionario=cessionario,
        lines=list(lines),
        vat_summary=summaries,
        payments=[payment],
    )


def make_invoice(rng: Random, supplier: SupplierProfile, client: Party, data: date,
                 numero: str) -> Invoice:
    """A correct invoice from `supplier` to `client`, with 1-4 lines from its catalog."""
    lines = []
    for i in range(rng.randint(1, 4)):
        product = rng.choice(supplier.products)
        quantity = Decimal(rng.randint(*product.qty_range))
        lines.append(make_line(i + 1, product, quantity))
    return assemble_invoice(
        cedente=supplier.party,
        cessionario=client,
        data=data,
        numero=numero,
        lines=lines,
        iban=supplier.iban,
        due_date=data + timedelta(days=supplier.payment_days),
    )
