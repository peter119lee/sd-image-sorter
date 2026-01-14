"""
Comprehensive test script for SD Image Sorter.
Tests all core functionality including scanning, parsing, tagging, and API endpoints.
"""
import os
import sys
import json
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, os.path.dirname(__file__))

# Test configuration
TEST_IMAGE_DIR = r"L:\Antigravitiy code\sd-image-sorter\testimage"
TEST_OUTPUT_DIR = r"L:\Antigravitiy code\sd-image-sorter\test_output"

def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def test_imports():
    """Test that all required modules can be imported."""
    print_section("Testing Imports")
    
    try:
        import database
        print("✓ database module imported")
    except Exception as e:
        print(f"✗ Failed to import database: {e}")
        return False
    
    try:
        import metadata_parser
        print("✓ metadata_parser module imported")
    except Exception as e:
        print(f"✗ Failed to import metadata_parser: {e}")
        return False
    
    try:
        import image_manager
        print("✓ image_manager module imported")
    except Exception as e:
        print(f"✗ Failed to import image_manager: {e}")
        return False
    
    try:
        import tagger
        print("✓ tagger module imported")
    except Exception as e:
        print(f"✗ Failed to import tagger: {e}")
        return False
    
    return True

def test_metadata_parsing():
    """Test metadata parsing for all test images."""
    print_section("Testing Metadata Parsing")
    
    from metadata_parser import parse_image
    
    if not os.path.exists(TEST_IMAGE_DIR):
        print(f"✗ Test image directory not found: {TEST_IMAGE_DIR}")
        return False
    
    test_images = list(Path(TEST_IMAGE_DIR).glob("*"))
    if not test_images:
        print(f"✗ No test images found in {TEST_IMAGE_DIR}")
        return False
    
    print(f"Found {len(test_images)} test images\n")
    
    success_count = 0
    for img_path in test_images:
        if img_path.is_file():
            try:
                result = parse_image(str(img_path))
                print(f"✓ {img_path.name}")
                print(f"  Generator: {result['generator']}")
                print(f"  Size: {result['width']}x{result['height']}")
                print(f"  Prompt: {result['prompt'][:50] if result['prompt'] else 'None'}...")
                success_count += 1
            except Exception as e:
                print(f"✗ {img_path.name}: {e}")
    
    print(f"\nParsed {success_count}/{len(test_images)} images successfully")
    return success_count > 0

