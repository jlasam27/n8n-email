from flask import Flask, request, send_file, abort
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO

app = Flask(__name__)

@app.route('/overlay', methods=['POST'])
def overlay_text():
    data = request.get_json()
    if not data or 'imageUrl' not in data or 'ocrResults' not in data:
        abort(400, 'Invalid input JSON: must contain imageUrl and ocrResults')

    image_url = data['imageUrl']
    ocr_results = data['ocrResults']

    # 1) Fetch the image
    try:
        resp = requests.get(image_url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        abort(400, f'Error fetching image: {e}')

    try:
        image = Image.open(BytesIO(resp.content)).convert('RGB')
    except Exception as e:
        abort(400, f'Invalid image data: {e}')

    draw = ImageDraw.Draw(image)
    img_w, img_h = image.width, image.height

    # 2) Prepare font path and minimum size
    #    Adjust "arial.ttf" to a valid TTF on your system, or bundle DejaVuSans-Bold.ttf, etc.
    font_path = 'arial.ttf'
    min_font_size = 14

    def get_text_bbox(text, font):
        """
        Return (left, top, right, bottom) for the given text using the given font.
        Handles multiline text with newline characters.
        """
        return draw.multiline_textbbox((0, 0), text, font=font)

    def wrap_text_to_width(text, font, max_width):
        """
        Wrap text into multiple lines so that each line does not exceed max_width in pixels.
        Uses font.getbbox() to measure each candidate line.
        """
        words = text.split()
        if not words:
            return []

        lines = []
        current = words[0]

        for word in words[1:]:
            candidate = current + " " + word
            bbox = font.getbbox(candidate)
            w = bbox[2] - bbox[0]
            if w <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    # 3) Iterate over OCR results
    for item in ocr_results:
        text = item.get('translatedText') or item.get('text') or ''
        if not text.strip():
            continue

        # Extract bounding box
        box = item.get('boundingBox') or item.get('bbox') or item.get('box')
        if not box:
            continue

        # Normalize box format
        if isinstance(box, dict):
            x = int(box.get('left', box.get('x', 0)))
            y = int(box.get('top', box.get('y', 0)))
            w = int(box.get('width', box.get('w', 0)))
            h = int(box.get('height', box.get('h', 0)))
        elif isinstance(box, (list, tuple)) and len(box) >= 4:
            x, y, w, h = map(int, box[:4])
        else:
            continue

        # Clamp box to image boundaries
        if x < 0:
            x = 0
        if y < 0:
            y = 0
        if x + w > img_w:
            w = img_w - x
        if y + h > img_h:
            h = img_h - y
        if w <= 0 or h <= 0:
            continue

        # 3a) Start with the largest font that won't exceed image height (and ≥ min_font_size)
        font_size = max(min_font_size, h)
        try:
            font = ImageFont.truetype(font_path, font_size)
        except OSError:
            font = ImageFont.load_default()
            font_size = min_font_size  # fallback

        # 3b) Wrap text and shrink font until it fits width
        lines = wrap_text_to_width(text, font, w)
        while True:
            # After wrapping, check if any line is too wide
            too_wide = False
            for ln in lines:
                bbox = font.getbbox(ln)
                line_w = bbox[2] - bbox[0]
                if line_w > w:
                    too_wide = True
                    break

            # Compute total text height for these lines
            ascent, descent = font.getmetrics()
            line_height = ascent + descent
            total_text_height = line_height * len(lines)

            if (too_wide or total_text_height > h) and font_size > min_font_size:
                font_size -= 1
                try:
                    font = ImageFont.truetype(font_path, font_size)
                except OSError:
                    font = ImageFont.load_default()
                    break
                lines = wrap_text_to_width(text, font, w)
                continue
            else:
                break

        # 3c) Recompute total text height (with final font)
        ascent, descent = font.getmetrics()
        line_height = ascent + descent
        total_text_height = line_height * len(lines)

        # If height still exceeds box, expand box downward (clamped to image)
        if total_text_height > h:
            new_h = total_text_height
            if y + new_h > img_h:
                new_h = img_h - y
            h = new_h

        # 3d) Build multiline text string
        text_block = "\n".join(lines)

        # 3e) Compute exact text bounding box at (x, y)
        text_bbox = draw.multiline_textbbox((x, y), text_block, font=font)
        tb_left, tb_top, tb_right, tb_bottom = text_bbox

        # 3f) Draw white padded rectangle behind the text
        padding = 4
        rect_left = max(tb_left - padding, 0)
        rect_top = max(tb_top - padding, 0)
        rect_right = min(tb_right + padding, img_w)
        rect_bottom = min(tb_bottom + padding, img_h)
        draw.rectangle([rect_left, rect_top, rect_right, rect_bottom], fill='white')

        # 3g) Draw the text itself (black color)
        draw.multiline_text((x, y), text_block, fill='black', font=font)

    # 4) Return the modified image in-memory
    img_buffer = BytesIO()
    image.save(img_buffer, format='PNG')
    img_buffer.seek(0)
    return send_file(img_buffer, mimetype='image/png')


if __name__ == '__main__':
    app.run(debug=True)
