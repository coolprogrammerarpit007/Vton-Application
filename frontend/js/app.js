const API_BASE_URL = "https://vton.falcondetectives.com"; 
const DUMMY_USER_ID = 1; 

let currentStream = null;
let useFacingMode = "user"; 
let capturedBlob = null; 

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
const garmentDescription = document.getElementById("garmentDescription"); // Added text input reference
const userFileInput = document.getElementById("userFileInput");
const garmentFileInput = document.getElementById("garmentFileInput");

async function startCamera() {
    stopCameraStream();
    try {
        currentStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: useFacingMode, width: { ideal: 640 }, height: { ideal: 800 } },
            audio: false
        });
        video.srcObject = currentStream;
    } catch (err) {
        console.error("Camera access failed: ", err);
        alert("Could not access device camera. Please upload a photo instead.");
        switchTab("upload");
    }
}

function stopCameraStream() {
    if (currentStream) {
        currentStream.getTracks().forEach(track => track.stop());
        currentStream = null;
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
        const imageUrl = URL.createObjectURL(file);
        showPersonPreview(imageUrl);
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

garmentFileInput.addEventListener("change", () => validateFormInputs());

function validateFormInputs() {
    const hasPerson = capturedBlob !== null;
    const hasGarment = garmentFileInput.files.length > 0;
    btnGenerate.disabled = !(hasPerson && hasGarment);
}

btnGenerate.addEventListener("click", async () => {
    if (!capturedBlob || garmentFileInput.files.length === 0) return;

    const formData = new FormData();
    formData.append("user_id", DUMMY_USER_ID);
    formData.append("category", garmentCategory.value);
    formData.append("garment_desc", garmentDescription.value); // Added text prompt appending

    const personFile = (capturedBlob instanceof File) ? capturedBlob : new File([capturedBlob], "capture.jpg", { type: "image/jpeg" });
    formData.append("person_image", personFile);
    formData.append("garment_image", garmentFileInput.files[0]);

    btnGenerate.disabled = true;
    placeholderText.classList.add("hidden");
    finalOutputImage.classList.add("hidden");
    loadingEngine.classList.remove("hidden");
    loadingStatusText.innerText = "Transmitting image layers to server instance...";

    try {
        const response = await fetch(`${API_BASE_URL}/api/tryon`, {
            method: "POST",
            body: formData
        });

        if (!response.ok) throw new Error("Server rejected upload submission payload");

        const initialJobData = await response.json();
        pollJobStatus(initialJobData.id);
    } catch (err) {
        alert("Error executing job: " + err.message);
        resetOutputUi();
    }
});

function pollJobStatus(jobId) {
    loadingStatusText.innerText = "AI processing job initialized. Waiting in queue...";
    
    const intervalId = setInterval(async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/api/tryon/${jobId}`);
            if (!response.ok) return;

            const job = await response.json();
            
            if (job.status === "processing") {
                loadingStatusText.innerText = "FASHN.ai is currently rendering your clothing swap (10-30s)...";
            } else if (job.status === "completed") {
                clearInterval(intervalId);
                displayFinalImage(job.result_image_url);
            } else if (job.status === "failed") {
                clearInterval(intervalId);
                alert("The AI model was unable to map this design configuration properly.");
                resetOutputUi();
            }
        } catch (err) {
            console.error("Error checking execution state: ", err);
        }
    }, 3000); 
}

function displayFinalImage(url) {
    loadingEngine.classList.add("hidden");
    finalOutputImage.src = url;
    finalOutputImage.classList.remove("hidden");
    validateFormInputs();
}

function resetOutputUi() {
    loadingEngine.classList.add("hidden");
    placeholderText.classList.remove("hidden");
    validateFormInputs();
}

window.addEventListener("load", () => {
    startCamera();
});