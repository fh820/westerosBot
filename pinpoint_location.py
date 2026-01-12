import sys
import os
from PIL import Image, ImageDraw, ImageFont

# --- CONFIGURATION ---
# Use the visual map (jpg) to see terrain features, or the cost map (png) to check collision data
MAP_PATH = "data/maps/map.jpg"
OUTPUT_PATH = "pinpoint_result.png"


def pinpoint(x: int, y: int):
    """
    Draws a crosshair and label at the specified coordinates on the map.
    """
    if not os.path.exists(MAP_PATH):
        print(f"❌ Error: Map file not found at '{MAP_PATH}'")
        return

    try:
        print(f"Loading map from {MAP_PATH}...")
        with Image.open(MAP_PATH) as img:
            # Ensure image is in RGB mode for drawing colors
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)

            width, height = img.size
            print(f"Map Size: {width}x{height}")

            # Validate bounds
            if not (0 <= x < width and 0 <= y < height):
                print(f"❌ Error: Coordinates ({x}, {y}) are out of bounds.")
                return

            # --- DRAW CROSSHAIR ---
            # Style: Bright Red, Thick lines
            color = (255, 0, 0)  # Red
            line_width = 5
            size = 30  # Size of the crosshair arms

            # Horizontal line
            draw.line((x - size, y, x + size, y), fill=color, width=line_width)
            # Vertical line
            draw.line((x, y - size, x, y + size), fill=color, width=line_width)
            # Circle in the center
            r = 5
            draw.ellipse(
                (x - r, y - r, x + r, y + r), fill=None, outline=color, width=line_width
            )

            # --- DRAW TEXT LABEL ---
            # Try to load a generic font, fallback to default
            try:
                # Basic font, usually available
                font = ImageFont.truetype("arial.ttf", 40)
            except IOError:
                font = ImageFont.load_default()

            label = f"({x}, {y})"

            # Draw text with a black outline for visibility
            text_x = x + 20
            text_y = y - 40

            # Thick black border
            draw.text((text_x - 2, text_y), label, font=font, fill="black")
            draw.text((text_x + 2, text_y), label, font=font, fill="black")
            draw.text((text_x, text_y - 2), label, font=font, fill="black")
            draw.text((text_x, text_y + 2), label, font=font, fill="black")

            # Actual white text
            draw.text((text_x, text_y), label, font=font, fill="white")

            print(f"✅ Marked ({x}, {y}) on the map.")

            # Save the result
            img.save(OUTPUT_PATH)
            print(f"🖼️ Saved result to: {os.path.abspath(OUTPUT_PATH)}")
            print("Open this file to view the location.")

    except Exception as e:
        print(f"❌ An error occurred: {e}")


if __name__ == "__main__":
    # How to run: python pinpoint_location.py 671 1569
    if len(sys.argv) < 3:
        print("Usage: python pinpoint_location.py [X] [Y]")
        print("Example: python pinpoint_location.py 671 1569")
    else:
        try:
            in_x = int(sys.argv[1])
            in_y = int(sys.argv[2])
            pinpoint(in_x, in_y)
        except ValueError:
            print("❌ Error: Coordinates must be integers.")
