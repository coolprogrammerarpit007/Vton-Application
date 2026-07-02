const API_BASE_URL = "http://127.0.0.1:8000"; 
const DEBUG_MODE = true; 

// --- NEW STATE VARIABLES ---
let authToken = localStorage.getItem("vton_token");
let selectedClosetItemId = null;
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
const btnSaveHistory = document.getElementById("btnSaveHistory");

// Navigation Buttons
const navStudio = document.getElementById("navStudio");
const navCloset = document.getElementById("navCloset");
const navHistory = document.getElementById("navHistory");
const btnLogout = document.getElementById("btnLogout");

const btnUploadToCloset = document.getElementById("btnUploadToCloset");
const closetFileInput = document.getElementById("closetFileInput");
const closetItemLabel = document.getElementById("closetItemLabel");



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

function switchMainView(view) {
    studioView.classList.add("hidden");
    closetViewPanel.classList.add("hidden");
    historyViewPanel.classList.add("hidden");

    navStudio.classList.remove("active");
    navCloset.classList.remove("active");
    navHistory.classList.remove("active");

    if (view === 'studio') {
        studioView.classList.remove("hidden");
        navStudio.classList.add("active");
        if (!capturedBlob && btnTabCamera.classList.contains("active")) {
            startCamera();
        }
    } else if (view === 'closet') {
        stopCameraStream();
        closetViewPanel.classList.remove("hidden");
        navCloset.classList.add("active");
        // Future Phase 2 logic: fetchClosetItems();
    } else if (view === 'history') {
        stopCameraStream();
        historyViewPanel.classList.remove("hidden");
        navHistory.classList.add("active");
        // Future Phase 5 logic: fetchHistoryItems();
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

userFileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) {
        capturedBlob = file; 
        showPersonPreview(URL.createObjectURL(file));
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
    alert("In Phase 2, selecting an image here will auto-fill the Try-On garment data!");
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
                loadingStatusText.innerText = `FASHN.ai rendering (Attempt ${pollAttempts})...`;
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
async function loadClosetGallery() {
    const gallery = document.getElementById("closetGallery");
    gallery.innerHTML = "<p>Loading your closet...</p>";

    try {
        const response = await fetch(`${API_BASE_URL}/api/closet/`, {
            headers: { "Authorization": `Bearer ${authToken}` }
        });
        const items = await response.json();
        console.log("DEBUG: Closet items received:", items);

        gallery.innerHTML = ""; // Clear loader
        items.forEach(item => {
            const img = document.createElement("img");
            img.src = `${API_BASE_URL}${item.image_url}`;
            img.className = "gallery-item";
            img.onclick = () => {
                // Logic to select this item for try-on
                selectedClosetItemId = item.id;
                alert(`Selected: ${item.label}`);
                switchMainView('studio');
            };
            gallery.appendChild(img);
        });
    } catch (e) {
        logError("Failed to load closet", e);
        gallery.innerHTML = "<p>Error loading closet items.</p>";
    }
}

// Update your switchMainView(view) logic to call this function:
// ... inside 'else if (view === 'closet')' ...
    loadClosetGallery(); // <-- ADD THIS CALL

// Start application logic on load
window.addEventListener("load", initApp);



