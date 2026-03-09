"""
NEXUS Traffic - Emergency Vehicle Detector
Uses YOLOv8 nano to detect emergency vehicles from camera frames.
Can run standalone or be imported into main.py.

Usage:
  python emergency_detector.py --source 0          # webcam
  python emergency_detector.py --source video.mp4  # video file
  python emergency_detector.py --demo              # demo with test images
"""

import argparse
import time
import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("⚠️  opencv-python not installed. Run: pip install opencv-python")

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    print("⚠️  ultralytics not installed. Run: pip install ultralytics")


# ─────────────────────────────────────────────
# VEHICLE COUNTER
# ─────────────────────────────────────────────
class VehicleCounter:
    """
    Counts vehicles per lane using YOLOv8.
    Divides frame into 4 quadrants (N, S, E, W lanes).
    """
    VEHICLE_CLASSES = {
        2: "car", 3: "motorcycle", 5: "bus",
        7: "truck", 0: "person"  # person = pedestrian
    }
    EMERGENCY_CLASSES = {
        # YOLOv8 doesn't have ambulance by default.
        # We use a fine-tuned model or detect by color (red/blue flashing)
        "ambulance": True,
        "fire truck": True,
        "police car": True,
    }

    def __init__(self, model_path="yolov8n.pt", conf_threshold=0.4):
        if not YOLO_AVAILABLE:
            self.model = None
            return
        print(f"Loading YOLOv8 model: {model_path}")
        self.model = YOLO(model_path)  # auto-downloads on first run
        self.conf = conf_threshold

    def count_lanes(self, frame) -> dict:
        """
        Count vehicles in 4 lane directions.
        Returns: {N: count, S: count, E: count, W: count, total: count}
        """
        if self.model is None or not CV2_AVAILABLE:
            return self._mock_counts()

        h, w = frame.shape[:2]
        results = self.model(frame, conf=self.conf, verbose=False)[0]
        counts = {"N": 0, "S": 0, "E": 0, "W": 0}

        for box in results.boxes:
            cls_id = int(box.cls[0])
            if cls_id not in self.VEHICLE_CLASSES:
                continue
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

            # Assign to lane quadrant
            if cy < h * 0.4:           counts["N"] += 1
            elif cy > h * 0.6:         counts["S"] += 1
            elif cx < w * 0.4:         counts["W"] += 1
            else:                      counts["E"] += 1

        counts["total"] = sum(counts.values())
        counts["density"] = {k: min(v / 10.0, 1.0) for k, v in counts.items() if k != "total"}
        return counts

    def detect_emergency(self, frame) -> dict:
        """
        Detect emergency vehicles.
        Uses color detection (red/blue flashing lights) as primary signal.
        """
        if not CV2_AVAILABLE:
            return {"detected": False, "type": None, "confidence": 0.0}

        # Color-based detection for flashing lights
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Red range (emergency lights)
        red_mask1 = cv2.inRange(hsv, np.array([0, 120, 70]),   np.array([10, 255, 255]))
        red_mask2 = cv2.inRange(hsv, np.array([170, 120, 70]), np.array([180, 255, 255]))
        red_mask  = red_mask1 | red_mask2

        # Blue range (police lights)
        blue_mask = cv2.inRange(hsv, np.array([100, 120, 70]), np.array([130, 255, 255]))

        h, w = frame.shape[:2]
        total_pixels = h * w

        red_ratio  = cv2.countNonZero(red_mask)  / total_pixels
        blue_ratio = cv2.countNonZero(blue_mask) / total_pixels

        if red_ratio > 0.03:
            return {"detected": True,  "type": "Ambulance/Fire",
                    "confidence": min(red_ratio * 10, 1.0), "color": "red"}
        if blue_ratio > 0.02:
            return {"detected": True,  "type": "Police",
                    "confidence": min(blue_ratio * 10, 1.0), "color": "blue"}

        # YOLOv8 vehicle detection fallback
        if self.model:
            results = self.model(frame, conf=0.5, verbose=False)[0]
            for box in results.boxes:
                name = self.model.names[int(box.cls[0])].lower()
                if any(e in name for e in ["ambulance", "truck", "fire"]):
                    return {"detected": True, "type": name.title(),
                            "confidence": float(box.conf[0]), "color": "unknown"}

        return {"detected": False, "type": None, "confidence": 0.0}

    def annotate_frame(self, frame, counts: dict, emergency: dict) -> np.ndarray:
        """Draw bounding boxes and lane counts on frame."""
        if not CV2_AVAILABLE:
            return frame

        h, w = frame.shape[:2]
        # Lane dividers
        cv2.line(frame, (w // 2, 0),     (w // 2, h),     (80, 80, 80), 1)
        cv2.line(frame, (0,     h // 2), (w,     h // 2), (80, 80, 80), 1)

        # Lane count overlays
        positions = {
            "N": (w // 2 - 30, 40),
            "S": (w // 2 - 30, h - 20),
            "W": (20,          h // 2),
            "E": (w - 60,      h // 2),
        }
        for lane, pos in positions.items():
            cv2.putText(frame, f"{lane}: {counts.get(lane, 0)}",
                        pos, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        # Emergency overlay
        if emergency.get("detected"):
            cv2.rectangle(frame, (0, 0), (w, h), (0, 0, 255), 4)
            cv2.putText(frame, f"EMERGENCY: {emergency['type']}",
                        (10, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)

        return frame

    def _mock_counts(self) -> dict:
        """Return simulated counts when model unavailable."""
        counts = {
            "N": np.random.randint(0, 8),
            "S": np.random.randint(0, 8),
            "E": np.random.randint(0, 8),
            "W": np.random.randint(0, 8),
        }
        counts["total"] = sum(counts.values())
        counts["density"] = {k: min(v / 10.0, 1.0) for k, v in counts.items() if k != "total"}
        return counts


# ─────────────────────────────────────────────
# DEMO / MAIN
# ─────────────────────────────────────────────
def run_demo():
    """Run demo with simulated data (no camera needed)."""
    print("\n🎬 Running NEXUS Emergency Detector Demo (simulated)\n")
    detector = VehicleCounter()

    for i in range(10):
        counts = detector._mock_counts()
        # Simulate emergency every 5th frame
        emergency = {
            "detected": (i % 5 == 0),
            "type": "Ambulance 🚑" if i % 5 == 0 else None,
            "confidence": 0.92 if i % 5 == 0 else 0.0
        }
        print(f"Frame {i+1:2d} | Counts: N={counts['N']} S={counts['S']} "
              f"E={counts['E']} W={counts['W']} | "
              f"Emergency: {'🚨 ' + emergency['type'] if emergency['detected'] else '✅ None'}")
        time.sleep(0.3)

    print("\n✅ Demo complete.")


def run_live(source):
    """Run on live video source."""
    if not CV2_AVAILABLE or not YOLO_AVAILABLE:
        print("Install requirements: pip install opencv-python ultralytics")
        return

    detector = VehicleCounter()
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"Could not open source: {source}")
        return

    print(f"🎥 Processing: {source}")
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret: break

        counts    = detector.count_lanes(frame)
        emergency = detector.detect_emergency(frame)
        frame     = detector.annotate_frame(frame, counts, emergency)

        # Send to backend API
        try:
            import requests
            requests.post("http://127.0.0.1:8000/sensor_update", json={
                "intersection_id": "I00",
                "lane_densities": [
                    counts.get("N", 0) / 10.0,
                    counts.get("S", 0) / 10.0,
                    counts.get("E", 0) / 10.0,
                    counts.get("W", 0) / 10.0
                ],
                "lane_queues": [
                    counts.get("N", 0) / 10.0,
                    counts.get("S", 0) / 10.0,
                    counts.get("E", 0) / 10.0,
                    counts.get("W", 0) / 10.0
                ],
                "emergency_detected": emergency.get("detected", False)
            }, timeout=1)
        except:
            pass

        cv2.imshow("NEXUS Traffic Monitor", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str, default=None, help="Video source (0=webcam, path=video file)")
    parser.add_argument("--demo",   action="store_true",    help="Run simulation demo")
    args = parser.parse_args()

    if args.demo or args.source is None:
        run_demo()
    else:
        src = int(args.source) if args.source.isdigit() else args.source
        run_live(src)
