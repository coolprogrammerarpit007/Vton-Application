const API_BASE_URL = "https://vton-backend.falcondetectives.com"; // Update this to your deployed backend URL
const DEBUG_MODE = true; 

// --- NEW STATE VARIABLES ---
let authToken = localStorage.getItem("vton_token");
let selectedClosetItemId = null;
let closetSelectionMode = 'studio'; // NEW: Tracks why the closet is open
let isLoginMode = true;
let currentStream = null;
let useFacingMode = "user"; 
let capturedBlob = null; 

// ==========================================
// Debugging Utility Functions
// ==========================================
function logDebug(action, data = null) {
    if (!DEBUG_MODE) return;
    const time = new Date().toISOString().split('T')[1].slice(0, -1);
    if (data) {
        console.log(`[${time}]  VTON: ${action}`, data);
    } else {
        console.log(`[${time}]  VTON: ${action}`);
    }
}

function logError(action, err) {
    const time = new Date().toISOString().split('T')[1].slice(0, -1);
    console.error(`[${time}]  VTON ERROR: ${action}`, err);
}

// ==========================================
// DOM Elements Binding
// ==========================================
const video = document.getElementById("videoStream");
const canvas = document.getElementById("captureCanvas");
const personPreview = document.getElementById("personPreview");
const finalOutputImage = document.getElementById("finalOutputImage");

const cameraView = document.getElementById("cameraView");
const uploadView = document.getElementById("uploadView");
const personPreviewContainer = document.getElementById("personPreviewContainer");
const loadingEngine = document.getElementById("loadingEngine");
const placeholderText = document.getElementById("placeholderText");
const loadingStatusText = document.getElementById("loadingStatusText");

const btnTabCamera = document.getElementById("btnTabCamera");
const btnTabUpload = document.getElementById("btnTabUpload");
const btnFlipCamera = document.getElementById("btnFlipCamera");
const btnCapture = document.getElementById("btnCapture");
const btnResetPerson = document.getElementById("btnResetPerson");
const btnGenerate = document.getElementById("btnGenerate");
const garmentCategory = document.getElementById("garmentCategory");
const garmentDescription = document.getElementById("garmentDescription"); 
const userFileInput = document.getElementById("userFileInput");
const garmentFileInput = document.getElementById("garmentFileInput");

// New SPA DOM Elements
const authModal = document.getElementById("authModal");
const mainNav = document.getElementById("main-nav");
const studioView = document.getElementById("studioView");
const closetViewPanel = document.getElementById("closetViewPanel");
const historyViewPanel = document.getElementById("historyViewPanel");


// Navigation Buttons
const navStudio = document.getElementById("navStudio");
const navCloset = document.getElementById("navCloset");
const navHistory = document.getElementById("navHistory");
const btnLogout = document.getElementById("btnLogout");

const btnUploadToCloset = document.getElementById("btnUploadToCloset");
const closetFileInput = document.getElementById("closetFileInput");
const closetItemLabel = document.getElementById("closetItemLabel");

const selectedGarmentPreview = document.getElementById("selectedGarmentPreview");
const garmentPreviewImg = document.getElementById("garmentPreviewImg");
const btnRemoveGarment = document.getElementById("btnRemoveGarment");
const garmentInputGroup = document.getElementById("garmentInputGroup");


//  Multi Angle View

const nav360 = document.getElementById("nav360");
const view360Panel = document.getElementById("view360Panel");

nav360.addEventListener("click", () => switchMainView('360'));


// ==========================================
// Application Initialization & SPA Routing
// ==========================================
// 1. Update initApp to fetch username
async function initApp() {
    if (authToken) {
        authModal.classList.add("hidden");
        mainNav.classList.remove("hidden");
        
        // Fetch username
        try {
            const res = await fetch(`${API_BASE_URL}/api/auth/me`, {
                headers: { "Authorization": `Bearer ${authToken}` }
            });
            if (res.ok) {
                const userData = await res.json();
                document.getElementById("displayUsername").innerText = userData.username;
                document.getElementById("userProfileDisplay").classList.remove("hidden");
            }
        } catch (e) { logError("Failed to fetch user profile", e); }
        
        switchMainView('studio');
    } else {
        authModal.classList.remove("hidden");
        mainNav.classList.add("hidden");
        document.getElementById("userProfileDisplay").classList.add("hidden");
        stopCameraStream(); 
    }
}

