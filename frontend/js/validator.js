// js/validator.js

async function validateImagePose(imageElement) {
    console.log("Validator: Starting pose detection...");
    
    try {
        const detectorConfig = { modelType: poseDetection.movenet.modelType.SINGLEPOSE_LIGHTNING };
        const detector = await poseDetection.createDetector(poseDetection.SupportedModels.MoveNet, detectorConfig);
        
        console.log("Validator: Detector loaded, estimating...");
        const poses = await detector.estimatePoses(imageElement);
        
        console.log("Validator: Poses detected:", poses);
        
        if (poses.length === 0) return { valid: false, msg: "No person detected." };
        
        const keypoints = poses[0].keypoints;
        const leftAnkle = keypoints.find(k => k.name === 'left_ankle');
        const rightAnkle = keypoints.find(k => k.name === 'right_ankle');

        console.log("Validator: Ankle scores - Left:", leftAnkle?.score, "Right:", rightAnkle?.score);

        if (!leftAnkle || !rightAnkle || leftAnkle.score < 0.3 || rightAnkle.score < 0.3) {
            return { valid: false, msg: "Please take a full-body photo (we need to see your feet!)" };
        }
        
        return { valid: true, msg: "Perfect pose." };
    } catch (err) {
        console.error("Validator Error:", err);
        return { valid: false, msg: "Detection failed: " + err.message };
    }
}