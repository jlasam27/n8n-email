from flask import Flask, request, send_file, jsonify
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
import os
import tempfile
import textwrap

app = Flask(__name__)

@app.route('/translate-image', methods=['POST'])
def translate_image():
    data = request.get_json(force=True)  # force=True ensures JSON parse even if no mimetype
    image_url = data.get("imageUrl")
    ocr_results = data.get("ocrResults", [])

    if not image_url or not isinstance(ocr_results, list):
        return jsonify({"error": "Missing or invalid imageUrl/ocrResults"}), 400

    # Use a browser‐like User‐Agent to avoid 403s
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/90.0.4430.93 Safari/537.36"
        ),
    }

    # 1. Fetch the image
    try:
        resp = requests.get(image_url, headers=headers, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch image: {e}"}), 400

    # 2. Open and convert the image
    try:
        image = Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        return jsonify({"error": f"Cannot open image: {e}"}), 400

    draw = ImageDraw.Draw(image)

    # 3. Load a default TrueType font
    try:
        # DejaVuSans‐Bold.ttf comes bundled with many Linux distros
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=24)
    except IOError:
        font = ImageFont.load_default()  # fallback

    for item in ocr_results:
        box = item.get("box", [])
        translation = item.get("translation", "") or ""
        # We only proceed if box is a 4‐element list and translation is nonempty
        if not (isinstance(box, list) and len(box) == 4) or not translation.strip():
            continue

        x, y, w, h = box

        # 4. Prepare to wrap or shrink text
        # We will try wrapping the text into multiple lines to fit width w.
        # If wrapping to a minimal single‐character width still overflows the box height,
        # we gradually reduce font size.

        # Start with initial font size
        current_font_size = font.size if hasattr(font, "size") else 24
        current_font = font
        lines = [translation]

        while True:
            # 5. Attempt to wrap text at the current font size so that each line <= w
            wrapper = textwrap.TextWrapper(width=9999)  # large initial width
            # Compute average character width to estimate wrap width
            # Use getbbox on a representative char, e.g. "あ"
            try:
                char_bbox = current_font.getbbox("あ")
                avg_char_width = char_bbox[2] - char_bbox[0]
                # If avg_char_width is zero (edge case), fallback to 10px
                avg_char_width = avg_char_width if avg_char_width > 0 else 10
            except Exception:
                avg_char_width = 10

            # Compute how many chars can fit in the box width
            max_chars_per_line = max(1, w // avg_char_width)
            wrapper.width = max_chars_per_line

            lines = wrapper.wrap(translation)

            # 6. Measure total height with small line spacing (say 4px)
            total_height = 0
            line_heights = []
            for line in lines:
                # getbbox returns (x0, y0, x1, y1)
                bbox = current_font.getbbox(line)
                line_height = bbox[3] - bbox[1]
                line_heights.append(line_height)
                total_height += line_height
            # Add spacing: assume 4px between lines
            total_height += (len(lines) - 1) * 4

            # 7. If the wrapped text fits within height h, break; else shrink font
            if total_height <= h or current_font_size <= 10:
                break
            # Otherwise, reduce font size by 2 and retry
            current_font_size -= 2
            try:
                current_font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=current_font_size)
            except IOError:
                current_font = ImageFont.load_default()
                break  # can't shrink default builtin font

        # 8. Draw a filled rectangle to cover the original Japanese
        #    We want to cover (x, y) to (x + w, y + h).
        draw.rectangle([x, y, x + w, y + h], fill="white")

        # 9. Render each line, centering it vertically within the box
        current_y = y
        # Compute remaining vertical space to center text block
        leftover_space = h - total_height
        current_y += max(0, leftover_space // 2)

        for idx, line in enumerate(lines):
            # measure width to center horizontally
            bbox = current_font.getbbox(line)
            line_width = bbox[2] - bbox[0]
            line_height = bbox[3] - bbox[1]
            # Center line within box horizontally
            text_x = x + max(0, (w - line_width) // 2)
            text_y = current_y
            draw.text((text_x, text_y), line, fill="black", font=current_font)
            # Advance y
            current_y += line_height + 4

    # 10. Save to a temporary file and return
    temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    image.save(temp_file.name, format="JPEG", quality=90)
    temp_file.seek(0)
    return send_file(temp_file.name, mimetype='image/jpeg')


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