// 2. Update Logout Listener
btnLogout.addEventListener("click", async () => {
    // Notify server of logout
    try {
        await fetch(`${API_BASE_URL}/api/auth/logout`, {
            method: "POST",
            headers: { "Authorization": `Bearer ${authToken}` }
        });
    } catch (e) { logError("Logout API call failed", e); }

    // Clear local state
    localStorage.removeItem("vton_token");
    authToken = null;
    initApp();
});

// ADD THIS EVENT LISTENER
// This listens for a click on the 'X' and hides the modal
document.getElementById("closeAuthModal").addEventListener("click", () => {
    document.getElementById("authModal").classList.add("hidden");
    
    // Optional: Re-show the main nav and studio view so they can use the app unauthenticated
    document.getElementById("main-nav").classList.remove("hidden"); 
    switchMainView('studio');
});

// ***************** Outfit Builder *****************
const navOutfit = document.getElementById("navOutfit");
const outfitViewPanel = document.getElementById("outfitViewPanel");

navOutfit.addEventListener("click", () => switchMainView('outfit'));

function switchMainView(view) {
    const panels = [studioView, closetViewPanel, historyViewPanel, outfitViewPanel, view360Panel];
    const navs = [navStudio, navCloset, navHistory, navOutfit, nav360];

    navs.forEach(nav => nav.classList.remove("active"));
    panels.forEach(panel => panel.classList.add("hidden"));

    if (view === 'studio') {
        studioView.classList.remove("hidden");
        navStudio.classList.add("active");
        if (!capturedBlob && btnTabCamera.classList.contains("active")) startCamera();
    } else if (view === '360') {
        stopCameraStream();
        view360Panel.classList.remove("hidden");
        nav360.classList.add("active");
    } else if (view === 'closet') {
        stopCameraStream();
        closetViewPanel.classList.remove("hidden");
        navCloset.classList.add("active");
        loadClosetGallery();
    } else if (view === 'history') {
        stopCameraStream();
        historyViewPanel.classList.remove("hidden");
        navHistory.classList.add("active");
        loadHistoryGallery();
    } else if (view === 'outfit') {
        stopCameraStream();
        outfitViewPanel.classList.remove("hidden");
        navOutfit.classList.add("active");
        if (typeof validateOutfitForm === "function") validateOutfitForm();
    }
}

// Navigation Listeners
navStudio.addEventListener("click", () => switchMainView('studio'));
navCloset.addEventListener("click", () => switchMainView('closet'));
navHistory.addEventListener("click", () => switchMainView('history'));

// ==========================================
// Authentication Logic
// ==========================================
document.getElementById("toggleAuthMode").addEventListener("click", () => {
    isLoginMode = !isLoginMode;
    const title = document.getElementById("authTitle");
    const btn = document.getElementById("authSubmitBtn");
    const toggle = document.getElementById("toggleAuthMode");
    const userGroup = document.getElementById("usernameGroup");
    const userInput = document.getElementById("authUsername");

    if (isLoginMode) {
        title.innerText = "Login to VTON";
        btn.innerText = "Login";
        toggle.innerText = "Need an account? Register";
        userGroup.classList.add("hidden");
        userInput.required = false;
    } else {
        title.innerText = "Create Account";
        btn.innerText = "Register";
        toggle.innerText = "Already have an account? Login";
        userGroup.classList.remove("hidden");
        userInput.required = true;
    }
});

document.getElementById("authForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = document.getElementById("authEmail").value;
    const password = document.getElementById("authPassword").value;
    const username = document.getElementById("authUsername").value;
    
    const endpoint = isLoginMode ? "/api/auth/login" : "/api/auth/register";
    const payload = isLoginMode ? { email, password } : { username, email, password };

    try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        
        if (response.ok) {
            authToken = data.access_token;
            localStorage.setItem("vton_token", authToken);
            initApp(); 
        } else {
            alert(data.detail || "Authentication failed");
        }
    } catch (err) {
        logError("Auth Error", err);
        alert("Server communication error.");
    }
});

