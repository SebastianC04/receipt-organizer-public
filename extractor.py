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
    "Do NOT use placeholder names like John Doe. Read the actual image.\n"
    "Output a strict JSON object with exactly these keys:\n"
    "{\n"
    "  \"raw_transcription\": \"(Step 1: Type the literal text you see for the Order Date, Seq No, Sender, Total, and Recipient here first)\",\n"
    "  \"date\": \"(Step 2: Extract the date from your transcription, formatted as MM/DD/YYYY)\",\n"
    "  \"sequence_number\": \"(Step 2: Extract the Seq No digits)\",\n"
    "  \"sender_name\": \"(Step 2: Extract the SENDER full name)\",\n"
    "  \"amount\": \"(Step 2: Extract the Total USD numeric value)\",\n"
    "  \"recipient_name\": \"(Step 2: Extract the RECIPIENT full name)\"\n"
    "}"
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
    "  \"date\": \"(Step 2: Extract the receipt date, formatted as MM/DD/YYYY)\",\n"
    "  \"sequence_number\": \"(Step 2: On a normal receipt, extract the number after 'Receipt/Folio #'. On a Cancellation Receipt, extract the number after 'Canceled Invoice #' instead -- this links the cancellation back to the original transaction)\",\n"
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
        except Exception as e:
            print(f"   ❌ Field validation failed for {filename}: {e}")
            shutil.move(file_path, os.path.join(NEEDS_REVIEW_DIR, filename))
            continue

        # --- Stage 3: cancellation linking, or duplicate protection ---
        if is_cancellation and seq_num:
            # A cancellation receipt references an existing invoice number rather
            # than being a new transaction -- link it to that record instead of
            # rejecting it as a duplicate or inserting a confusing second row.
            original = db_session.query(Deposit).filter(
                Deposit.sequence_number == seq_num,
                Deposit.is_canceled == False
            ).first()
            if original:
                original.is_canceled = True
                db_session.commit()
                try:
                    shutil.move(file_path, os.path.join(ARCHIVE_DIR, filename))
                    print(f"   🛑 Marked original transaction (Ref: {seq_num}) as CANCELED based on {filename}")
                except Exception as e:
                    print(f"   ⚠️  Marked original transaction (Ref: {seq_num}) as CANCELED, but failed to archive {filename}: {e}")
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
                sender_name=sender_name,
                amount=amount,
                recipient_name=recipient_name,
                receipt_type=receipt_type,
                image_filename=filename,
                is_flagged=False,
                is_canceled=is_cancellation
            )
            db_session.add(new_deposit)
            db_session.commit()
            print(f"   ✅ Successfully Logged [{receipt_type}]: '{new_deposit.sender_name}' -> '{new_deposit.recipient_name}' | Ref: {new_deposit.sequence_number} | ${new_deposit.amount:,.2f}")
        except Exception as e:
            db_session.rollback()
            print(f"   ❌ Critical error saving {filename} to database: {e}")

    flag_senders_by_monthly_total(db_session)
    db_session.close()


if __name__ == "__main__":
    process_image_batch_queue()