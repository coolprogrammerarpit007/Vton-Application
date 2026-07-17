import cv2
import mediapipe as mp
# Explicitly import the pose submodule
# from mediapipe.python.solutions import pose as mp_pose
import numpy as np
import logging


logger = logging.getLogger(__name__)

# Initialize MediaPipe Pose engine safely

try:
    mp_pose = mp.solutions.pose
    pose = mp_pose.Pose(
        static_image_mode=True,
        model_complexity = 1,
        enable_segmentation=False,
        min_detection_confidence=0.5
    )
    
except Exception as e:
    logger.critical(f"Failed to initialize MediaPipe Pose: {str(e)}")
    raise


def get_person_bounding_box(image_rgb):
    """
    Leverages MediaPipe Pose landmarks to calculate a highly precise bounding 
    box containing the complete visible human body (head-to-toe).
    """
    
    try:
        results = pose.process(image_rgb)
        
        if not results.pose_landmarks:
            logger.warning("MediaPipe failed to detect pose landmarks in the provided image.")
            return None
        
        h,w,_ = image_rgb.shape
        x_coords = []
        y_coords = []
        
        # Map all 33 structural body landmarks
        for landmark in results.pose_landmarks.landmark:
            # MediaPipe returns normalized coordinates (0.0 to 1.0)
            x_coords.append(landmark.x * w)
            y_coords.append(landmark.y * h)

        x_min, x_max = int(min(x_coords)), int(max(x_coords))
        y_min, y_max = int(min(y_coords)), int(max(y_coords))

        # Clamp bounding boundaries to absolute image canvas dimension limits
        return {
            "x": max(0, x_min),
            "y": max(0, y_min),
            "w": min(w - x_min, x_max - x_min),
            "h": min(h - y_min, y_max - y_min)
        }
    except Exception as e:
        logger.error(f"Error during landmark detection: {str(e)}", exc_info=True)
        return None
    
    
    
def calculate_ideal_crop(image_shape, human_box, target_ratio_str="9:16", margin_padding=0.15):
    """
    Executes geometric aspect ratio matching centered over the human target
    without changing image proportions or risking out-of-bounds array slices.
    """
    img_h, img_w = image_shape[:2]
    
    # Parse target aspect ratio fraction
    try:
        ratio_parts = target_ratio_str.split(":")
        target_ratio = float(ratio_parts[0]) / float(ratio_parts[1])
    except (ValueError, IndexError):
        logger.error(f"Invalid target ratio string format: '{target_ratio_str}'. Defaulting to 9:16.")
        target_ratio = 9.0 / 16.0

    # 1. Expand human bounding box slightly for natural breathing space
    safe_w = human_box['w'] * (1 + margin_padding)
    safe_h = human_box['h'] * (1 + margin_padding)
    
    # 2. Determine ideal box dimensions matching the requested aspect ratio
    if (safe_w / safe_h) < target_ratio:
        crop_h = safe_h
        crop_w = crop_h * target_ratio
    else:
        crop_w = safe_w
        crop_h = crop_w / target_ratio
        
    # 3. Handle downscaling constraints if the crop box exceeds raw image bounds
    if crop_w > img_w or crop_h > img_h:
        img_ratio = img_w / img_h
        if img_ratio < target_ratio:
            crop_w = img_w
            crop_h = crop_w / target_ratio
        else:
            crop_h = img_h
            crop_w = crop_h * target_ratio

    # 4. Center coordinates calculated directly relative to human coordinates center
    center_x = human_box['x'] + (human_box['w'] / 2)
    center_y = human_box['y'] + (human_box['h'] / 2)
    
    crop_x = center_x - (crop_w / 2)
    crop_y = center_y - (crop_h / 2)
    
    # 5. Clamp coordinates to ensure the crop bounding box does not slide out of frame
    crop_x = max(0, min(crop_x, img_w - crop_w))
    crop_y = max(0, min(crop_y, img_h - crop_h))

    return {
        "x": int(crop_x),
        "y": int(crop_y),
        "w": int(crop_w),
        "h": int(crop_h)
    }

def process_smart_crop(input_path, output_path, target_ratio_str="9:16"):
    """
    High-level orchestration method that reads target source imagery, executes 
    computer vision analysis, performs array slicing, and updates physical storage.
    """
    img = cv2.imread(input_path)
    if img is None:
        raise ValueError(f"Failed to read file from disk path: {input_path}")
        
    # MediaPipe requires RGB channel layout
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    
    human_box = get_person_bounding_box(img_rgb)
    
    if human_box:
        logger.info("Human subject detected successfully. Appending smart geometric cropping.")
        crop_dims = calculate_ideal_crop(img.shape, human_box, target_ratio_str)
    else:
        logger.warning("No human profile resolved. Falling back to standard canvas center-crop matrix.")
        h, w = img.shape[:2]
        dummy_box = {"x": int(w * 0.25), "y": int(h * 0.25), "w": int(w * 0.5), "h": int(h * 0.5)}
        crop_dims = calculate_ideal_crop(img.shape, dummy_box, target_ratio_str, margin_padding=0)

    # Cut the frame array and export
    x, y, w, h = crop_dims["x"], crop_dims["y"], crop_dims["w"], crop_dims["h"]
    cropped_img = img[y:y+h, x:x+w]
    
    success = cv2.imwrite(output_path, cropped_img)
    if not success:
        raise IOError(f"Failed to save cropped image output asset down to: {output_path}")
        
    return True

