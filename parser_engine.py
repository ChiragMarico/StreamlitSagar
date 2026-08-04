"""Safe, UI-friendly adapter around the recovered debit-note parser rules."""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any

import pdfplumber


GST_STATE = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Orissa", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "32": "Kerala", "33": "Tamil Nadu", "36": "Telangana", "37": "Andhra Pradesh",
}

OUTPUT_COLUMNS = ["PDF Name", "Month", "Claim Type", "Vendor Name", "InvoiceRefNo.",
                  "Invoice Date", "Base Amount", "GST Amount", "Total Amount", "Description",
                  "Customer State", "Customer Gst code", "Plant", "Plant GST"]


def clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[ \t]+", " ", str(value).replace("\xa0", " ").replace("**", " ")).strip()


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    raw = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    try:
        return round(float(raw), 2) if raw not in ("", "-", ".") else None
    except ValueError:
        return None


def match(pattern: str, text: str) -> str:
    found = re.search(pattern, text, re.I | re.S)
    return clean(found.group(1)) if found else ""


def normal_date(value: str) -> str:
    value = clean(value).split(" ")[0]
    for pattern in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, pattern).strftime("%m/%d/%Y")
        except ValueError:
            pass
    return value


def state(gstin: str) -> str:
    gstin = clean(gstin)
    return GST_STATE.get(gstin[:2], "") if len(gstin) >= 2 else ""


def claim(description: str, default: str = "") -> str:
    value = clean(description).lower()
    if "promotion services" in value:
        return "Promotion services"
    if "promo discount" in value:
        return "Promo Discount"
    if any(token in value for token in ("promo recovery", "trade disc", "promo/")) or value.startswith("promo"):
        return "Promo"
    return default