btnLogout.addEventListener("click", () => {
    localStorage.removeItem("vton_token");
    authToken = null;
    initApp();
});


// --- FETCH HISTORY GALLERY ---
async function loadHistoryGallery() {
    const gallery = document.getElementById("historyGallery");
    gallery.innerHTML = "<p>Loading your history...</p>";

    try {
        const response = await fetch(`${API_BASE_URL}/api/history/`, {
            headers: { "Authorization": `Bearer ${authToken}` }
        });
        const items = await response.json();

        if (items.length === 0) {
            gallery.innerHTML = "<p>Your past AI outfits will appear here.</p>";
            return;
        }

        gallery.innerHTML = "";
        items.forEach(item => {
            const img = document.createElement("img");
            
           
            if (item.image_url.startsWith('http')) {
                // If it's a full URL (CDN), use it exactly as is
                img.src = item.image_url;
            } else {
                // If it's a relative path, prepend your API base
                img.src = `${API_BASE_URL}${item.image_url}`;
            }
            // --------------------------------------------------------

            img.className = "gallery-item";
            img.onclick = () => window.open(img.src, "_blank");
            gallery.appendChild(img);
        });
    } catch (e) {
        console.error("Failed to load history", e);
        gallery.innerHTML = "<p>Error loading history items.</p>";
    }
}



// --- HISTORY SAVING LOGIC ---
const btnSaveHistory = document.getElementById("btnSaveHistory");

