
import os
import sys

# Add l:\Antigravitiy code\sd-image-sorter\backend to sys.path
sys.path.append(r'l:\Antigravitiy code\sd-image-sorter\backend')

import database

def test_sorting():
    print("Testing Tags Sorting...")
    tags_count = database.get_all_tags(sort_by='count')
    tags_alpha = database.get_all_tags(sort_by='alpha')
    
    if tags_count:
        print(f"Top tag (count): {tags_count[0]['tag']} ({tags_count[0]['count']})")
    if tags_alpha:
        print(f"First tag (alpha): {tags_alpha[0]['tag']}")
        
    print("\nTesting Checkpoints Sorting...")
    cps_count = database.get_all_checkpoints(sort_by='count')
    cps_alpha = database.get_all_checkpoints(sort_by='alpha')
    
    if cps_count:
        print(f"Top CP (count): {cps_count[0]['checkpoint']} ({cps_count[0]['count']})")
    if cps_alpha:
        print(f"First CP (alpha): {cps_alpha[0]['checkpoint']}")
        
    print("\nTesting Loras Sorting...")
    loras_count = database.get_all_loras(sort_by='count')
    loras_alpha = database.get_all_loras(sort_by='alpha')
    
    if loras_count:
        print(f"Top Lora (count): {loras_count[0]['lora']} ({loras_count[0]['count']})")
    if loras_alpha:
        print(f"First Lora (alpha): {loras_alpha[0]['lora']}")

if __name__ == "__main__":
    try:
        test_sorting()
        print("\nVerification successful!")
    except Exception as e:
        print(f"\nVerification failed: {e}")