def month(description: str, invoice_date: str) -> str:
    found = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*[' -]*(\d{2,4})", clean(description), re.I)
    if found:
        return f"{found.group(1).title()[:3]}'{found.group(2)[-2:]}"
    try:
        return datetime.strptime(normal_date(invoice_date), "%m/%d/%Y").strftime("%b'%y")
    except ValueError:
        return ""


def empty_record(name: str) -> dict[str, Any]:
    return {column: None if "Amount" in column else "" for column in OUTPUT_COLUMNS} | {"PDF Name": name}


def read_pages(data: bytes) -> list[dict[str, Any]]:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [{"page_no": i, "text": clean(page.extract_text() or "")} for i, page in enumerate(pdf.pages, 1)]


def identify(text: str) -> tuple[str, str]:
    upper = text.upper()
    if "RELIANCE RETAIL LIMITED" in upper and "DEBIT NOTE NO" in upper:
        return "reliance", "Reliance Retail Limited"
    if any(x in upper for x in ("MORE RETAIL PRIVATE LIMITED", "FINANCIAL DEBIT NOTE", "MRPL")):
        return "more", "MORE RETAIL PRIVATE LIMITED"
    if any(x in upper for x in ("AIRPLAZA RETAIL HOLDINGS PVT LTD", "VISHAL MEGA MART")):
        return "vmm", "AIRPLAZA RETAIL HOLDINGS PVT LTD"
    if any(x in upper for x in ("FIORA HYPERMARKET LIMITED", "TRENT HYPERMARKET PRIVATE LIMITED", "STAR BAZAAR")):
        return "tesco", "Tesco"
    return "unknown", "Unknown format"


def parse_pdf(name: str, data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    try:
        pages = read_pages(data)
    except Exception as exc:
        return [], [{"PDF Name": name, "Issue": f"Could not open PDF: {exc}"}], {"pages": 0, "characters": 0, "vendor": "Unreadable"}
    full = "\n".join(page["text"] for page in pages)
    key, vendor = identify(full)
    evidence = {"pages": len(pages), "characters": len(full), "vendor": vendor, "preview": full[:1800]}
    if len(full.strip()) < 50:
        return [], [{"PDF Name": name, "Issue": "No usable text found; this may be a scanned PDF."}], evidence
    parsers = {"reliance": _reliance, "more": _more, "vmm": _vmm, "tesco": _tesco}
    if key not in parsers:
        return [], [{"PDF Name": name, "Issue": "Unknown customer format; a parser rule must be trained."}], evidence
    records = parsers[key](name, pages)
    issues = validate(records)
    return records, issues, evidence


def _reliance(name: str, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for page in pages:
        text = page["text"]
        if "Debit Note No" not in text:
            continue
        row = empty_record(name) | {"Vendor Name": "Reliance Retail Limited"}
        row["InvoiceRefNo."] = match(r"Debit Note No\s*-\s*(\d+)\s*/\s*\d+", text)
        row["Invoice Date"] = normal_date(match(r"PAN No\.\s*AABCR1718E\s+(\d{1,2}/\d{1,2}/\d{4})", text) or match(r"(\d{1,2}/\d{1,2}/\d{4})\s+Debit Note", text))
        description = match(r"Particulars\s+Amount\(Rs\)\s+(.+?)\s+Total \(In Figures\)", text)
        total = number(match(r"Total \(In Figures\)\s+([0-9,.]+)", text))
        row |= {"Description": re.sub(r"\s+[0-9,]+(?:\.\d{1,2})?$", "", clean(description).lstrip("*")), "Base Amount": total, "Total Amount": total}
        row["Month"], row["Claim Type"] = month(row["Description"], row["Invoice Date"]), claim(row["Description"], "Promo Discount")
        out.append(row)
    return out


def _more(name: str, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for page in pages:
        text = page["text"]
        if "FINANCIAL DEBIT NOTE" not in text.upper():
            continue
        row = empty_record(name) | {"Vendor Name": "MORE RETAIL PRIVATE LIMITED"}
        for value in re.findall(r"Financial Debit Note\s*:?\s*([A-Z0-9/]+|[0-9]{4}-[0-9]{2}-[0-9]{2})", text, re.I):
            if re.match(r"\d{4}-\d{2}-\d{2}", value): row["Invoice Date"] = normal_date(value)
            elif "/" in value: row["InvoiceRefNo."] = value
        supplier, recipient = match(r"Supplier GSTN\s*:?\s*([0-9A-Z]{15})", text), match(r"Recipient GSTN\s*:?\s*([0-9A-Z]{15})", text)
        description = match(r"INVOICE DESCRIPTION:\s*(.+?)\s*TOTAL AMOUNT RS", text)
        total = number(match(r"TOTAL AMOUNT RS\.?:?\s*([0-9,.]+)", text))
        row |= {"Description": description, "Base Amount": total, "GST Amount": 0.0, "Total Amount": total, "Customer Gst code": supplier, "Customer State": state(supplier), "Plant GST": recipient, "Plant": state(recipient)}
        row["Month"], row["Claim Type"] = month(description, row["Invoice Date"]), claim(description, "Promo")
        out.append(row)
    return out


def _vmm(name: str, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for page in pages:
        text = page["text"]
        if "Invoice No." not in text or "AIRPLAZA RETAIL HOLDINGS PVT LTD" not in text:
            continue
        row = empty_record(name) | {"Vendor Name": "AIRPLAZA RETAIL HOLDINGS PVT LTD"}
        gstins = re.findall(r"GST:\s*([0-9A-Z]{15})", text, re.I)
        supplier, recipient = (gstins[0] if gstins else ""), (gstins[-1] if len(gstins) > 1 else "")
        description = match(r"1\.\s*(.+?)\s+([0-9,]+\.\d{2})\s+GST", text)
        base = number(match(r"1\.\s*.+?\s+([0-9,]+\.\d{2})\s+GST", text))
        taxes = [number(x) or 0 for x in re.findall(r"(?:IGST|CGST|SGST)\s+@\s+[0-9.]+\s*%\s+([0-9,]+\.\d{2})", text, re.I)]
        total = number(match(r"Total\s+([0-9,]+\.\d{2}|[0-9,]+)\s+For AIRPLAZA", text))
        row |= {"InvoiceRefNo.": match(r"Invoice No\.?:\s*([0-9]+)", text), "Invoice Date": normal_date(match(r"Date:\s*(\d{1,2}\.\d{1,2}\.\d{4})", text)), "Description": description, "Base Amount": base, "GST Amount": round(sum(taxes), 2) if taxes else None, "Total Amount": total, "Customer Gst code": supplier, "Customer State": state(supplier), "Plant GST": recipient, "Plant": state(recipient)}
        row["Month"], row["Claim Type"] = month(description, row["Invoice Date"]), claim(description, "Promotion services")
        out.append(row)
    return out


def _tesco(name: str, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for page in pages:
        text = page["text"]
        if "Invoice No." not in text or "Description of Goods" not in text or not any(x in text for x in ("Fiora Hypermarket", "Trent Hypermarket")):
            continue
        row = empty_record(name) | {"Vendor Name": "Tesco"}
        description = match(r"(Promo/[^\n]+?|Reg\.Trade Disc/[^\n]+?)\s+996211", text)
        supplier, recipient = match(r"GSTN\s*/\s*UIN\s*:\s*([0-9A-Z]{15})", text), match(r"GSTIN/ Unique ID\s*:\s*([0-9A-Z]{15})", text)
        values = re.findall(r"Total\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s+0\.00\s+([0-9,]+\.\d{2})", text, re.I)
        base = cgst = sgst = total = None
        if values: base, cgst, sgst, total = map(number, values[-1])
        row |= {"InvoiceRefNo.": match(r"Invoice No\.\s*:\s*([0-9]+)", text), "Invoice Date": normal_date(match(r"Invoice date\s*:\s*(\d{1,2}\.\d{1,2}\.\d{4})", text)), "Description": description, "Base Amount": base, "GST Amount": round((cgst or 0) + (sgst or 0), 2) if values else None, "Total Amount": total, "Customer Gst code": supplier, "Customer State": state(supplier), "Plant GST": recipient, "Plant": state(recipient)}
        row["Month"], row["Claim Type"] = month(description, row["Invoice Date"]), claim(description, "Promo")
        out.append(row)
    return out


def validate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues = []
    mandatory = ("Vendor Name", "InvoiceRefNo.", "Invoice Date", "Total Amount")
    seen = set()
    for row in records:
        row_issues = [f"Missing {field}" for field in mandatory if row.get(field) in (None, "")]
        key = (row.get("Vendor Name"), row.get("InvoiceRefNo."))
        if key in seen and all(key): row_issues.append("Duplicate vendor + invoice reference")
        seen.add(key)
        if all(row.get(x) is not None for x in ("Base Amount", "GST Amount", "Total Amount")) and abs(row["Base Amount"] + row["GST Amount"] - row["Total Amount"]) > 1:
            row_issues.append("Base + GST does not match total")
        issues.extend({"PDF Name": row["PDF Name"], "InvoiceRefNo.": row.get("InvoiceRefNo.", ""), "Issue": issue} for issue in row_issues)
    return issues
