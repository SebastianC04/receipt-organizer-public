import os
import random
from PIL import Image, ImageDraw

os.makedirs("pending_scans", exist_ok=True)

senders = ["GERARDO VAZQUEZ RAMOS", "MARIA GARCIA", "JOHN SMITH", "ANA MARTINEZ"]
recipients = ["SARA LEON ROSAS", "CARLOS HERNANDEZ", "MIGUEL ANGEL", "LUCIA FERNANDEZ"]

print("Generating 15 Ria-style mock receipts...")

for i in range(1, 16):
    img = Image.new('RGB', (600, 800), color='white')
    draw = ImageDraw.Draw(img)
    
    sender = random.choice(senders)
    recipient = random.choice(recipients)
    
    # Intentionally create anomalies: 20% chance to be a high-value flagged transfer
    is_anomaly = random.random() < 0.2 
    transfer_amount = round(random.uniform(1200, 2500), 2) if is_anomaly else round(random.uniform(50, 400), 2)
    fees = 10.00
    taxes = round(transfer_amount * 0.01, 2)
    total = transfer_amount + fees + taxes
    
    receipt_text = f"""
    ria | Dandelion Payments, Inc. dba Ria Money Transfer
    --------------------------------------------------
    Order No. / No. Orden       US52629{random.randint(10000, 99999)}
    Order Date / Fecha de Orden 8/20/2026 17:48
    
    SENDER / CLIENTE
    {sender}
    5626 Owens Dr, Pleasanton CA 94588
    
    Transfer Amount             {transfer_amount:.2f} USD
    Transfer Fees               {fees:.2f} USD
    Transfer Taxes              {taxes:.2f} USD
    Total                       {total:.2f} USD
    
    RECIPIENT / BENEFICIARIO
    {recipient}
    TLACHICHUCA, Puebla. Mexico
    """
    
    # Draw text (using default bitmap font for simplicity)
    draw.text((30, 30), receipt_text, fill="black")
    img.save(f"pending_scans/ria_mock_{i}.jpg")

print("✅ 15 Mock receipts generated in 'pending_scans'!")