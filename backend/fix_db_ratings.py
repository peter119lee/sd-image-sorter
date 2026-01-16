import sqlite3
import os

DATABASE_PATH = r"l:\Antigravitiy code\sd-image-sorter\backend\images.db"
RATINGS = ["general", "sensitive", "questionable", "explicit"]

def fix_ratings():
    if not os.path.exists(DATABASE_PATH):
        print(f"Database not found: {DATABASE_PATH}")
        return

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    print("Fetching images with multiple ratings...")
    cursor.execute("""
        SELECT image_id, COUNT(*) as count 
        FROM tags 
        WHERE tag IN ('general', 'sensitive', 'questionable', 'explicit')
        GROUP BY image_id
        HAVING count > 1
    """)
    buggy_images = cursor.fetchall()

    print(f"Found {len(buggy_images)} images with redundant ratings.")

    for image_id, count in buggy_images:
        # Get all ratings for this image, sorted by confidence
        cursor.execute("""
            SELECT id, tag, confidence 
            FROM tags 
            WHERE image_id = ? AND tag IN ('general', 'sensitive', 'questionable', 'explicit')
            ORDER BY confidence DESC
        """, (image_id,))
        ratings = cursor.fetchall()

        if not ratings:
            continue

        # Keep the top one, delete the rest
        top_id = ratings[0][0]
        ids_to_delete = [r[0] for r in ratings[1:]]
        
        placeholders = ",".join("?" * len(ids_to_delete))
        cursor.execute(f"DELETE FROM tags WHERE id IN ({placeholders})", ids_to_delete)
        # print(f"Fixed image {image_id}: kept {ratings[0][1]} ({ratings[0][2]}), removed {len(ids_to_delete)} others.")

    conn.commit()
    conn.close()
    print("Database ratings cleanup complete!")

if __name__ == "__main__":
    fix_ratings()
