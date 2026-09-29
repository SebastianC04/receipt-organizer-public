import os
import re
import json
import shutil
import base64
import io
import requests
import fitz  # PyMuPDF -- pip install pymupdf
from datetime import datetime
from PIL import Image
from database import SessionLocal, Deposit, init_db
from analytics import flag_senders_by_monthly_total

init_db()

PENDING_DIR = "pending_scans"
ARCHIVE_DIR = "processed_archive"
DUPLICATE_DIR = "duplicate_scans"
NEEDS_REVIEW_DIR = "needs_review"

OLLAMA_MODEL = "qwen2.5vl:7b"
OLLAMA_URL = "http://localhost:11434/api/chat"
REQUEST_TIMEOUT = 120  # seconds

DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%m/%d/%y"]

VALID_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.pdf')

os.makedirs(PENDING_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)
os.makedirs(DUPLICATE_DIR, exist_ok=True)
os.makedirs(NEEDS_REVIEW_DIR, exist_ok=True)

RIA_EXTRACTION_PROMPT = (
    "You are an OCR and data extraction tool. Look at the provided Ria receipt.\n"
    "This may be a normal Order receipt, an Order receipt marked VOID (look for the word\n"
    "'VOID' handwritten or stamped across the middle of the page, and/or handwritten over\n"
    "the signature lines near the bottom), or a separate Refund receipt (look for the words\n"
    "'Refund Date' and 'Total Refunded' in place of 'Order Date' and 'Total').\n"
    "Do NOT use placeholder names like John Doe. Read the actual image.\n"
    "Ria receipts have THREE different ID numbers -- do not confuse them:\n"
    "  - 'Seq No' / 'No. Sec.': a short internal sequence number. Only normal/VOID receipts\n"
    "    have this -- a Refund receipt never shows a Seq No.\n"
    "  - 'Order No' / 'No. Orden' (on normal/VOID receipts) or 'Transaction No' / 'No.\n"
    "    Transaccion' (on Refund receipts): a longer, usually 'US'-prefixed transaction\n"
    "    identifier. These are the SAME identifier space under two different labels --\n"
    "    this is what links a Refund receipt back to the original transaction.\n"
    "  - 'Customer No' / 'No. Cliente': identifies the sender's account, NOT the transaction.\n"
    "    Never use this for sequence_number or order_number.\n"
    "Output a strict JSON object with exactly these keys:\n"
    "{\n"
    "  \"raw_transcription\": \"(Step 1: Type the literal text you see for the Order/Refund Date, Seq No, Order No/Transaction No, Sender, Total, Recipient, and any VOID marks here first)\",\n"
    "  \"date\": \"(Step 2: Extract the Order Date, or the Refund Date on a Refund receipt, formatted as MM/DD/YYYY)\",\n"
    "  \"sequence_number\": \"(Step 2: On a normal or VOID receipt, extract only the digits after 'Seq No', e.g. '69447' -- this field IS present on those two receipt types. On a Refund receipt only, there is no Seq No field at all, so leave this as an empty string)\",\n"
    "  \"order_number\": \"(Step 2: Extract only the digits after 'Order No' on a normal/VOID receipt, or after 'Transaction No' on a Refund receipt -- never the Customer No)\",\n"
    "  \"sender_name\": \"(Step 2: Extract the SENDER full name)\",\n"
    "  \"amount\": \"(Step 2: Extract the Total USD numeric value, or the Total Refunded value on a Refund receipt)\",\n"
    "  \"recipient_name\": \"(Step 2: Extract the RECIPIENT full name)\",\n"
    "  \"is_cancellation\": \"(Step 2: true if the word VOID appears anywhere on the receipt, OR this is a separate Refund receipt; otherwise false)\",\n"
    "  \"cancellation_type\": \"(Step 2: 'void' if VOID appears on the receipt, 'refund' if this is a Refund receipt, otherwise null)\"\n"
    "}\n"
    "\n"
    "Example:\n"
    "Given a receipt showing:\n"
    "  Order No. / No. Orden        US1234567890\n"
    "  Seq No. / No. Sec.           55555\n"
    "  Order Date / Fecha de Orden  01/15/2026\n"
    "  SENDER / CLIENTE             JUAN PEREZ LOPEZ\n"
    "  Total                        100.00 USD\n"
    "  RECIPIENT / BENEFICIARIO     MARIA PEREZ LOPEZ\n"
    "\n"
    "the correct output is:\n"
    "{\n"
    "  \"raw_transcription\": \"Order No. US1234567890 Seq No. 55555 Order Date 01/15/2026 SENDER JUAN PEREZ LOPEZ Total 100.00 USD RECIPIENT MARIA PEREZ LOPEZ\",\n"
    "  \"date\": \"01/15/2026\",\n"
    "  \"sequence_number\": \"55555\",\n"
    "  \"order_number\": \"1234567890\",\n"
    "  \"sender_name\": \"JUAN PEREZ LOPEZ\",\n"
    "  \"amount\": \"100.00\",\n"
    "  \"recipient_name\": \"MARIA PEREZ LOPEZ\",\n"
    "  \"is_cancellation\": false,\n"
    "  \"cancellation_type\": null\n"
    "}\n"
    "\n"
    "Now do the same for the receipt image provided below."
)

