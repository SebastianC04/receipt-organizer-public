import json
import os
import io
import csv
import glob
import shutil
import requests
from datetime import datetime

from PIL import Image

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import SessionLocal, Deposit, clear_all_deposits, engine, Base
from analytics import find_high_value_clients_data, get_sender_month_transactions
from extractor import (
    process_image_batch_queue,
    prepare_image_payload,
    get_extraction_prompt,
    OLLAMA_MODEL,
    OLLAMA_URL,
    REQUEST_TIMEOUT,
    ARCHIVE_DIR,
    NEEDS_REVIEW_DIR,
    VALID_EXTENSIONS,
    load_review_log,
    save_review_log,
)

app = FastAPI(title="Receipt Organizer Dashboard")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

UPLOAD_DIR = "pending_scans"
SNAPSHOT_DIR = "snapshots"
DB_PATH = "receipts.db"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

@app.get("/", response_class=HTMLResponse)
def render_dashboard(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={}
    )

@app.get("/database")
def redirect_old_database_view():
    """The full records table now lives on the dashboard itself; keep old links working."""
    return RedirectResponse("/")

@app.get("/api/analytics")
def get_analytics_api():
    db_session = SessionLocal()
    try:
        flagged_results = find_high_value_clients_data(db_session)
        # dashboard.js expects {month, total} for the monthly table and
        # {client_name, amount} for the flagged/VIP table -- both come from
        # the same underlying flagged-sender-month rows.
        monthly_aggregates = [
            {"month": r["month"], "total": r["amount"]} for r in flagged_results
        ]
        return JSONResponse({
            "monthly_aggregates": monthly_aggregates,
            "vip_clients": flagged_results
        })
    finally:
        db_session.close()

@app.get("/api/sender-monthly-totals")
def get_sender_monthly_totals():
    """
    Returns every sender's total deposited per calendar month.
    Used by the expanded DB view to show a running monthly total
    beside each individual transaction row.
    Shape: { "YYYY-MM": { "Sender Name": total_float, ... }, ... }
    """
    from sqlalchemy import func
    db_session = SessionLocal()
    try:
        results = db_session.query(
            Deposit.sender_name,
            func.substr(Deposit.date_string, 1, 7).label('month'),
            func.sum(Deposit.amount).label('total')
        ).group_by(Deposit.sender_name, 'month').all()

        totals = {}
        for row in results:
            month = row.month or "unknown"
            if month not in totals:
                totals[month] = {}
            totals[month][row.sender_name] = round(row.total, 2)
        return totals
    finally:
        db_session.close()

@app.patch("/api/deposit/{deposit_id}")
async def update_deposit(deposit_id: int, payload: dict):
    """
    Partial update for a single deposit row. Currently supports:
      { "amount": 123.45 }
    After updating the amount, re-runs flag logic so is_flagged stays accurate.
    """
    db_session = SessionLocal()
    try:
        deposit = db_session.query(Deposit).filter(Deposit.id == deposit_id).first()
        if not deposit:
            raise HTTPException(status_code=404, detail="Deposit not found.")
        if "amount" in payload:
            try:
                deposit.amount = float(payload["amount"])
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid amount value.")
        db_session.commit()
        # Re-run flag logic so a corrected amount that drops below $3k clears the flag
        from analytics import flag_senders_by_monthly_total
        # First clear all flags for this sender+month, then recompute
        from sqlalchemy import func as f
        month = deposit.date_string[:7] if deposit.date_string else None
        if month:
            db_session.query(Deposit).filter(
                Deposit.sender_name == deposit.sender_name,
                f.substr(Deposit.date_string, 1, 7) == month
            ).update({"is_flagged": False})
            db_session.commit()
        flag_senders_by_monthly_total(db_session)
        return {"status": "success", "id": deposit_id}
    finally:
        db_session.close()

@app.post("/api/recompute-flags")
async def recompute_flags():
    """
    Clears all is_flagged values across the entire table and re-runs the
    $3,000/month threshold check from scratch. Use this when a correction
    to an amount means a sender is no longer over the threshold, or when
    records have been manually adjusted.
    """
    from analytics import flag_senders_by_monthly_total
    db_session = SessionLocal()
    try:
        db_session.query(Deposit).update({"is_flagged": False})
        db_session.commit()
        flag_senders_by_monthly_total(db_session)
        return {"status": "success", "message": "All flags recomputed from current data."}
    finally:
        db_session.close()

def get_sender_transactions(sender_name: str, month: str):
    """
    Drill-down data for a flagged sender: every transaction they made in the
    given month (format 'YYYY-MM'), including the archived image filename so
    the UI can link to /api/receipt-image/{filename} for manual verification
    (e.g. spotting Reimbursed/Reembolsado receipts).
    """
    db_session = SessionLocal()
    try:
        transactions = get_sender_month_transactions(sender_name, month, db_session)
        return {"sender_name": sender_name, "month": month, "transactions": transactions}
    finally:
        db_session.close()