btnSaveHistory.addEventListener("click", async () => {
    // Get the URL of the currently displayed generated image
    const currentImageUrl = document.getElementById("finalOutputImage").src;
    
    // Convert the full absolute URL back to a relative path for safe DB storage
    const relativeUrl = currentImageUrl.replace(API_BASE_URL, "");

    btnSaveHistory.disabled = true;
    btnSaveHistory.innerText = "Saving...";

    try {
        const response = await fetch(`${API_BASE_URL}/api/history/save`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${authToken}`,
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ image_url: relativeUrl })
        });

        if (response.ok) {
            btnSaveHistory.innerText = "Saved! ✔️";
            btnSaveHistory.style.backgroundColor = "#48bb78"; // Turn green for success
        } else {
            throw new Error("Failed to save.");
        }
    } catch (err) {
        console.error("History Save Error:", err);
        alert("Could not save to history.");
        btnSaveHistory.disabled = false;
        btnSaveHistory.innerText = "Save to History";
    }
});

// ==========================================
// Camera & UI Logic (Original Logic Retained)
// ==========================================
async function startCamera() {
    stopCameraStream();
    logDebug("Requesting camera access...");
    try {
        currentStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: useFacingMode, width: { ideal: 640 }, height: { ideal: 800 } },
            audio: false
        });
        video.srcObject = currentStream;
        logDebug("Camera stream started successfully.");
    } catch (err) {
        logError("Camera access failed", err);
        alert("Could not access device camera. Please upload a photo instead.");
        switchTab("upload");
    }
}

function stopCameraStream() {
    if (currentStream) {
        currentStream.getTracks().forEach(track => track.stop());
        currentStream = null;
        logDebug("Camera stream stopped.");
    }
}

btnCapture.addEventListener("click", () => {
    if (!currentStream) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    
    canvas.toBlob((blob) => {
        capturedBlob = blob;
        const imageUrl = URL.createObjectURL(blob);
        logDebug("Photo captured from camera");
        showPersonPreview(imageUrl);
        stopCameraStream();
    }, "image/jpeg", 0.95);
});

btnFlipCamera.addEventListener("click", () => {
    useFacingMode = (useFacingMode === "user") ? "environment" : "user";
    startCamera();
});

function switchTab(target) {
    if (target === "camera") {
        btnTabCamera.classList.add("active");
        btnTabUpload.classList.remove("active");
        uploadView.classList.add("hidden");
        if (!capturedBlob) {
            cameraView.classList.remove("hidden");
            startCamera();
        }
    } else {
        btnTabUpload.classList.add("active");
        btnTabCamera.classList.remove("active");
        cameraView.classList.add("hidden");
        uploadView.classList.remove("hidden");
        stopCameraStream();
    }
}

btnTabCamera.addEventListener("click", () => switchTab("camera"));
btnTabUpload.addEventListener("click", () => switchTab("upload"));

// Inside your existing userFileInput.addEventListener
userFileInput.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (file) {
        const url = URL.createObjectURL(file);
        
        // --- NEW: Validation Step ---authToken
        const tempImg = new Image();
        tempImg.src = url;
        // Inside your app.js
tempImg.onload = async () => {
    console.log("App: Image loaded, starting validation...");
    const result = await validateImagePose(tempImg);
    console.log("App: Validation result received:", result);
    
    if (!result.valid) {
        alert("Alert Triggered: " + result.msg); // Force an alert to see if this branch executes
        userFileInput.value = ""; 
        return;
    }
    
    console.log("App: Validation passed, proceeding to preview.");
    capturedBlob = file;
    showPersonPreview(url);
};
    }
});
function showPersonPreview(url) {
    personPreview.src = url;
    cameraView.classList.add("hidden");
    uploadView.classList.add("hidden");
    personPreviewContainer.classList.remove("hidden");
    validateFormInputs();
}

btnResetPerson.addEventListener("click", () => {
    capturedBlob = null;
    personPreview.src = "";
    personPreviewContainer.classList.add("hidden");
    userFileInput.value = "";
    
    if (btnTabCamera.classList.contains("active")) {
        cameraView.classList.remove("hidden");
        startCamera();
    } else {
        uploadView.classList.remove("hidden");
    }
    validateFormInputs();
});

garmentFileInput.addEventListener("change", () => {
    selectedClosetItemId = null; // Reset closet selection if they upload a new file
    validateFormInputs();
});

// Mock functionality for selecting from closet (will hook to actual data in Phase 2)
document.getElementById("btnSelectFromCloset").addEventListener("click", () => {
    switchMainView('closet');
    alert("Closet selection is now available! Click on an item to select it for try-on.");
});

function selectClosetItem(item) {
    selectedClosetItemId = item.id;
    
    // Show the preview image in the studio
    garmentPreviewImg.src = `${API_BASE_URL}${item.image_url}`;
    selectedGarmentPreview.classList.remove("hidden");
    
    // Hide the file upload input group
    garmentInputGroup.classList.add("hidden");
    
    // Automatically take the user back to the Studio tab
    switchMainView('studio');
    validateFormInputs();
}

btnRemoveGarment.addEventListener("click", () => {
    // Reset state and restore UI
    selectedClosetItemId = null;
    selectedGarmentPreview.classList.add("hidden");
    garmentInputGroup.classList.remove("hidden");
    garmentFileInput.value = ""; 
    validateFormInputs();
});

// Update the closet button to simply switch views (remove the alert)
document.getElementById("btnSelectFromCloset").addEventListener("click", () => {
    closetSelectionMode = 'studio'; // Tell the closet it's for the studio
    switchMainView('closet');
});

function validateFormInputs() {
    const hasPerson = capturedBlob !== null;
    const hasGarment = garmentFileInput.files.length > 0 || selectedClosetItemId !== null;
    btnGenerate.disabled = !(hasPerson && hasGarment);
}

// ==========================================
// API Interaction Logic (Updated with Auth)
// ==========================================
btnGenerate.addEventListener("click", async () => {
    if (!capturedBlob || (garmentFileInput.files.length === 0 && !selectedClosetItemId)) return;

    const formData = new FormData();
    formData.append("category", garmentCategory.value);
    formData.append("garment_desc", garmentDescription.value); 

    const personFile = (capturedBlob instanceof File) ? capturedBlob : new File([capturedBlob], "capture.jpg", { type: "image/jpeg" });
    formData.append("person_image", personFile);
    
    if (garmentFileInput.files.length > 0) {
        formData.append("garment_image", garmentFileInput.files[0]);
    } else if (selectedClosetItemId) {
        formData.append("closet_item_id", selectedClosetItemId);
    }

    btnGenerate.disabled = true;
    placeholderText.classList.add("hidden");
    finalOutputImage.classList.add("hidden");
    btnSaveHistory.classList.add("hidden"); // Hide save button on new run
    loadingEngine.classList.remove("hidden");
    loadingStatusText.innerText = "Transmitting to secure server...";

    try {
        const response = await fetch(`${API_BASE_URL}/api/tryon`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${authToken}` // Authenticated request
            },
            body: formData
        });

        if (!response.ok) throw new Error(`Server Error: ${response.status}`);
        const initialJobData = await response.json();
        pollJobStatus(initialJobData.id);

    } catch (err) {
        logError("Submission Error", err.message);
        alert("Error executing job.");
        resetOutputUi();
    }
});

