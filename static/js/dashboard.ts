// DOM Interfacing Elements with Type Safety
const dropZone = document.getElementById('drop-zone') as HTMLDivElement;
const fileInput = document.getElementById('file-input') as HTMLInputElement;
const statusDiv = document.getElementById('upload-status') as HTMLDivElement;

// Changed to optional checking so missing layout blocks don't drop fatal engine crashes
const monthlyBody = document.getElementById('monthly-table-body') as HTMLTableSectionElement | null;
const flaggedBody = document.getElementById('flagged-table-body') as HTMLTableSectionElement | null;

const processBtn = document.getElementById('process-batch-btn') as HTMLButtonElement | null;
const statusText = document.getElementById('batch-status-text') as HTMLSpanElement | null;
const receiptTypeSelect = document.getElementById('receipt-type-select') as HTMLSelectElement | null;

declare var heic2any: any;

if (processBtn) {
    processBtn.addEventListener('click', async () => {
        const receiptType = receiptTypeSelect ? receiptTypeSelect.value : 'ria';
        if (statusText) statusText.innerText = `Processing batch as '${receiptType}' with Qwen2.5...`;
        processBtn.disabled == true;

        try {
            const response = await fetch(`/api/process-batch?receipt_type=${encodeURIComponent(receiptType)}`, { method: 'POST' });
            if (!response.ok) throw new Error("Batch processing failed to initialize.");

            // Poll or delay briefly before updating analytics
            setTimeout(async () => {
                if (typeof fetchAnalytics === 'function') await fetchAnalytics();
                if (statusText) statusText.innerText = "Processing complete! Data updated.";
                processBtn.disabled = false;
            }, 3000);
        }
        catch (err) {
            if (statusText) statusText.innerText = "Error running batch processing.";
            processBtn.disabled = false; 
        }
    });
}

// Initialization Lifecycle 
document.addEventListener('DOMContentLoaded', () => {
    fetchAnalytics();
    setupDragAndDrop();
});

// Fetch metrics data from local FastAPI Server
async function fetchAnalytics(): Promise<void> {
    try {
        const response = await fetch('/api/analytics');
        if (!response.ok) throw new Error('Data fetch failed');
        const data = await response.json();
        
        renderTables(data.monthly_aggregates, data.vip_clients);
    } catch (err) {
        console.error('Failed to sync UI metrics:', err);
    }
}

// Write API arrays into UI DOM structures cleanly
function renderTables(monthlyData: any[], flaggedData: any[]): void {
    
    // 1. Wrap monthlyBody in an if check to clear the first error
    if (monthlyBody) {
        monthlyBody.innerHTML = monthlyData.length ? monthlyData.map(row => `
            <tr class="hover:bg-gray-700/30">
                <td class="py-2.5">${row.month}</td>
                <td class="py-2.5 text-right font-mono text-emerald-400">$${row.total.toFixed(2)}</td>
            </tr>
        `).join('') : '<tr><td colspan="2" class="text-center py-4 text-gray-500">No records parsed.</td></tr>';
    }

    // 2. Wrap flaggedBody in an if check to clear the second error
    if (flaggedBody) {
        flaggedBody.innerHTML = flaggedData.length ? flaggedData.map(row => `
            <tr class="hover:bg-amber-950/20">
                <td class="py-2.5 font-medium text-amber-200">${row.client_name}</td>
                <td class="py-2.5 text-right font-mono text-amber-400">$${row.amount.toFixed(2)}</td>
            </tr>
        `).join('') : '<tr><td colspan="2" class="text-center py-4 text-gray-500">No high volume thresholds crossed.</td></tr>';
    }
}


// Wire drag and drop framework event emitters
function setupDragAndDrop(): void {
    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
        if (fileInput.files) handleFiles(fileInput.files);
    });

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            dropZone.classList.add('border-emerald-500', 'bg-gray-800');
        }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            dropZone.classList.remove('border-emerald-500', 'bg-gray-800');
        }, false);
    });

    dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        if (dt && dt.files) handleFiles(dt.files);
    });
}

// Stream payloads up to server AKA UPLOADS
async function handleFiles(files: FileList) {
    for (let i = 0; i < files.length; i++) {
        let file = files[i];
        
        // 1. Check if the file is HEIC
        const fileName = file.name.toLowerCase();
        if (fileName.endsWith('.heic') || fileName.endsWith('.heif')) {
            // Update UI to show conversion is happening
            if (statusText) statusText.innerText = `Converting ${file.name} to JPG...`;
            
            try {
                // 2. Convert to JPEG blob
                const convertedBlob = await heic2any({
                    blob: file,
                    toType: "image/jpeg",
                    quality: 0.8
                });
                
                // heic2any can return an array if there are multiple frames, we just want the first
                const finalBlob = Array.isArray(convertedBlob) ? convertedBlob[0] : convertedBlob;
                
                // 3. Swap the original file for the new JPG
                const newName = file.name.replace(/\.[^/.]+$/, ".jpg");
                file = new File([finalBlob], newName, { type: "image/jpeg" });
                
            } catch (err) {
                console.error("HEIC conversion failed:", err);
                if (statusText) statusText.innerText = `Error converting ${file.name}`;
                continue; // Skip uploading this broken file
            }
        }

        // 4. Proceed with standard upload
        if (statusText) statusText.innerText = `Uploading ${file.name}...`;
        const formData = new FormData();
        formData.append("file", file);

        await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });
    }
    
    if (statusText) statusText.innerText = "Upload complete! Ready to process queue.";
}