MAXI_EXTRACTION_PROMPT = (
    "You are an OCR and data extraction tool. Look at the provided MaxiTransfers (Maxi Send) receipt.\n"
    "This may be either a normal transfer receipt or a Cancellation Receipt -- check for the words\n"
    "'Cancellation Receipt' or 'Canceled Invoice #' near the top of the document.\n"
    "Do NOT use placeholder names like John Doe. Read the actual image.\n"
    "Output a strict JSON object with exactly these keys:\n"
    "{\n"
    "  \"raw_transcription\": \"(Step 1: Type the literal text you see for the date, Receipt/Folio # or Canceled Invoice #, Sender/Remitente, the USD total, and Recipient/Beneficiario here first)\",\n"
    "  \"is_cancellation\": \"(Step 2: true if this is a Cancellation Receipt, otherwise false)\",\n"
    "  \"cancellation_type\": \"(Step 2: 'refund' if this is a Cancellation Receipt, otherwise null)\",\n"
    "  \"date\": \"(Step 2: Extract the receipt date, formatted as MM/DD/YYYY)\",\n"
    "  \"sequence_number\": \"(Step 2: On a normal receipt, extract the number after 'Receipt/Folio #'. On a Cancellation Receipt, extract the number after 'Canceled Invoice #' instead)\",\n"
    "  \"references_sequence_number\": \"(Step 2: On a Cancellation Receipt, the same Canceled Invoice # value -- this links the cancellation back to the original transaction. On a normal receipt, null)\",\n"
    "  \"sender_name\": \"(Step 2: Extract the full name that follows 'Sender/Remitente')\",\n"
    "  \"amount\": \"(Step 2: On a normal receipt, extract the numeric value next to 'TOTAL/TOTAL' -- the USD total charged to the sender. On a Cancellation Receipt, extract the numeric value next to 'Total Amount Refunded' instead)\",\n"
    "  \"recipient_name\": \"(Step 2: Extract the full name that follows 'Recipient/Beneficiario')\"\n"
    "}"
)


def get_extraction_prompt(receipt_type):
    receipt_type = (receipt_type or "ria").strip().lower()
    if receipt_type == "maxi":
        return MAXI_EXTRACTION_PROMPT
    return RIA_EXTRACTION_PROMPT


def load_source_image(file_path):
    """
    Returns a PIL Image regardless of whether the source file is a
    JPEG/PNG scan or a PDF. For PDFs, only the first page is rendered
    (receipts are expected to be single-page).
    """
    if file_path.lower().endswith('.pdf'):
        doc = fitz.open(file_path)
        try:
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=300)
            return Image.open(io.BytesIO(pix.tobytes("png")))
        finally:
            doc.close()
    return Image.open(file_path)


