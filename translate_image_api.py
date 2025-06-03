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
    data = request.get_json()
    image_url = data.get("imageUrl")
    ocr_results = data.get("ocrResults", [])

    if not image_url or not isinstance(ocr_results, list):
        return jsonify({"error": "Missing or invalid imageUrl/ocrResults"}), 400

    # Use a browser-like User-Agent to avoid 403 errors
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/90.0.4430.93 Safari/537.36"
        ),
    }

    # 1) Fetch the image
    try:
        resp = requests.get(image_url, headers=headers, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch image: {e}"}), 400

    try:
        image = Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        return jsonify({"error": f"Cannot open image: {e}"}), 400

    draw = ImageDraw.Draw(image)

    # 2) Load a TrueType font (fallback to default)
    try:
        base_font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=24)
    except Exception:
        base_font = ImageFont.load_default()

    # 3) Process each OCR block
    for item in ocr_results:
        box = item.get("box", [])
        translation = item.get("translation", "").strip()
        if not translation or not (isinstance(box, list) and len(box) == 4):
            continue

        x, y, w, h = box

        # 3a) Cover the original Japanese text exactly
        draw.rectangle([x, y, x + w, y + h], fill="white")

        # 3b) Prepare to shrink and wrap the English translation
        # Start with base font size
        try:
            current_size = base_font.size
        except AttributeError:
            # If load_default() was used, pick a default size
            current_size = 24

        current_font = base_font

        while True:
            # Measure a Japanese glyph and an English sample to get a conservative character width
            try:
                jp_bbox = current_font.getbbox("あ")
                jp_char_width = jp_bbox[2] - jp_bbox[0]
            except Exception:
                jp_char_width = 12

            try:
                sample_bbox = current_font.getbbox("Street")
                sample_width = sample_bbox[2] - sample_bbox[0]
                en_char_width = max(1, sample_width // 6)
            except Exception:
                en_char_width = jp_char_width

            avg_char_width = max(jp_char_width, en_char_width, 10)

            # Estimate how many characters fit per line
            chars_per_line = max(1, w // avg_char_width)
            wrapper = textwrap.TextWrapper(width=chars_per_line)
            wrapped_lines = wrapper.wrap(translation)

            # Calculate total height of all wrapped lines (including spacing)
            total_text_height = 0
            line_heights = []
            for line in wrapped_lines:
                bbox = current_font.getbbox(line)
                lh = bbox[3] - bbox[1]
                line_heights.append(lh)
                total_text_height += lh
            # Add 4px spacing between lines
            total_text_height += max(0, (len(wrapped_lines) - 1) * 4)

            # Check if text fits within the box height, or if we've reached minimum font size
            if total_text_height <= h or current_size <= 8:
                break

            # Otherwise, shrink the font and retry
            current_size -= 2
            try:
                current_font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=current_size)
            except Exception:
                current_font = ImageFont.load_default()
                break

        # 3c) Center the wrapped text vertically within the box
        y_offset = y + max(0, (h - total_text_height) // 2)

        for idx, line in enumerate(wrapped_lines):
            bbox = current_font.getbbox(line)
            line_width = bbox[2] - bbox[0]
            line_height = bbox[3] - bbox[1]

            # Center horizontally within the box
            text_x = x + max(0, (w - line_width) // 2)
            draw.text((text_x, y_offset), line, fill="black", font=current_font)

            # Move down for the next line (4px line spacing)
            y_offset += line_height + 4

    # 4) Save to a temporary file and return
    temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    image.save(temp_file.name, format="JPEG")
    temp_file.seek(0)
    return send_file(temp_file.name, mimetype="image/jpeg")


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