function pollJobStatus(jobId) {
    loadingStatusText.innerText = "AI processing... Waiting in queue...";
    let pollAttempts = 0;

    const intervalId = setInterval(async () => {
        pollAttempts++;
        try {
            const response = await fetch(`${API_BASE_URL}/api/tryon/${jobId}`, {
                headers: { "Authorization": `Bearer ${authToken}` }
            });
            
            if (!response.ok) return;

            const job = await response.json();
            
            if (job.status === "processing") {
                loadingStatusText.innerText = `Microprixs AI rendering (Attempt ${pollAttempts})...`;
            } else if (job.status === "completed") {
                clearInterval(intervalId);
                displayFinalImage(job.result_image_url);
            } else if (job.status === "failed") {
                clearInterval(intervalId);
                alert("The AI model was unable to map this design.");
                resetOutputUi();
            }
        } catch (err) {
            logError("Polling error", err.message);
        }
    }, 3000); 
}

function displayFinalImage(url) {
    loadingEngine.classList.add("hidden");
    finalOutputImage.src = url;
    finalOutputImage.classList.remove("hidden");
    btnSaveHistory.classList.remove("hidden"); // Show save button!
    validateFormInputs();
}

function resetOutputUi() {
    loadingEngine.classList.add("hidden");
    placeholderText.classList.remove("hidden");
    btnSaveHistory.classList.add("hidden");
    btnGenerate.disabled = false;
}

