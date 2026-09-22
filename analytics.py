from sqlalchemy import func
from database import SessionLocal, Deposit

def find_high_value_clients_data(db_session=None):
    """
    Fetches aggregated monthly deposit data for senders whose monthly total
    exceeds $3,000.00 (across all receipt types combined).
    Returns a list of dicts for the web UI, or prints to terminal if run directly.
    """
    session_owner = False
    if db_session is None:
        db_session = SessionLocal()
        session_owner = True

    try:
        results = db_session.query(
            Deposit.sender_name,
            func.substr(Deposit.date_string, 1, 7).label('calendar_month'),
            func.sum(Deposit.amount).label('total_deposited')
        ).group_by(
            Deposit.sender_name,
            'calendar_month'
        ).having(
            func.sum(Deposit.amount) > 3000.00
        ).order_by(
            'calendar_month',
            func.sum(Deposit.amount).desc()
        ).all()

        report_list = []
        for row in results:
            report_list.append({
                "client_name": row.sender_name if row.sender_name else "Unknown Client",
                "month": row.calendar_month,
                "amount": row.total_deposited
            })

        return report_list

    except Exception as e:
        print(f"[Query Error Execution]: {e}")
        return []
    finally:
        if session_owner:
            db_session.close()


def get_sender_month_transactions(sender_name, month, db_session=None):
    """
    Returns every individual transaction for a given sender in a given
    calendar month (format 'YYYY-MM'), including the archived image filename
    and receipt_type so the UI can link back to the original scan and show
    which service (Ria/Maxi) it came from. Powers the flagged-sender
    drill-down view.
    """
    session_owner = False
    if db_session is None:
        db_session = SessionLocal()
        session_owner = True

    try:
        results = db_session.query(Deposit).filter(
            Deposit.sender_name == sender_name,
            func.substr(Deposit.date_string, 1, 7) == month
        ).order_by(Deposit.date_string).all()

        return [
            {
                "id": r.id,
                "date_string": r.date_string,
                "sequence_number": r.sequence_number,
                "amount": r.amount,
                "recipient_name": r.recipient_name,
                "receipt_type": r.receipt_type,
                "image_filename": r.image_filename,
                "is_flagged": r.is_flagged,
                "is_canceled": r.is_canceled,
            }
            for r in results
        ]
    except Exception as e:
        print(f"[Sender Transaction Query Error]: {e}")
        return []
    finally:
        if session_owner:
            db_session.close()


def flag_senders_by_monthly_total(db_session=None):
    """
    Flags every deposit belonging to a sender whose total deposits (across
    Ria and Maxi combined) exceed $3,000.00 in a given calendar month.
    Relies on date_string already being normalized to YYYY-MM-DD.
    """
    session_owner = False
    if db_session is None:
        db_session = SessionLocal()
        session_owner = True

    try:
        sender_totals = {}
        results = db_session.query(
            Deposit.sender_name,
            func.substr(Deposit.date_string, 1, 7).label('calendar_month'),
            func.sum(Deposit.amount).label('total_deposited')
        ).group_by(
            Deposit.sender_name,
            'calendar_month'
        ).having(
            func.sum(Deposit.amount) > 3000.00
        ).all()

        for row in results:
            sender_key = (row.calendar_month, row.sender_name)
            sender_totals[sender_key] = row.total_deposited

        for (month, sender_name) in sender_totals:
            db_session.query(Deposit).filter(
                func.substr(Deposit.date_string, 1, 7) == month,
                Deposit.sender_name == sender_name
            ).update({"is_flagged": True})

        db_session.commit()

    except Exception as e:
        print(f"[Flagging Error Execution]: {e}")
    finally:
        if session_owner:
            db_session.close()


if __name__ == "__main__":
    print("Running terminal diagnostic query...")
    data = find_high_value_clients_data()
    print("=" * 65)
    print(f"{'CLIENT NAME':<30} | {'MONTH':<10} | {'TOTAL DEPOSITED':<15}")
    print("=" * 65)
    for item in data:
        print(f"{item['client_name']:<30} | {item['month']:<10} | ${item['amount']:,.2f}")
    print("=" * 65)