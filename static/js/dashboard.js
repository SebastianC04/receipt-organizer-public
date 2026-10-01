"use strict";
// DOM Interfacing Elements with Type Safety
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const statusDiv = document.getElementById('upload-status');
// Changed to optional checking so missing layout blocks don't drop fatal engine crashes
const monthlyBody = document.getElementById('monthly-table-body');
const flaggedBody = document.getElementById('flagged-table-body');
const processBtn = document.getElementById('process-batch-btn');
const statusText = document.getElementById('batch-status-text');
const receiptTypeSelect = document.getElementById('receipt-type-select');
// The batch runs in the background on the server and takes ~10s per receipt,
// so "done" means the pending queue has emptied -- not that the request returned.
const POLL_MS = 2000;
// Longer than the server's per-receipt model timeout (120s), so one slow
// receipt isn't mistaken for a stuck batch.
const STALL_MS = 5 * 60 * 1000;
async function countPendingFiles() {
    const response = await fetch('/api/pending-files');
    if (!response.ok)
        throw new Error(`pending-files: ${response.status}`);
    const data = await response.json();
    return (data.files || []).length;
}
async function refreshAfterBatch() {
    const page = window;
    if (typeof fetchAnalytics === 'function')
        await fetchAnalytics();
    if (typeof page.loadDatabaseRecords === 'function')
        await page.loadDatabaseRecords();
    if (typeof page.loadPendingDropdown === 'function')
        await page.loadPendingDropdown();
}
if (processBtn) {
    processBtn.addEventListener('click', async () => {
        const receiptType = receiptTypeSelect ? receiptTypeSelect.value : 'ria';
        const setStatus = (text) => { if (statusText)
            statusText.innerText = text; };
        processBtn.disabled = true;
        try {
            const total = await countPendingFiles();
            if (total === 0) {
                setStatus("No files in pending_scans to process.");
                return;
            }
            setStatus(`Processing ${total} receipt(s) as '${receiptType}' with Qwen2.5...`);
            const response = await fetch(`/api/process-batch?receipt_type=${encodeURIComponent(receiptType)}`, { method: 'POST' });
            if (!response.ok)
                throw new Error("Batch processing failed to initialize.");
            let remaining = total;
            let lastProgressAt = Date.now();
            while (remaining > 0) {
                await new Promise(resolve => setTimeout(resolve, POLL_MS));
                const now = await countPendingFiles();
                if (now !== remaining) {
                    remaining = now;
                    lastProgressAt = Date.now();
                    setStatus(`Processing as '${receiptType}'... ${remaining} of ${total} left`);
                }
                else if (Date.now() - lastProgressAt > STALL_MS) {
                    await refreshAfterBatch();
                    setStatus(`No progress for 5 minutes -- ${remaining} file(s) still pending. Check the server window for errors.`);
                    return;
                }
            }
            await refreshAfterBatch();
            setStatus(`Done: ${total} receipt(s) processed. Data updated (anything suspicious is in needs_review).`);
        }
        catch (err) {
            setStatus("Error running batch processing.");
        }
        finally {
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
async function fetchAnalytics() {
    try {
        const response = await fetch('/api/analytics');
        if (!response.ok)
            throw new Error('Data fetch failed');
        const data = await response.json();
        renderTables(data.monthly_aggregates, data.vip_clients);
    }
    catch (err) {
        console.error('Failed to sync UI metrics:', err);
    }
}
// Write API arrays into UI DOM structures cleanly
function renderTables(monthlyData, flaggedData) {
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
function setupDragAndDrop() {
    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
        if (fileInput.files)
            handleFiles(fileInput.files);
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
        if (dt && dt.files)
            handleFiles(dt.files);
    });
}
// Stream payloads up to server AKA UPLOADS
async function handleFiles(files) {
    for (let i = 0; i < files.length; i++) {
        let file = files[i];
        // 1. Check if the file is HEIC
        const fileName = file.name.toLowerCase();
        if (fileName.endsWith('.heic') || fileName.endsWith('.heif')) {
            // Update UI to show conversion is happening
            if (statusText)
                statusText.innerText = `Converting ${file.name} to JPG...`;
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
            }
            catch (err) {
                console.error("HEIC conversion failed:", err);
                if (statusText)
                    statusText.innerText = `Error converting ${file.name}`;
                continue; // Skip uploading this broken file
            }
        }
        // 4. Proceed with standard upload
        if (statusText)
            statusText.innerText = `Uploading ${file.name}...`;
        const formData = new FormData();
        formData.append("file", file);
        await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });
    }
    if (statusText)
        statusText.innerText = "Upload complete! Ready to process queue.";
}