@app.get("/api/receipt-image/{filename}")
def get_receipt_image(filename: str):
    file_path = os.path.join(ARCHIVE_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found in archive.")
    return FileResponse(file_path)

@app.get("/api/needs-review")
def list_needs_review():
    """Receipts the pipeline set aside instead of storing, newest first, with the recorded reason."""
    if not os.path.isdir(NEEDS_REVIEW_DIR):
        return {"items": []}
    log = load_review_log()
    items = []
    for name in os.listdir(NEEDS_REVIEW_DIR):
        if not name.lower().endswith(VALID_EXTENSIONS):
            continue
        entry = log.get(name, {})
        items.append({
            "filename": name,
            "reason": entry.get("reason"),
            "receipt_type": entry.get("receipt_type"),
            "flagged_at": entry.get("flagged_at"),
            "modified": os.path.getmtime(os.path.join(NEEDS_REVIEW_DIR, name)),
        })
    # Moving a file keeps its old modified time, so prefer when it was set aside.
    items.sort(key=lambda item: item["flagged_at"] or datetime.fromtimestamp(item["modified"]).isoformat(), reverse=True)
    return {"items": items}

@app.get("/api/needs-review-image/{filename}")
def get_needs_review_image(filename: str):
    file_path = os.path.join(NEEDS_REVIEW_DIR, os.path.basename(filename))
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found in needs_review.")
    return FileResponse(file_path)

@app.post("/api/needs-review/{filename}/requeue")
def requeue_needs_review(filename: str):
    """Sends a set-aside receipt back to pending_scans so the next batch tries it again."""
    name = os.path.basename(filename)
    source = os.path.join(NEEDS_REVIEW_DIR, name)
    if not os.path.isfile(source):
        raise HTTPException(status_code=404, detail="File not found in needs_review.")
    target = os.path.join(UPLOAD_DIR, name)
    if os.path.exists(target):
        raise HTTPException(status_code=409, detail="A file with this name is already waiting in pending_scans.")
    shutil.move(source, target)
    log = load_review_log()
    if log.pop(name, None) is not None:
        save_review_log(log)
    return {"status": "success", "filename": name}

@app.post("/api/upload")
async def upload_receipt(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    file_ext = file.filename.split('.')[-1].lower()
    safe_filename = file.filename.replace(" ", "_")

    if file_ext in ['heic', 'heif']:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents))
        image = image.convert("RGB")
        new_filename = safe_filename.rsplit('.', 1)[0] + ".jpg"
        save_path = os.path.join(UPLOAD_DIR, new_filename)
        image.save(save_path, "JPEG")

    elif file_ext in ['jpg', 'jpeg', 'png', 'pdf']:
        save_path = os.path.join(UPLOAD_DIR, safe_filename)
        with open(save_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format")

    return {"status": "success", "filename": safe_filename, "saved_as": save_path}

@app.post("/api/process-batch")
async def trigger_batch_processing(background_tasks: BackgroundTasks, receipt_type: str = "ria"):
    background_tasks.add_task(process_image_batch_queue, receipt_type)
    return {"status": "batch_started", "message": f"Queue processing initialized for '{receipt_type}' receipts."}

@app.post("/api/debug-inspect/{filename}")
async def debug_inspect_file(filename: str, receipt_type: str = "ria"):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found in pending_scans folder.")

    try:
        # Uses the same downscale/re-encode helper as the batch pipeline in
        # extractor.py, so debug results match what actually happens in production.
        encoded_string = prepare_image_payload(file_path)
        extraction_prompt = get_extraction_prompt(receipt_type)

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
        
        clean_text = raw_output
        if clean_text.startswith("```json"):
            clean_text = clean_text.replace("```json", "").replace("```", "").strip()
        elif clean_text.startswith("```"):
            clean_text = clean_text.replace("```", "").strip()

        parsed_json = json.loads(clean_text)

        return {
            "status": "success",
            "filename": filename,
            "receipt_type": receipt_type,
            "raw_ai_response": raw_output,
            "parsed_json": parsed_json
        }
    except requests.exceptions.Timeout:
        return {
            "status": "error",
            "filename": filename,
            "error_details": f"Timed out waiting on {OLLAMA_MODEL} (>{REQUEST_TIMEOUT}s)"
        }
    except Exception as e:
        return {
            "status": "error",
            "filename": filename,
            "error_details": str(e)
        }

@app.get("/api/pending-files")
async def get_pending_files():
    if not os.path.exists(UPLOAD_DIR):
        return {"files": []}
    files = [f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(VALID_EXTENSIONS)]
    return {"files": files}

@app.get("/api/deposits")
async def get_deposits(search: str = "", sort: str = "recent"):
    """
    sort options:
      recent   -- newest first (default)
      sequence -- by sequence/folio number
      sender   -- alphabetical by sender name
      flagged  -- flagged senders first, then alphabetical by sender name
    """
    db_session = SessionLocal()
    try:
        query = db_session.query(Deposit)
        if search:
            query = query.filter(Deposit.sender_name.ilike(f"%{search}%"))

        if sort == "sequence":
            query = query.order_by(Deposit.sequence_number.asc())
        elif sort == "sender":
            query = query.order_by(Deposit.sender_name.asc())
        elif sort == "flagged":
            query = query.order_by(Deposit.is_flagged.desc(), Deposit.sender_name.asc())
        elif sort == "flag-view":
            # Flagged only, sorted by sender so monthly totals group naturally —
            # the frontend does the final sort by monthly total descending after
            # it merges in the sender_monthly_totals data.
            query = query.filter(Deposit.is_flagged == True).order_by(Deposit.sender_name.asc())
        else:
            query = query.order_by(Deposit.id.desc())

        records = query.all()
        
        result = []
        for row in records:
            result.append({
                "id": row.id,
                "date_string": row.date_string,
                "sequence_number": row.sequence_number,
                "sender_name": row.sender_name,
                "amount": row.amount,
                "recipient_name": row.recipient_name,
                "receipt_type": row.receipt_type,
                "image_filename": row.image_filename,
                "is_flagged": row.is_flagged,
                "is_canceled": row.is_canceled,
                "created_at": str(row.created_at)
            })
        return {"deposits": result}
    finally:
        db_session.close()

@app.get("/api/export-csv")
async def export_csv(filename: str = "receipts_export"):
    safe_name = sanitize_filename(filename)
    db_session = SessionLocal()
    try:
        records = db_session.query(Deposit).order_by(Deposit.id.asc()).all()
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Date", "Sequence/Folio No.", "Sender Name", "Amount (USD)", "Recipient Name", "Type", "Flagged", "Canceled", "Logged At"])
        
        for r in records:
            writer.writerow([r.id, r.date_string, r.sequence_number, r.sender_name, r.amount, r.recipient_name, r.receipt_type, r.is_flagged, r.is_canceled, r.created_at])
            
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode('utf-8')),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.csv"'}
        )
    finally:
        db_session.close()

