import sys
import os
import unittest

# Add backend to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from censor import CensorDetector

class TestCensorLoading(unittest.TestCase):
    def setUp(self):
        # We need a valid model file for testing ort.InferenceSession
        # Use an empty file or similar if we just want to test logic, 
        # but InferenceSession requires a valid ONNX.
        # Since I can't easily get a valid ONNX here without downloading,
        # I'll mock the ort.InferenceSession if I have to.
        # For now, let's just check the logic in main.py style.
        pass

    def test_singleton_logic(self):
        global _test_detector
        _test_detector = None
        
        model_path1 = "dummy_path_1.onnx"
        model_path2 = "dummy_path_2.onnx"
        
        def mock_load(self):
            self.session = "mock_session"
            self.input_name = "mock_input"
            
        # Patch load
        original_load = CensorDetector.load
        CensorDetector.load = mock_load
        
        try:
            # First call
            if _test_detector is None or _test_detector.model_path != model_path1 or _test_detector.session is None:
                _test_detector = CensorDetector(model_path1)
                _test_detector.load()
            
            self.assertIsNotNone(_test_detector)
            self.assertEqual(_test_detector.model_path, model_path1)
            self.assertEqual(_test_detector.session, "mock_session")
            
            # Second call - Should reuse
            detector_ref = _test_detector
            if _test_detector is None or _test_detector.model_path != model_path1 or _test_detector.session is None:
                _test_detector = CensorDetector(model_path1)
                _test_detector.load()
            
            self.assertIs(detector_ref, _test_detector)
            
            # Third call - Session missing
            _test_detector.session = None
            if _test_detector is None or _test_detector.model_path != model_path1 or _test_detector.session is None:
                _test_detector = CensorDetector(model_path1)
                _test_detector.load()
            
            self.assertEqual(_test_detector.session, "mock_session")
            
            # Fourth call - Path changed
            if _test_detector is None or _test_detector.model_path != model_path2 or _test_detector.session is None:
                _test_detector = CensorDetector(model_path2)
                _test_detector.load()
                
            self.assertEqual(_test_detector.model_path, model_path2)
            
        finally:
            CensorDetector.load = original_load

if __name__ == "__main__":
    unittest.main()
