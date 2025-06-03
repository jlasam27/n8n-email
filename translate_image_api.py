from flask import Flask, request, send_file, jsonify, after_this_request
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
import os
import tempfile
import textwrap

app = Flask(__name__)

def _fetch_image(url):
    """
    Fetch an image from `url` using a browser-like User-Agent to avoid 403s.
    Returns a PIL Image in RGBA mode or raises an exception.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/90.0.4430.93 Safari/537.36"
        ),
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    img = Image.open(BytesIO(resp.content)).convert("RGBA")
    return img

def _load_font(size):
    """
    Try to load DejaVuSans-Bold at `size`. If missing, fall back to PIL default.
    """
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except IOError:
        return ImageFont.load_default()

def _text_dimensions(font, text):
    """
    Given a FreeTypeFont `font` and a string `text`, return (width, height).
    Uses font.getbbox() to compute the bounding box.
    """
    bbox = font.getbbox(text)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    return w, h

def _fit_text(text, box_width, box_height, font_path="DejaVuSans-Bold.ttf"):
    """
    Find a font and line-wrapping for `text` so that it fits within (box_width, box_height).
    Returns (font, lines) where lines is a list of wrapped strings.
    If it cannot fit even at minimum size, it still returns the smallest font with a single line.
    """
    # Start with a font size near box_height, then shrink if necessary
    font_size = max(box_height, 12)
    if font_size < 8:
        font_size = 8

    while font_size >= 8:
        try:
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            font = ImageFont.load_default()
            break

        full_w, full_h = _text_dimensions(font, text)
        # If single‐line text fits exactly, we’re done
        if full_w <= box_width and full_h <= box_height:
            return font, [text]

        # Otherwise, try wrapping into multiple lines
        # Estimate max characters per line based on ratio
        approx_chars = max(1, int(len(text) * box_width / (full_w + 1)))
        lines = textwrap.wrap(text, width=approx_chars)

        # Compute total height and max line width of wrapped lines
        total_h = 0
        max_w = 0
        for line in lines:
            lw, lh = _text_dimensions(font, line)
            total_h += lh + 2  # 2px line spacing
            if lw > max_w:
                max_w = lw
        total_h -= 2  # remove extra spacing after last line

        if total_h <= box_height and max_w <= box_width:
            return font, lines

        font_size -= 2

    # If we exit loop: use a minimum font size (8) or default
    try:
        font = ImageFont.truetype(font_path, 8)
    except IOError:
        font = ImageFont.load_default()
    return font, [text]

@app.route('/translate-image', methods=['POST'])
def translate_image():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Invalid JSON payload"}), 400

    image_url = payload.get("url") or payload.get("imageUrl")
    ocr_results = payload.get("ocrResults") or payload.get("results")

    if not image_url:
        return jsonify({"error": "Missing ‘url’ (image URL) in payload"}), 400
    if ocr_results is None:
        return jsonify({"error": "Missing ‘ocrResults’ (array) in payload"}), 400

    # Fetch and open the image
    try:
        image = _fetch_image(image_url)
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch image: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Cannot open image: {str(e)}"}), 400

    draw = ImageDraw.Draw(image)
    img_w, img_h = image.size

    for item in ocr_results:
        box = item.get("box", [])
        translation = item.get("translation", "") or item.get("text", "")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue

        x, y, w, h = box

        # Clamp the box to image bounds
        x = max(0, x)
        y = max(0, y)
        if x >= img_w or y >= img_h:
            # Box starts entirely outside the image
            continue

        w = min(w, img_w - x)
        h = min(h, img_h - y)
        if w <= 0 or h <= 0:
            continue

        # Draw white rectangle over original text area (preserves transparency)
        draw.rectangle([(x, y), (x + w, y + h)], fill=(255, 255, 255, 255))

        # Fit translated text into the clamped box
        font, lines = _fit_text(translation, w, h, font_path="DejaVuSans-Bold.ttf")

        # Compute total height of wrapped lines
        total_h = 0
        for line in lines:
            _, lh = _text_dimensions(font, line)
            total_h += lh + 2
        total_h -= 2  # remove extra spacing after last line

        # Determine starting Y so text is vertically centered
        current_y = y + max(0, (h - total_h) // 2)

        for line in lines:
            lw, lh = _text_dimensions(font, line)
            # Center horizontally within the box
            current_x = x + max(0, (w - lw) // 2)
            # Clamp the drawn text just in case
            if current_x + lw > img_w:
                current_x = img_w - lw
            if current_y + lh > img_h:
                break

            draw.text((current_x, current_y), line, fill="black", font=font)
            current_y += lh + 2
            if current_y > y + h:
                break  # no more vertical space

    # Save to a temporary file and stream back
    temp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        image.save(temp.name, format="PNG")
    except Exception as e:
        temp.close()
        os.unlink(temp.name)
        return jsonify({"error": f"Failed to save modified image: {str(e)}"}), 500

    @after_this_request
    def cleanup(response):
        try:
            os.unlink(temp.name)
        except Exception:
            pass
        return response

    return send_file(temp.name, mimetype='image/png')


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