def prepare_image_payload(file_path):
    """
    Downscale + re-encode before sending to the vision model.

    A raw 300 DPI scan (or PDF page rendered at 300 DPI) is far larger than
    what the model's vision encoder actually uses internally. Letting the
    model do that downsampling itself (rather than a clean, controlled
    thumbnail) is what was destroying small receipt text and driving the
    hallucinated field values.
    """
    img = load_source_image(file_path)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img.thumbnail((1024, 1024))
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def normalize_date_string(raw_date):
    """
    Converts whatever date format the model returns into YYYY-MM-DD.
    Monthly grouping (for the $3,000 flagging threshold) depends on this
    being consistent. Raises ValueError if unparseable, so the caller can
    route that receipt to needs_review instead of storing bad data.
    """
    raw_date = (raw_date or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw_date, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: '{raw_date}'")


def normalize_cancellation_type(raw_value):
    """
    Collapses whatever the model returns for cancellation_type down to the
    two values the pipeline understands ("void", "refund"), or None. Guards
    against the model inventing a label or echoing the field description.
    """
    value = str(raw_value or "").strip().lower()
    if value in ("void", "refund"):
        return value
    return None


def normalize_order_number(raw_value):
    """
    Strips to digits-only. The model is inconsistent about whether it keeps
    the 'US' country-code prefix on Order No/Transaction No -- observed on
    real scans keeping it on some receipts and dropping it on others. Since
    order_number is exactly the field a Ria refund is matched back to its
    original transaction by (see Stage 3 linking below), an inconsistent
    prefix would make that match silently fail. Normalizing to digits-only
    makes the comparison prefix-agnostic instead of relying on the model to
    be consistent about cosmetic formatting.
    """
    digits = re.sub(r'[^0-9]', '', str(raw_value or ''))
    return digits or None


def parse_amount(raw_amount):
    """
    Strips currency symbols/commas/stray text before converting to float.
    Raises ValueError on anything unparseable so the caller can flag it
    rather than silently defaulting to 0.0.
    """
    if raw_amount is None:
        raise ValueError("Missing amount")
    cleaned = re.sub(r'[^0-9.\-]', '', str(raw_amount))
    if not cleaned:
        raise ValueError(f"Could not parse amount from '{raw_amount}'")
    return float(cleaned)


def process_image_batch_queue(receipt_type="ria"):
    """
    Processes every file in pending_scans/ using the extraction prompt for
    the given receipt_type ("ria" or "maxi"). One mode applies to the whole
    batch run -- mixed-type batches aren't auto-detected.
    """
    db_session = SessionLocal()
    image_files = [f for f in os.listdir(PENDING_DIR) if f.lower().endswith(VALID_EXTENSIONS)]

    if not image_files:
        print(f"Queue empty. Drop receipt files (jpg/png/pdf) inside the '{PENDING_DIR}' folder to process them.")
        return

    extraction_prompt = get_extraction_prompt(receipt_type)
    print(f"Found {len(image_files)} file(s) in queue. Processing as '{receipt_type}' receipts via {OLLAMA_MODEL}...\n")

    for filename in image_files:
        file_path = os.path.join(PENDING_DIR, filename)
        print(f"📸 Asking {OLLAMA_MODEL} to read {filename}...")

        # --- Stage 1: model call + JSON parsing ---
        try:
            encoded_string = prepare_image_payload(file_path)

            payload = {
                "model": OLLAMA_MODEL,
                "format": "json",
                "messages": [
                    {
                        "role": "user",
                        "content": extraction_prompt,
                        "images": [encoded_string]
                    }
                ],
                "stream": False,
                "options": {"temperature": 0.0}
            }

            response = requests.post(OLLAMA_URL, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            raw_output = response.json()['message']['content'].strip()

            if raw_output.startswith("```json"):
                raw_output = raw_output.replace("```json", "").replace("```", "").strip()
            elif raw_output.startswith("```"):
                raw_output = raw_output.replace("```", "").strip()

            clean_data = json.loads(raw_output)

        except requests.exceptions.Timeout:
            print(f"   ❌ Timed out waiting on {OLLAMA_MODEL} for {filename} (>{REQUEST_TIMEOUT}s)")
            continue
        except Exception as e:
            print(f"   ❌ Model call or JSON parsing failed for {filename}: {e}")
            shutil.move(file_path, os.path.join(NEEDS_REVIEW_DIR, filename))
            continue

        # --- Stage 2: field-level validation ---
        try:
            seq_num = str(clean_data.get('sequence_number', '')).strip()
            sender_name = clean_data.get('sender_name', '').strip()
            recipient_name = clean_data.get('recipient_name', '')
            amount = parse_amount(clean_data.get('amount'))
            date_string = normalize_date_string(clean_data.get('date', ''))
            is_cancellation = str(clean_data.get('is_cancellation', False)).strip().lower() in ('true', '1', 'yes')
            cancellation_type = normalize_cancellation_type(clean_data.get('cancellation_type'))
            references_seq = str(clean_data.get('references_sequence_number') or '').strip() or None
            order_num = normalize_order_number(clean_data.get('order_number'))
        except Exception as e:
            print(f"   ❌ Field validation failed for {filename}: {e}")
            shutil.move(file_path, os.path.join(NEEDS_REVIEW_DIR, filename))
            continue

        # --- Stage 3: cancellation linking, or duplicate protection ---
        # Maxi cancellations link back to an original transaction via
        # references_sequence_number, matched against sequence_number. Ria refunds
        # link back via order_number instead -- Order No/Transaction No is the
        # identifier space shared across Ria's normal, void, and refund documents,
        # while Seq No never appears on a Refund receipt so it can't serve as a
        # link. A Ria void has nothing to link to -- it IS the original receipt,
        # just voided -- so it falls through to the normal duplicate-check/insert
        # path below with is_canceled already set to True.
        lookup_field = lookup_value = None
        if is_cancellation and cancellation_type == 'refund':
            if receipt_type == 'maxi' and references_seq:
                lookup_field, lookup_value = Deposit.sequence_number, references_seq
            elif receipt_type == 'ria' and order_num:
                lookup_field, lookup_value = Deposit.order_number, order_num

        if lookup_value:
            original = db_session.query(Deposit).filter(
                lookup_field == lookup_value,
                Deposit.is_canceled == False
            ).first()
            if original:
                original.is_canceled = True
                original.cancellation_type = cancellation_type
                db_session.commit()
                try:
                    shutil.move(file_path, os.path.join(ARCHIVE_DIR, filename))
                    print(f"   🛑 Marked original transaction (Ref: {lookup_value}) as CANCELED based on {filename}")
                except Exception as e:
                    print(f"   ⚠️  Marked original transaction (Ref: {lookup_value}) as CANCELED, but failed to archive {filename}: {e}")
                continue
            # No matching original on file yet -- fall through and insert this
            # cancellation as its own standalone canceled record below.
        elif seq_num:
            existing = db_session.query(Deposit).filter(Deposit.sequence_number == seq_num).first()
            if existing:
                print(f"   ⚠️  Duplicate sequence_number '{seq_num}' for {filename} -- skipping insert")
                shutil.move(file_path, os.path.join(DUPLICATE_DIR, filename))
                continue

        # --- Stage 4: archive + persist ---
        try:
            shutil.move(file_path, os.path.join(ARCHIVE_DIR, filename))
        except Exception as e:
            print(f"   ❌ Failed to archive {filename}: {e}")
            continue

        try:
            new_deposit = Deposit(
                date_string=date_string,
                sequence_number=seq_num,
                order_number=order_num,
                sender_name=sender_name,
                amount=amount,
                recipient_name=recipient_name,
                receipt_type=receipt_type,
                image_filename=filename,
                is_flagged=False,
                is_canceled=is_cancellation,
                cancellation_type=cancellation_type,
                references_sequence_number=references_seq
            )
            db_session.add(new_deposit)
            db_session.commit()
            status_tag = f" [{cancellation_type.upper()}]" if cancellation_type else ""
            print(f"   ✅ Successfully Logged [{receipt_type}]{status_tag}: '{new_deposit.sender_name}' -> '{new_deposit.recipient_name}' | Ref: {new_deposit.sequence_number} | ${new_deposit.amount:,.2f}")
        except Exception as e:
            db_session.rollback()
            print(f"   ❌ Critical error saving {filename} to database: {e}")

    flag_senders_by_monthly_total(db_session)
    db_session.close()


if __name__ == "__main__":
    process_image_batch_queue()