def test_database():
    """Test database operations."""
    print_section("Testing Database")
    
    import database as db
    
    try:
        # Initialize fresh database for testing
        db.init_db()
        print("✓ Database initialized")
        
        # Test adding a dummy image
        test_id = db.add_image(
            path="test.png",
            filename="test.png",
            generator="comfyui",
            prompt="test prompt",
            negative_prompt="test negative",
            metadata_json="{}",
            width=512,
            height=512,
            file_size=1024
        )
        print(f"✓ Added test image (ID: {test_id})")
        
        # Test retrieving image
        image = db.get_image_by_id(test_id)
        if image:
            print(f"✓ Retrieved image: {image['filename']}")
        else:
            print("✗ Failed to retrieve image")
            return False
        
        # Test adding tags
        tags = [
            {"tag": "test_tag1", "confidence": 0.9},
            {"tag": "test_tag2", "confidence": 0.8}
        ]
        db.add_tags(test_id, tags)
        print(f"✓ Added {len(tags)} tags")
        
        # Test retrieving tags
        retrieved_tags = db.get_image_tags(test_id)
        print(f"✓ Retrieved {len(retrieved_tags)} tags")
        
        return True
        
    except Exception as e:
        print(f"✗ Database test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_scanning():
    """Test image scanning functionality."""
    print_section("Testing Image Scanning")
    
    from image_manager import scan_folder
    import database as db
    
    if not os.path.exists(TEST_IMAGE_DIR):
        print(f"✗ Test image directory not found: {TEST_IMAGE_DIR}")
        return False
    
    try:
        # Clear database for fresh scan
        db.init_db()
        
        print(f"Scanning: {TEST_IMAGE_DIR}")
        
        def progress_cb(current, total, filename):
            print(f"  [{current}/{total}] {filename}")
        
        result = scan_folder(TEST_IMAGE_DIR, recursive=False, progress_callback=progress_cb)
        
        print(f"\nScan Results:")
        print(f"  Total found: {result['total']}")
        print(f"  New images: {result['new']}")
        print(f"  Errors: {result['errors']}")
        print(f"  By generator: {result['by_generator']}")
        
        if result['total'] > 0:
            print(f"✓ Successfully scanned {result['total']} images")
            return True
        else:
            print("✗ No images scanned")
            return False
            
    except Exception as e:
        print(f"✗ Scanning failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_tagging():
    """Test WD14 tagging functionality."""
    print_section("Testing WD14 Tagging")
    
    try:
        from tagger import WD14Tagger
        
        print("Initializing tagger...")
        tagger = WD14Tagger()
        
        print("Loading model (this may take a while)...")
        tagger.load()
        print("✓ Model loaded successfully")
        
        # Find a test image
        test_images = list(Path(TEST_IMAGE_DIR).glob("*.png"))
        if not test_images:
            test_images = list(Path(TEST_IMAGE_DIR).glob("*.jpg"))
        if not test_images:
            test_images = list(Path(TEST_IMAGE_DIR).glob("*.webp"))
        
        if not test_images:
            print("✗ No test images found for tagging")
            return False
        
        test_image = str(test_images[0])
        print(f"\nTagging test image: {os.path.basename(test_image)}")
        
        result = tagger.tag(test_image)
        
        print(f"\nTagging Results:")
        print(f"  Rating: {result['rating']}")
        print(f"  General tags: {len(result['general_tags'])}")
        print(f"  Character tags: {len(result['character_tags'])}")
        
        if result['general_tags']:
            print(f"\n  Top 5 general tags:")
            for tag_data in result['general_tags'][:5]:
                print(f"    - {tag_data['tag']}: {tag_data['confidence']:.2f}")
        
        print(f"\n✓ Successfully tagged image")
        return True
        
    except Exception as e:
        print(f"✗ Tagging failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_api():
    """Test API endpoints (requires server to be running)."""
    print_section("Testing API Endpoints")
    
    try:
        import requests
        
        base_url = "http://localhost:8000"
        
        # Test stats endpoint
        response = requests.get(f"{base_url}/api/stats", timeout=5)
        if response.status_code == 200:
            stats = response.json()
            print(f"✓ Stats endpoint working")
            print(f"  Total images: {stats.get('total_images', 0)}")
        else:
            print(f"✗ Stats endpoint failed: {response.status_code}")
            return False
        
        # Test images endpoint
        response = requests.get(f"{base_url}/api/images", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✓ Images endpoint working")
            print(f"  Images returned: {data.get('count', 0)}")
        else:
            print(f"✗ Images endpoint failed: {response.status_code}")
            return False
        
        # Test generators endpoint
        response = requests.get(f"{base_url}/api/generators", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✓ Generators endpoint working")
            print(f"  Generators: {data.get('generators', [])}")
        else:
            print(f"✗ Generators endpoint failed: {response.status_code}")
            return False
        
        return True
        
    except requests.exceptions.ConnectionError:
        print("✗ Could not connect to API server")
        print("  Make sure the server is running: python backend/main.py")
        return False
    except ImportError:
        print("✗ requests library not installed")
        print("  Install with: pip install requests")
        return False
    except Exception as e:
        print(f"✗ API test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def run_all_tests():
    """Run all tests and generate a report."""
    print("\n" + "="*60)
    print("  SD Image Sorter - Comprehensive Test Suite")
    print("="*60)
    
    results = {}
    
    # Run tests
    results['Imports'] = test_imports()
    results['Metadata Parsing'] = test_metadata_parsing()
    results['Database'] = test_database()
    results['Image Scanning'] = test_scanning()
    results['WD14 Tagging'] = test_tagging()
    results['API Endpoints'] = test_api()
    
    # Print summary
    print_section("Test Summary")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, success in results.items():
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status:10} - {test_name}")
    
    print(f"\n{'='*60}")
    print(f"  Results: {passed}/{total} tests passed")
    print(f"{'='*60}\n")
    
    return passed == total

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
