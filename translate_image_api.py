from flask import Flask, request, send_file, jsonify
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
import tempfile
import os
import textwrap

app = Flask(__name__)

@app.route('/translate-image', methods=['POST'])
def translate_image():
    data = request.get_json()
    image_url = data.get("imageUrl")
    ocr_results = data.get("ocrResults", [])

    # 0) Validate inputs
    if not image_url or not isinstance(ocr_results, list):
        return jsonify({"error": "Missing or invalid imageUrl/ocrResults"}), 400

    # 1) Fetch the image (with a browser-like User-Agent to avoid 403)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/90.0.4430.93 Safari/537.36"
        ),
    }
    try:
        resp = requests.get(image_url, headers=headers, stream=True, timeout=10)
        resp.raise_for_status()
        image = Image.open(resp.raw).convert("RGB")
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch image: {e}"}), 400
    except Exception as e:
        return jsonify({"error": f"Cannot open image: {e}"}), 400

    draw = ImageDraw.Draw(image)
    img_w, img_h = image.size

    # 2) Load a TTF font (fallback to default)
    try:
        base_font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=24)
    except Exception:
        base_font = ImageFont.load_default()

    # 3) For each OCR block, erase and draw the translation
    for item in ocr_results:
        raw_box = item.get("box", [])
        translation = item.get("translation", "").strip()
        if not translation or not (isinstance(raw_box, list) and len(raw_box) == 4):
            continue

        x0, y0, w0, h0 = raw_box
        if w0 <= 0 or h0 <= 0:
            continue

        # 3a) Compute padded “erase” rectangle
        PAD = 2
        left = max(0, x0 - PAD)
        top = max(0, y0 - PAD)
        right = min(img_w, x0 + w0 + PAD)
        bottom = min(img_h, y0 + h0 + PAD)
        if bottom < top:
            bottom = top
        if right < left:
            right = left

        # Draw a white rectangle to cover the original text
        draw.rectangle([left, top, right, bottom], fill="white")

        # 3b) Determine font size & wrapping so text fits
        # Start with base font size, then shrink or expand box if needed
        font_size = getattr(base_font, "size", 24)
        current_font = base_font

        # Helper to measure a line of text
        def measure_text(text, font):
            """Return (width, height) of the given text with this font."""
            bbox = font.getbbox(text)
            return (bbox[2] - bbox[0], bbox[3] - bbox[1])

        # Wrap text into lines given a font and box width
        def wrap_text_to_width(text, font, max_width):
            """Return a list of lines wrapped so that each line's pixel width ≤ max_width."""
            words = text.split()
            if not words:
                return []
            lines = []
            current_line = words[0]
            for word in words[1:]:
                test_line = current_line + " " + word
                line_w, _ = measure_text(test_line, font)
                if line_w <= max_width:
                    current_line = test_line
                else:
                    lines.append(current_line)
                    current_line = word
            lines.append(current_line)
            return lines

        # Attempt to fit text within (w0, h0). If too large, shrink font down to min 6.
        # If still too big, expand box dimensions up to image boundaries.
        MIN_FONT = 6
        FONT_STEP = 2

        while True:
            # Wrap at current font and original box width
            lines = wrap_text_to_width(translation, current_font, w0)
            # Measure each line, accumulate total height
            line_widths = []
            line_heights = []
            total_height = 0
            for line in lines:
                lw, lh = measure_text(line, current_font)
                line_widths.append(lw)
                line_heights.append(lh)
                total_height += lh
            # Add 4px spacing between lines
            total_height += max(0, (len(lines) - 1) * 4)

            # If any line exceeds w0, or total height exceeds h0, try shrinking font
            if (lines and (max(line_widths) > w0 or total_height > h0)) and font_size > MIN_FONT:
                font_size = max(MIN_FONT, font_size - FONT_STEP)
                try:
                    current_font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=font_size)
                except Exception:
                    current_font = ImageFont.load_default()
                    break
                continue

            # If shrink didn't help (font at min size) but still doesn't fit, expand box
            if lines and (max(line_widths) > w0 or total_height > h0):
                # Expand to accommodate width and height
                new_w = max(w0, max(line_widths))
                new_h = max(h0, total_height)

                # Clamp to image boundaries
                w0 = min(new_w, img_w - x0)
                h0 = min(new_h, img_h - y0)

                # After expansion, re-wrap because box width changed
                lines = wrap_text_to_width(translation, current_font, w0)
                # Recalculate sizes
                line_widths = []
                line_heights = []
                total_height = 0
                for line in lines:
                    lw, lh = measure_text(line, current_font)
                    line_widths.append(lw)
                    line_heights.append(lh)
                    total_height += lh
                total_height += max(0, (len(lines) - 1) * 4)
                # If still doesn't fit in height, clamp height and break
                if total_height > (img_h - y0):
                    h0 = img_h - y0
                break

            # Everything fits, break
            break

        # 3c) Compute vertical offset to center lines within the final box height
        total_height = sum(line_heights) + max(0, (len(lines) - 1) * 4)
        y_offset = y0 + max(0, (h0 - total_height) // 2)

        # 3d) Draw each line centered horizontally in the box
        for idx, line in enumerate(lines):
            lw, lh = measure_text(line, current_font)
            text_x = x0 + max(0, (w0 - lw) // 2)
            draw.text((text_x, y_offset), line, fill="black", font=current_font)
            y_offset += lh + 4  # 4px line spacing

    # 4) Save to a temp file and return as JPEG
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    image.save(tmp.name, format="JPEG")
    tmp.seek(0)
    return send_file(tmp.name, mimetype="image/jpeg")


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
