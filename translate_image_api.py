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
    Fetch an image from `url` using a browser‐like User-Agent to avoid 403s.
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
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=preferred_size)
    except IOError:
        return ImageFont.load_default()

def _text_dimensions(font, text):
    """
    Given a FreeTypeFont `font` and a string `text`, return (width, height).
    Uses font.getbbox() to compute the bounding box.
    """
    bbox = font.getbbox(text)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return width, height

def _fit_text(text, box_width, box_height, font_path="DejaVuSans-Bold.ttf"):
    """
    Return a (font, wrapped_lines) pair so that `text` fits within (box_width, box_height).
    - Tries decreasing font sizes until it fits.
    - Wraps text to multiple lines if necessary.
    """
    # Start with a large font size, then shrink until it fits
    # A reasonable upper bound might be box_height itself
    font_size = box_height
    if font_size < 8:
        font_size = 8

    while font_size >= 8:
        try:
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            font = ImageFont.load_default()
            break

        # Determine a rough wrap‐width in characters:
        #   measure full text width at this font size
        full_w, full_h = _text_dimensions(font, text)
        if full_w <= box_width and full_h <= box_height:
            # No wrapping needed
            return font, [text]

        # Otherwise, try wrapping into lines.
        # We guess a max line length based on character count and ratio:
        approx_char_per_line = max(1, int(len(text) * box_width / (full_w + 1)))
        lines = textwrap.wrap(text, width=approx_char_per_line)

        # Compute total height of these wrapped lines
        total_h = 0
        max_line_w = 0
        for line in lines:
            w, h = _text_dimensions(font, line)
            total_h += h + 2  # 2 pixels of line spacing
            if w > max_line_w:
                max_line_w = w

        total_h -= 2  # remove trailing extra spacing
        if total_h <= box_height and max_line_w <= box_width:
            return font, lines

        # Otherwise, reduce font size and try again
        font_size -= 2

    # If we exit loop, fall back to smallest font (size 8 or default)
    try:
        font = ImageFont.truetype(font_path, 8)
    except IOError:
        font = ImageFont.load_default()
    return font, textwrap.wrap(text, width=max(1, len(text)))

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
        font, lines = _fit_text(translation, w, h, font_path="DejaVuSans-Bold.ttf")

        # Compute total height of all wrapped lines
        total_h = 0
        for line in lines:
            _, lh = _text_dimensions(font, line)
            total_h += lh + 2
        total_h -= 2  # remove extra spacing after last line

        # Start drawing so that text is vertically centered in the box
        current_y = y + max(0, (h - total_h) // 2)

        for line in lines:
            lw, lh = _text_dimensions(font, line)
            current_x = x + max(0, (w - lw) // 2)
            draw.text((current_x, current_y), line, fill="black", font=font)
            current_y += lh + 2

    # Save the modified image to a temp file, then stream it back
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
