class YOLODetector:
    def __init__(self, model_path="yolo26n.pt", confidence=0.30):
        from ultralytics import YOLO

        self.model = YOLO(model_path)
        self.confidence = confidence

    def detect(self, source):
        return self.model.predict(source=source, conf=self.confidence, verbose=False)