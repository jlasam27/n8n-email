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

    # Validate inputs:
    if not image_url or not isinstance(ocr_results, list):
        return jsonify({"error": "Missing or invalid imageUrl/ocrResults"}), 400

    # Use a browser‐like User-Agent to avoid 403 “Forbidden”
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/90.0.4430.93 Safari/537.36"
        ),
    }

    # 1) Fetch the image from image_url
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

    # 2) Load a TTF font. We’ll start at size=24 and shrink from there.
    #    If DejaVuSans-Bold.ttf is unavailable, we fall back to a PIL default.
    try:
        base_font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=24)
    except Exception:
        base_font = ImageFont.load_default()

    # 3) Process each OCR block one by one:
    for item in ocr_results:
        raw_box = item.get("box", [])
        translation = item.get("translation", "").strip()
        if not translation or not (isinstance(raw_box, list) and len(raw_box) == 4):
            continue

        x, y, w, h = raw_box

        # 3a) Add a small padding (2px on all sides) so we cover cleanly:
        PAD = 2
        cover_left   = max(0, x - PAD)
        cover_top    = max(0, y - PAD)
        cover_right  = min(image.width, x + w + PAD)
        cover_bottom = min(image.height, y + h + PAD)

        draw.rectangle(
            [cover_left, cover_top, cover_right, cover_bottom],
            fill="white"
        )

        # 3b) Wrap & shrink logic:
        #    Start with the base font size, shrink until everything truly fits.
        #    Minimum font size is 6px.
        try:
            current_size = base_font.size
        except AttributeError:
            # If load_default() was used, assume starting size = 24
            current_size = 24
        current_font = base_font

        wrapped_lines = [translation]

        while True:
            # Measure each wrapped line’s pixel width via getbbox()
            # and confirm they fit within the box width.
            all_fit = True
            line_heights = []
            total_text_height = 0

            for line in wrapped_lines:
                bbox = current_font.getbbox(line)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
                line_heights.append(text_h)

                if text_w > w:
                    all_fit = False
                total_text_height += text_h

            # Add 4px between each line
            total_text_height += max(0, (len(wrapped_lines) - 1) * 4)

            # If height doesn’t fit, or any line’s width > w, shrink font (if possible)
            if (not all_fit or total_text_height > h) and current_size > 6:
                current_size -= 2
                try:
                    current_font = ImageFont.truetype(
                        "DejaVuSans-Bold.ttf", size=current_size
                    )
                except Exception:
                    current_font = ImageFont.load_default()
                    break  # can’t shrink further

                # Recompute a conservative “chars per line” bound:
                #  Measure a Japanese character (“あ”) and an English sample (“Street”) for safety
                try:
                    jp_bbox = current_font.getbbox("あ")
                    jp_w = jp_bbox[2] - jp_bbox[0]
                except Exception:
                    jp_w = 12

                try:
                    sample_bbox = current_font.getbbox("Street")
                    en_w = (sample_bbox[2] - sample_bbox[0]) // 6
                except Exception:
                    en_w = jp_w

                avg_char_width = max(jp_w, en_w, 8)
                chars_per_line = max(1, w // avg_char_width)

                wrapped_lines = textwrap.TextWrapper(
                    width=chars_per_line
                ).wrap(translation)
                continue
            else:
                # Everything fits (or we reached minimum font size)
                break

        # 3c) Now draw each wrapped line, centered inside the box:
        #     Compute final total height and begin vertically centered.
        total_text_height = sum(line_heights) + max(0, (len(wrapped_lines) - 1) * 4)
        y_offset = y + (h - total_text_height) // 2

        for idx, line in enumerate(wrapped_lines):
            bbox = current_font.getbbox(line)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            # Center horizontally:
            text_x = x + (w - text_w) // 2

            draw.text((text_x, y_offset), line, fill="black", font=current_font)
            y_offset += text_h + 4  # 4px line spacing

    # 4) Save the result to a temporary JPEG and return it
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    image.save(tmp.name, format="JPEG")
    tmp.seek(0)
    return send_file(tmp.name, mimetype="image/jpeg")


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