@app.post("/api/save-snapshot")
async def save_snapshot(filename: str = ""):
    """
    Saves a matched pair into snapshots/:
      - a CSV export of the current database
      - a copy of receipts.db itself
    Both files share the same base name so you can restore the exact DB state
    or reconstruct from the CSV alone.
    """
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_label = "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_')).strip()
    base_name = f"{safe_label}_{timestamp}" if safe_label else timestamp

    csv_path = os.path.join(SNAPSHOT_DIR, f"{base_name}.csv")
    db_session = SessionLocal()
    try:
        records = db_session.query(Deposit).order_by(Deposit.id.asc()).all()
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "Date", "Sequence/Folio No.", "Sender Name", "Amount (USD)", "Recipient Name", "Type", "Flagged", "Canceled", "Logged At"])
            for r in records:
                writer.writerow([r.id, r.date_string, r.sequence_number, r.sender_name, r.amount, r.recipient_name, r.receipt_type, r.is_flagged, r.is_canceled, r.created_at])
    finally:
        db_session.close()

    db_copy_path = os.path.join(SNAPSHOT_DIR, f"{base_name}.db")
    shutil.copy2(DB_PATH, db_copy_path)

    return {"status": "success", "snapshot_name": base_name, "csv": csv_path, "db": db_copy_path}

@app.get("/api/list-snapshots")
async def list_snapshots():
    db_files = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*.db")), reverse=True)
    snapshots = []
    for db_file in db_files:
        base = os.path.splitext(os.path.basename(db_file))[0]
        csv_file = os.path.join(SNAPSHOT_DIR, f"{base}.csv")
        snapshots.append({"name": base, "has_csv": os.path.exists(csv_file), "has_db": True})
    return {"snapshots": snapshots}

@app.post("/api/restore-snapshot/{snapshot_name}")
async def restore_snapshot(snapshot_name: str):
    db_copy = os.path.join(SNAPSHOT_DIR, f"{snapshot_name}.db")
    if not os.path.exists(db_copy):
        raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_name}' not found.")
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, f"{DB_PATH}.bak")
    shutil.copy2(db_copy, DB_PATH)
    return {"status": "success", "message": f"Restored from '{snapshot_name}'. Previous DB saved as receipts.db.bak."}

@app.get("/api/download-snapshot-csv/{snapshot_name}")
async def download_snapshot_csv(snapshot_name: str):
    csv_path = os.path.join(SNAPSHOT_DIR, f"{snapshot_name}.csv")
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="CSV for this snapshot not found.")
    return FileResponse(csv_path, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={snapshot_name}.csv"})

@app.post("/api/clear-database")
async def clear_database_endpoint():
    clear_all_deposits()
    return {"status": "success", "message": "Database cleared."}