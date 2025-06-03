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

def _load_font(preferred_size):
    """
    Attempt to load DejaVuSans-Bold or fallback to default PIL font.
    """
    try:
        # Adjust the path if you bundle a TTF with your deploy
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=preferred_size)
    except IOError:
        return ImageFont.load_default()

def _fit_text(draw, text, box_width, box_height, font_path="DejaVuSans-Bold.ttf"):
    """
    Return a PIL font object sized so that `text` fits within (box_width, box_height).
    If necessary, wrap to multiple lines. Returns (font, wrapped_lines).
    """
    # Start with a large font size, then shrink until it fits.
    max_font_size = min(box_height, box_width // max(1, len(text))) * 2
    font_size = max_font_size

    # Load a PIL ImageFont (we'll shrink if needed)
    try:
        font = ImageFont.truetype(font_path, size=font_size)
    except IOError:
        font = ImageFont.load_default()
        return font, [text]

    # Determine wrapping width roughly by character count
    # We'll adjust via binary search-like loop
    while font_size > 8:  # don't go below size 8
        font = ImageFont.truetype(font_path, font_size)
        # Wrap text so that line width <= box_width
        lines = textwrap.wrap(text, width=max(1, int(len(text) * box_width / (font.getsize(text)[0] + 1))))
        # Compute total text height
        total_h = sum(font.getsize(line)[1] for line in lines) + (len(lines)-1) * 2
        max_line_w = max((font.getsize(line)[0] for line in lines), default=0)
        if total_h <= box_height and max_line_w <= box_width:
            return font, lines
        font_size -= 2

    # If nothing fits well, return the smallest font and unwrapped text
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
    font_size_hint = payload.get("fontSize", 20)

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

    # For each OCR block, draw a filled rectangle and the translated text inside
    for item in ocr_results:
        box = item.get("box", [])
        translation = item.get("translation", "") or item.get("text", "")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue

        x, y, w, h = box
        # Draw a solid white rectangle over the original text area (preserves transparency)
        rect_coords = [(x, y), (x + w, y + h)]
        draw.rectangle(rect_coords, fill=(255, 255, 255, 255))

        # Fit translated text into the box
        font, lines = _fit_text(draw, translation, w, h, font_path="DejaVuSans-Bold.ttf")

        # Compute vertical offset so text is centered
        total_text_height = sum(font.getsize(line)[1] for line in lines) + (len(lines) - 1) * 2
        current_y = y + max(0, (h - total_text_height) // 2)

        for line in lines:
            line_w, line_h = font.getsize(line)
            # Center horizontally
            current_x = x + max(0, (w - line_w) // 2)
            draw.text((current_x, current_y), line, fill="black", font=font)
            current_y += line_h + 2

    # Write to a temporary file, then stream it back
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
    # In production, you’d want to disable debug=True
    app.run(host="0.0.0.0", port=port, debug=False)
