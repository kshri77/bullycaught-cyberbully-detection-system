from models.cyberbully_detector import CyberbullyDetector

if __name__ == "__main__":
    detector = CyberbullyDetector()
    detector.train("data/training_data.csv", save_path="saved_models/cyberbully_model.pkl")
    print("🎉 Model trained and saved successfully!")
