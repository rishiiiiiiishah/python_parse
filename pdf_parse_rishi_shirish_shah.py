import re
import pdfplumber
from typing import Dict, Any, List, Tuple
import logging

try:
    from pdf2image import convert_from_path
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RE_DATE = r'(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|\d{1,2})[\.]?\s*\d{1,2}[,]?\s*\d{2,4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b)'
RE_LAST4 = r'(?:\b(?:Account|Acct|Card)\s*(?:ending\s*in|ending|Number|No\.|#|:)?\s*(\d{4})\b)|(?:\*\*+(\d{4})\b)'
RE_TOTAL_BAL = r'(?:Total(?:\s|-)Balance|Statement\s+Balance|New\s+Balance|Balance\s+Due|Amount\s+Due)\s*[:\s]\$?\s*([-,\d\.]+)'
RE_DUE_DATE = r'(?:Payment\s+Due\s+Date|Due\s+Date|Payment\s+Due)\s*[:\s]\s*' + RE_DATE
RE_STATEMENT_PERIOD = r'(?:Statement\s+Period|Billing\s+Period|Statement\s+Date[s]?)\s*[:\s\-–]+\s*(' + RE_DATE + r')\s*(?:to|-|–)\s*(' + RE_DATE + r')'

CARD_TYPES = ['VISA', 'MASTERCARD', 'AMEX', 'AMERICAN EXPRESS', 'DISCOVER']

def text_from_pdf(path: str) -> str:
    text_pages = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            try:
                txt = p.extract_text() or ""
            except Exception:
                txt = ""
            text_pages.append(txt)
    return "\n".join(text_pages)

def ocr_pdf_text(path: str) -> str:
    if not OCR_AVAILABLE:
        raise RuntimeError("OCR libs not installed (pdf2image/pytesseract).")
    pages = convert_from_path(path, dpi=200)
    texts = []
    for img in pages:
        texts.append(pytesseract.image_to_string(img))
    return "\n".join(texts)

def find_first(regex: str, text: str, flags=0) -> Tuple[str, List[str]]:
    m = re.search(regex, text, flags)
    if not m:
        return "", []
    return m.group(0), list(m.groups()) if m.groups() else []

def parse_dates(datestr: str) -> str:
    from dateutil import parser
    try:
        dt = parser.parse(datestr, dayfirst=False)
        return dt.date().isoformat()
    except Exception:
        return datestr.strip()

def extract_transactions_tables(path: str) -> List[dict]:
    res = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            try:
                tables = p.extract_tables()
            except Exception:
                tables = []
            for t in tables:
                if not t:
                    continue
                header = t[0]
                rows = t[1:]
                df_rows = []
                for row in rows:
                    mapping = {(header[i] if i < len(header) and header[i] else f"col{i}"): (row[i] if i < len(row) else "") for i in range(max(len(header), len(row)))}
                    df_rows.append(mapping)
                if df_rows:
                    res.append({"page": p.page_number, "rows": df_rows})
    return res

def extract_fields_from_text(text: str) -> Dict[str, Any]:
    out = {"card_type": None, "last4": None, "statement_period": None, "due_date": None, "total_balance": None, "confidence": {}}

    for ct in CARD_TYPES:
        if re.search(r'\b' + re.escape(ct) + r'\b', text, re.IGNORECASE):
            out['card_type'] = ct.upper()
            out['confidence']['card_type'] = 0.95
            break

    m = re.search(RE_LAST4, text, re.IGNORECASE)
    if m:
        last4 = next((g for g in m.groups() if g and re.fullmatch(r'\d{4}', g)), None)
        out['last4'] = last4
        out['confidence']['last4'] = 0.95

    m = re.search(RE_STATEMENT_PERIOD, text, re.IGNORECASE)
    if m:
        try:
            groups = [g for g in m.groups() if g and re.search(r'\d', g)]
            if len(groups) >= 2:
                start = parse_dates(groups[0])
                end = parse_dates(groups[1])
                out['statement_period'] = {"start": start, "end": end}
                out['confidence']['statement_period'] = 0.9
        except Exception:
            out['statement_period'] = m.group(0)
            out['confidence']['statement_period'] = 0.6

    m = re.search(RE_DUE_DATE, text, re.IGNORECASE)
    if m:
        groups = [g for g in m.groups() if g and re.search(r'\d', g)]
        if groups:
            out['due_date'] = parse_dates(groups[0])
            out['confidence']['due_date'] = 0.95

    m = re.search(RE_TOTAL_BAL, text, re.IGNORECASE)
    if m:
        amt = None
        for g in m.groups():
            if g and re.search(r'[\d]', g):
                amt = g
                break
        if amt:
            amt = amt.replace(',', '').replace('(', '-').replace(')', '')
            out['total_balance'] = amt
            out['confidence']['total_balance'] = 0.95

    return out

def parse_statement(path: str, use_ocr_fallback: bool = True) -> Dict[str, Any]:
    logger.info("Parsing: %s", path)
    text = text_from_pdf(path)
    if not text.strip() and use_ocr_fallback and OCR_AVAILABLE:
        logger.info("No text extracted, running OCR fallback.")
        text = ocr_pdf_text(path)
    core = extract_fields_from_text(text)
    transactions = extract_transactions_tables(path)
    if use_ocr_fallback and OCR_AVAILABLE:
        pass
    result = {
        "source_file": path,
        "extracted": core,
        "transactions_tables": transactions,
        "raw_text_snippet": text[:2000],
    }
    return result

if __name__ == "__main__":
    import argparse, json
    parser = argparse.ArgumentParser(description="Credit card statement parser")
    parser.add_argument("pdf", help="PDF file to parse")
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR fallback")
    args = parser.parse_args()
    out = parse_statement(args.pdf, use_ocr_fallback=not args.no_ocr)
    print(json.dumps(out, indent=2))
