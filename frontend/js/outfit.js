// js/outfit.js

// --- STATE VARIABLES ---
let outfitTopId = null;
let outfitBottomId = null;
let outfitOuterwearId = null;

// --- DOM ELEMENTS ---
const btnSelectOutfitTop = document.getElementById("btnSelectOutfitTop");
const btnSelectOutfitBottom = document.getElementById("btnSelectOutfitBottom");
const btnSelectOutfitOuterwear = document.getElementById("btnSelectOutfitOuterwear");
const btnGenerateOutfit = document.getElementById("btnGenerateOutfit");
const outfitPersonStatus = document.getElementById("outfitPersonStatus");

// --- CLOSET ROUTING EVENT LISTENERS ---
btnSelectOutfitTop.addEventListener("click", () => {
    closetSelectionMode = 'outfit-top';
    switchMainView('closet');
});

btnSelectOutfitBottom.addEventListener("click", () => {
    closetSelectionMode = 'outfit-bottom';
    switchMainView('closet');
});

btnSelectOutfitOuterwear.addEventListener("click", () => {
    closetSelectionMode = 'outfit-outerwear';
    switchMainView('closet');
});

// --- HANDLE SELECTION FROM CLOSET ---
// This is called from app.js when an item is clicked in outfit mode
function routeOutfitSelection(item, mode) {
    if (mode === 'outfit-top') {
        outfitTopId = item.id;
        document.getElementById("outfitTopImg").src = `${API_BASE_URL}${item.image_url}`;
        document.getElementById("outfitTopPreview").classList.remove("hidden");
        btnSelectOutfitTop.classList.add("hidden");
    } else if (mode === 'outfit-bottom') {
        outfitBottomId = item.id;
        document.getElementById("outfitBottomImg").src = `${API_BASE_URL}${item.image_url}`;
        document.getElementById("outfitBottomPreview").classList.remove("hidden");
        btnSelectOutfitBottom.classList.add("hidden");
    } else if (mode === 'outfit-outerwear') {
        outfitOuterwearId = item.id;
        document.getElementById("outfitOuterwearImg").src = `${API_BASE_URL}${item.image_url}`;
        document.getElementById("outfitOuterwearPreview").classList.remove("hidden");
        btnSelectOutfitOuterwear.classList.add("hidden");
    }
    
    switchMainView('outfit');
    validateOutfitForm();
}

// --- REMOVE SELECTIONS ---
document.getElementById("btnRemoveOutfitTop").addEventListener("click", () => {
    outfitTopId = null;
    document.getElementById("outfitTopPreview").classList.add("hidden");
    btnSelectOutfitTop.classList.remove("hidden");
    validateOutfitForm();
});

document.getElementById("btnRemoveOutfitBottom").addEventListener("click", () => {
    outfitBottomId = null;
    document.getElementById("outfitBottomPreview").classList.add("hidden");
    btnSelectOutfitBottom.classList.remove("hidden");
    validateOutfitForm();
});

document.getElementById("btnRemoveOutfitOuterwear").addEventListener("click", () => {
    outfitOuterwearId = null;
    document.getElementById("outfitOuterwearPreview").classList.add("hidden");
    btnSelectOutfitOuterwear.classList.remove("hidden");
    validateOutfitForm();
});

// --- VALIDATION & GENERATION ---
function validateOutfitForm() {
    // Read capturedBlob globally from app.js
    if (typeof capturedBlob !== 'undefined' && capturedBlob !== null) {
        outfitPersonStatus.innerText = "✅ Base photo ready for generation.";
        outfitPersonStatus.style.color = "green";
        outfitPersonStatus.parentNode.style.borderLeftColor = "#48bb78";
        
        // Ensure at least one garment is selected
        const hasGarment = (outfitTopId || outfitBottomId || outfitOuterwearId);
        btnGenerateOutfit.disabled = !hasGarment;
    } else {
        outfitPersonStatus.innerText = "❌ Base photo missing. Please capture one in the Studio tab first.";
        outfitPersonStatus.style.color = "#c53030";
        outfitPersonStatus.parentNode.style.borderLeftColor = "#e53e3e";
        btnGenerateOutfit.disabled = true;
    }
}

