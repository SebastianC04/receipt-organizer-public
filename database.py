import os
import sqlite3
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

# 1. Define the local storage file path
DATABASE_URL = "sqlite:///receipts.db"

# 2. Initialize the database communication engines
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 3. Define the Database Table Structure (The Schema)
class Deposit(Base):
    __tablename__ = "deposits"

    id = Column(Integer, primary_key=True, index=True)
    date_string = Column(String, nullable=False)        # 1. Date (stored normalized as YYYY-MM-DD)
    sequence_number = Column(String, nullable=True, index=True)  # 2. Sequence Number / Receipt-Folio #
    order_number = Column(String, nullable=True, index=True)  # Ria Order No / Transaction No -- the only ID shared between a Ria refund and the original it refers to (Seq No never appears on a Refund receipt)
    sender_name = Column(String, nullable=False, index=True) # 3. Sender Name
    amount = Column(Float, nullable=False)              # 4. Total USD
    recipient_name = Column(String, nullable=True)     # 5. Recipient Name
    receipt_type = Column(String, nullable=False, default="ria")  # "ria" or "maxi"
    image_filename = Column(String, nullable=True)     # Archived scan filename, for drill-down/image viewing
    is_flagged = Column(Boolean, default=False)
    is_canceled = Column(Boolean, default=False)  # Set when a cancellation/refund/void references or is this transaction
    cancellation_type = Column(String, nullable=True)  # "void", "refund", or None
    references_sequence_number = Column(String, nullable=True)  # Original transaction this record's cancellation points to, if any
    created_at = Column(DateTime, default=datetime.utcnow)

# 4. Helper function to initialize the physical database file safely
def init_db():
    Base.metadata.create_all(bind=engine)
    print("Database system initialized with updated schema. 'receipts.db' verified.")

# 5. Helper function to wipe the deposits table on demand without dropping the schema
def clear_all_deposits():
    db_session = SessionLocal()
    try:
        db_session.query(Deposit).delete()
        db_session.commit()
        print("Database table 'deposits' cleared successfully.")
    except Exception as e:
        db_session.rollback()
        print(f"Error clearing deposits table: {e}")
    finally:
        db_session.close()

if __name__ == "__main__":
    init_db()