// --- CLOSET UPLOAD LOGIC ---
btnUploadToCloset.addEventListener("click", async () => {
    const file = closetFileInput.files[0];
    const label = closetItemLabel.value || "Untitled Garment";

    if (!file) {
        alert("Please select an image file first.");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("category", "garment"); 
    formData.append("label", label);

    try {
        btnUploadToCloset.disabled = true;
        btnUploadToCloset.innerText = "Uploading...";

        const response = await fetch(`${API_BASE_URL}/api/closet/upload`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${authToken}`
            },
            body: formData
        });

        if (response.ok) {
            alert("Successfully uploaded to your closet!");
            closetFileInput.value = "";
            closetItemLabel.value = "";
            // Optional: Refresh gallery here if you implement fetchClosetItems()
        } else {
            const errData = await response.json();
            throw new Error(errData.detail || "Upload failed");
        }
    } catch (err) {
        logError("Closet upload error", err);
        alert("Error: " + err.message);
    } finally {
        btnUploadToCloset.disabled = false;
        btnUploadToCloset.innerText = "Upload to Closet";
    }
});


// Add this helper function to your app.js
// Update the gallery item generation
async function loadClosetGallery() {
    const gallery = document.getElementById("closetGallery");
    gallery.innerHTML = "<p>Loading your closet...</p>";

    try {
        const response = await fetch(`${API_BASE_URL}/api/closet/`, {
            headers: { "Authorization": `Bearer ${authToken}` }
        });
        const items = await response.json();

        gallery.innerHTML = ""; 
        items.forEach(item => {
            const img = document.createElement("img");
            img.src = `${API_BASE_URL}${item.image_url}`;
            img.className = "gallery-item";
            
            // CONTEXT AWARE CLICK HANDLER
            img.onclick = () => {
                if (closetSelectionMode === 'studio') {
                    selectClosetItem(item); // Route to standard Studio
                } else if (closetSelectionMode === '360') {
                    selectClosetItem360(item); // NEW: Route to 360° Studio
                } else {
                    // Call the Phase 4 router defined in outfit.js
                    routeOutfitSelection(item, closetSelectionMode);
                }
            };
            gallery.appendChild(img);
        });
    } catch (e) {
        gallery.innerHTML = "<p>Error loading closet items.</p>";
    }
}

// Update your switchMainView(view) logic to call this function:
// ... inside 'else if (view === 'closet')' ...
    loadClosetGallery(); // <-- ADD THIS CALL


// ==========================================
// Phase 5: 360° Multi-Angle Logic
// ==========================================
let capturedAngles = { front: null, side: null, back: null };
let generatedResults = []; 
let currentCarouselIndex = 0;
let selectedClosetItemId360 = null;

const btnGenerate360 = document.getElementById("btnGenerate360");

// Update Validation State
function update360State() {
    const hasAtLeastOneAngle = capturedAngles.front || capturedAngles.side || capturedAngles.back;
    btnGenerate360.disabled = !(hasAtLeastOneAngle && selectedClosetItemId360);
}

// Bind the 3 upload slots
['front', 'side', 'back'].forEach(angle => {
    const inputEl = document.getElementById(`userFile${angle.charAt(0).toUpperCase() + angle.slice(1)}`);
    const previewEl = document.getElementById(`preview${angle.charAt(0).toUpperCase() + angle.slice(1)}`);
    
    if (inputEl) {
        inputEl.addEventListener("change", async (e) => {
            const file = e.target.files[0];
            if (file) {
                const url = URL.createObjectURL(file);
                const tempImg = new Image();
                tempImg.src = url;
                
                tempImg.onload = async () => {
                    inputEl.disabled = true;
                    // Uses the upgraded context-aware validator
                    // const result = await validateImagePose(tempImg, angle); 
                    
                    // if (!result.valid) {
                    //     alert(`Validation Failed for ${angle}: ` + result.msg);
                    //     inputEl.value = ""; 
                    //     inputEl.disabled = false;
                    //     return;
                    // }
                    
                    capturedAngles[angle] = file;
                    previewEl.src = url;
                    previewEl.classList.remove("hidden");
                    inputEl.classList.add("hidden");
                    document.getElementById("btnReset360").classList.remove("hidden");
                    inputEl.disabled = false;
                    update360State();
                };
            }
        });
    }
});

// Reset Angles
document.getElementById("btnReset360").addEventListener("click", () => {
    capturedAngles = { front: null, side: null, back: null };
    ['front', 'side', 'back'].forEach(angle => {
        const titleCase = angle.charAt(0).toUpperCase() + angle.slice(1);
        document.getElementById(`preview${titleCase}`).classList.add("hidden");
        document.getElementById(`preview${titleCase}`).src = "";
        document.getElementById(`userFile${titleCase}`).value = "";
        document.getElementById(`userFile${titleCase}`).classList.remove("hidden");
    });
    document.getElementById("btnReset360").classList.add("hidden");
    update360State();
});

// Closet Selection for 360
document.getElementById("btnSelectFromCloset360").addEventListener("click", () => {
    closetSelectionMode = '360';
    switchMainView('closet');
});

// You'll need to add a hook in your closetGallery onclick router:
// if (closetSelectionMode === '360') selectClosetItem360(item);

function selectClosetItem360(item) {
    selectedClosetItemId360 = item.id;
    document.getElementById("garmentPreviewImg360").src = `${API_BASE_URL}${item.image_url}`;
    document.getElementById("selectedGarmentPreview360").classList.remove("hidden");
    document.getElementById("btnSelectFromCloset360").classList.add("hidden");
    switchMainView('360');
    update360State();
}

document.getElementById("btnRemoveGarment360").addEventListener("click", () => {
    selectedClosetItemId360 = null;
    document.getElementById("selectedGarmentPreview360").classList.add("hidden");
    document.getElementById("btnSelectFromCloset360").classList.remove("hidden");
    update360State();
});

// The Promise.all() Generation logic
btnGenerate360.addEventListener("click", async () => {
    btnGenerate360.disabled = true;
    document.getElementById("placeholderText360").classList.add("hidden");
    document.getElementById("carouselContainer").classList.add("hidden");
    document.getElementById("carouselControls").classList.add("hidden");
    document.getElementById("loadingEngine360").classList.remove("hidden");
    document.getElementById("loadingStatusText360").innerText = "Initiating concurrent batch orchestration...";

    generatedResults = [];
    const activeAngles = Object.keys(capturedAngles).filter(key => capturedAngles[key] !== null);
    
    try {
        const generationPromises = activeAngles.map(async (angle) => {
            const formData = new FormData();
            formData.append("category", document.getElementById("garmentCategory360").value);
            formData.append("person_image", capturedAngles[angle]);
            formData.append("closet_item_id", selectedClosetItemId360);
            
            const response = await fetch(`${API_BASE_URL}/api/tryon`, {
                method: "POST",
                headers: { "Authorization": `Bearer ${authToken}` },
                body: formData
            });
            
            if (!response.ok) throw new Error(`Generation failed for ${angle}`);
            const data = await response.json();
            return { angle, jobId: data.id };
        });

        const jobIds = await Promise.all(generationPromises);
        document.getElementById("loadingStatusText360").innerText = "Rendering layers across all perspectives...";
        await poll360Jobs(jobIds);

    } catch (err) {
        logError("Batch Submission Error", err.message);
        alert("Error initiating the 360 batch.");
        reset360Ui();
    }
});

async function poll360Jobs(jobs) {
    const intervalId = setInterval(async () => {
        let allComplete = true;
        
        for (let job of jobs) {
            if (generatedResults.some(res => res.angle === job.angle)) continue;
            allComplete = false;
            
            try {
                const response = await fetch(`${API_BASE_URL}/api/tryon/${job.jobId}`, {
                    headers: { "Authorization": `Bearer ${authToken}` }
                });
                if (response.ok) {
                    const data = await response.json();
                    if (data.status === "completed") {
                        generatedResults.push({ angle: job.angle, url: data.result_image_url });
                    } else if (data.status === "failed") {
                        clearInterval(intervalId);
                        alert(`Model failed to map the ${job.angle} perspective.`);
                        reset360Ui();
                        return;
                    }
                }
            } catch(e) {}
        }

        if (allComplete) {
            clearInterval(intervalId);
            initCarousel();
        }
    }, 3500);
}

function initCarousel() {
    document.getElementById("loadingEngine360").classList.add("hidden");
    document.getElementById("carouselContainer").classList.remove("hidden");
    
    const order = { 'front': 1, 'side': 2, 'back': 3 };
    generatedResults.sort((a, b) => order[a.angle] - order[b.angle]);
    
    if (generatedResults.length > 1) {
        document.getElementById("carouselControls").classList.remove("hidden");
    }
    
    currentCarouselIndex = 0;
    updateCarouselDisplay();
}

function updateCarouselDisplay() {
    const current = generatedResults[currentCarouselIndex];
    const imgEl = document.getElementById("carouselImg");
    imgEl.src = current.url;
    document.getElementById("angleLabel").innerText = current.angle;
}

document.getElementById("btnNextAngle").addEventListener("click", () => {
    currentCarouselIndex = (currentCarouselIndex + 1) % generatedResults.length;
    updateCarouselDisplay();
});

document.getElementById("btnPrevAngle").addEventListener("click", () => {
    currentCarouselIndex = (currentCarouselIndex - 1 + generatedResults.length) % generatedResults.length;
    updateCarouselDisplay();
});

function reset360Ui() {
    document.getElementById("loadingEngine360").classList.add("hidden");
    document.getElementById("placeholderText360").classList.remove("hidden");
    update360State();
}

// Start application logic on load
window.addEventListener("load", initApp);



