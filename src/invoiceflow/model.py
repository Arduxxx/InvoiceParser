from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Convenzione: *_cents = int (centesimi, importi a 2 decimali della fattura).
#              Decimal = quantità, prezzo unitario, aliquote (mai float).

@dataclass(frozen=True, kw_only=True)
class Party:
    """Cedente/prestatore o cessionario/committente."""
    denominazione: str = ""        # ragione sociale (azienda)...
    nome: str = ""                 # ...oppure persona fisica
    cognome: str = ""
    id_paese: str = ""             # IdFiscaleIVA/IdPaese, es. "IT"
    partita_iva: str = ""          # IdFiscaleIVA/IdCodice; "" se privato senza P.IVA
    codice_fiscale: str = ""
    indirizzo: str = ""            # Sede
    cap: str = ""
    comune: str = ""
    provincia: str = ""            # due lettere, solo IT
    nazione: str = "IT"

@dataclass(frozen=True, kw_only=True)
class Line:
    """DettaglioLinee (ripetuta)."""
    numero_linea: int
    descrizione: str
    quantita: Decimal | None = None    # opzionale nello schema
    unita_misura: str = ""
    prezzo_unitario: Decimal           # fino a 8 decimali
    prezzo_totale_cents: int           # PrezzoTotale, 2 decimali
    aliquota_iva: Decimal              # percentuale, es. Decimal("22.00")
    natura: str = ""                   # N1..N7, obbligatorio se aliquota == 0

@dataclass(frozen=True, kw_only=True)
class VatSummary:
    """DatiRiepilogo: UNO per aliquota (lista, non singolo)."""
    aliquota_iva: Decimal
    imponibile_cents: int              # ImponibileImporto
    imposta_cents: int                 # Imposta
    esigibilita_iva: str = ""          # I / D / S
    natura: str = ""

@dataclass(frozen=True, kw_only=True)
class Payment:
    """DettaglioPagamento (ripetuto)."""
    modalita: str = ""                 # MP01..MP23
    data_scadenza: date | None = None
    importo_cents: int | None = None
    iban: str = ""

@dataclass(frozen=True, kw_only=True)
class Invoice:
    # DatiGeneraliDocumento
    tipo_documento: str                # TD01..
    divisa: str                        # "EUR"
    data: date                         # Data emissione
    numero: str                        # es. "2026/042"
    importo_totale_cents: int | None = None   # ImportoTotaleDocumento è OPZIONALE

    cedente: Party
    cessionario: Party
    lines: list[Line]
    vat_summary: list[VatSummary]
    payments: list[Payment]

    # Provenienza — serve a duplicati/dedup e a tracciare da dove viene
    source_path: str = ""
    source_sha256: str = ""            # hash dei byte grezzi del file