// --- VALIDATION & GENERATION ---
// Phase 4 Generation Payload Builder & Polling Logic
btnGenerateOutfit.addEventListener("click", async () => {
    const formData = new FormData();
    
    // Attach Base Image
    const personFile = (capturedBlob instanceof File) ? capturedBlob : new File([capturedBlob], "capture.jpg", { type: "image/jpeg" });
    formData.append("person_image", personFile);
    
    // Attach Selected IDs
    if (outfitTopId) formData.append("top_closet_id", outfitTopId);
    if (outfitBottomId) formData.append("bottom_closet_id", outfitBottomId);
    if (outfitOuterwearId) formData.append("outerwear_closet_id", outfitOuterwearId);
    
    formData.append("outfit_desc", document.getElementById("outfitPrompt").value);

    // Disable UI and show loading state
    btnGenerateOutfit.disabled = true;
    outfitPersonStatus.innerText = "Transmitting to Outfit Engine...";
    outfitPersonStatus.style.color = "#d69e2e"; // Yellow-ish for pending
    outfitPersonStatus.parentNode.style.borderLeftColor = "#d69e2e";

    try {
        const response = await fetch(`${API_BASE_URL}/api/outfit/generate`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${authToken}`
            },
            body: formData
        });

        if (!response.ok) throw new Error(`Server Error: ${response.status}`);
        
        const initialJobData = await response.json();
        pollOutfitJobStatus(initialJobData.id);

    } catch (err) {
        console.error("Submission Error", err.message);
        alert("Error executing outfit job.");
        resetOutfitUi();
    }
});

function pollOutfitJobStatus(jobId) {
    outfitPersonStatus.innerText = "AI processing layers... This may take a minute...";
    let pollAttempts = 0;

    const intervalId = setInterval(async () => {
        pollAttempts++;
        try {
            const response = await fetch(`${API_BASE_URL}/api/outfit/${jobId}`, {
                headers: { "Authorization": `Bearer ${authToken}` }
            });
            
            if (!response.ok) return;

            const job = await response.json();
            
            if (job.status === "processing") {
                outfitPersonStatus.innerText = `Rendering Layer Sequence (Attempt ${pollAttempts})...`;
            } else if (job.status === "completed") {
                clearInterval(intervalId);
                displayFinalOutfit(job.result_image_url);
            } else if (job.status === "failed") {
                clearInterval(intervalId);
                alert("The AI model was unable to map this complex outfit layout.");
                resetOutfitUi();
            }
        } catch (err) {
            console.error("Polling error", err.message);
        }
    }, 4000); // Polling every 4 seconds to reduce local DB strain
}

function displayFinalOutfit(url) {
    // Hide the slot selection grid
    document.querySelector("#outfitViewPanel .control-panel .card").classList.add("hidden");
    
    // Update the right-side card to show the final result
    const resultArea = document.querySelector("#outfitViewPanel .output-panel .card");
    resultArea.innerHTML = `
        <h3>3. Your Custom Outfit</h3>
        <img src="${url}" alt="Final Outfit" style="width: 100%; border-radius: 8px; margin-top: 15px;">
        <button id="btnResetOutfitView" class="secondary-btn" style="margin-top: 15px;">Build Another Look</button>
    `;

    // Bind the reset button
    document.getElementById("btnResetOutfitView").addEventListener("click", () => {
        location.reload(); // Quickest way to reset all states for a fresh build
    });
}

function resetOutfitUi() {
    outfitPersonStatus.innerText = "✅ Base photo ready for generation.";
    outfitPersonStatus.style.color = "green";
    outfitPersonStatus.parentNode.style.borderLeftColor = "#48bb78";
    btnGenerateOutfit.disabled = false;
    btnGenerateOutfit.innerText = "Generate Full Outfit";
}