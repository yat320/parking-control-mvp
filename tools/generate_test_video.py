"""Genera el video de prueba: python tools/generate_test_video.py [--out X.mp4] [--seconds N]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.demo_video import main  # noqa: E402

if __name__ == "__main__":
    main()
