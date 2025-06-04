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

    # 1) Fetch the image with a browser‐like User-Agent header
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/90.0.4430.93 Safari/537.36"
        ),
    }
    try:
        resp = requests.get(image_url, headers=headers, timeout=10)
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
    font_path = 'arial.ttf'  # adjust if needed
    min_font_size = 14

    def wrap_text_to_width(text, font, max_width):
        """
        Wrap `text` into multiple lines so that each line does not exceed max_width in pixels.
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

    # 3) Iterate over each OCR entry
    for item in ocr_results:
        text = item.get('translation') or item.get('text') or ''
        text = text.strip()
        if not text:
            continue

        # 3a) Extract/normalize bounding box
        box = item.get('boundingBox') or item.get('bbox') or item.get('box')
        if not box:
            continue

        if isinstance(box, dict):
            x = int(box.get('left', 0))
            y = int(box.get('top', 0))
            w = int(box.get('width', 0))
            h = int(box.get('height', 0))
        elif isinstance(box, (list, tuple)) and len(box) >= 4:
            x, y, w, h = map(int, box[:4])
        else:
            continue

        # Clamp within image
        if x < 0: x = 0
        if y < 0: y = 0
        if x + w > img_w:
            w = img_w - x
        if y + h > img_h:
            h = img_h - y
        if w <= 0 or h <= 0:
            continue

        # 3b) Start with a large font (but at least min_font_size)
        font_size = max(min_font_size, h)
        try:
            font = ImageFont.truetype(font_path, font_size)
        except OSError:
            font = ImageFont.load_default()
            font_size = min_font_size

        # 3c) Wrap & shrink until both width/height fit
        lines = wrap_text_to_width(text, font, w)
        while True:
            # Check if any line is too wide
            too_wide = False
            for ln in lines:
                bbox = font.getbbox(ln)
                line_w = bbox[2] - bbox[0]
                if line_w > w:
                    too_wide = True
                    break

            # Compute total height
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

        # 3d) Recompute total height and expand box downward if needed
        ascent, descent = font.getmetrics()
        line_height = ascent + descent
        total_text_height = line_height * len(lines)
        if total_text_height > h:
            new_h = total_text_height
            if y + new_h > img_h:
                new_h = img_h - y
            h = new_h

        # 3e) Build final multiline text
        text_block = "\n".join(lines)
        text_bbox = draw.multiline_textbbox((x, y), text_block, font=font)
        tb_left, tb_top, tb_right, tb_bottom = text_bbox

        # 3f) Draw white background rectangle (with 4px padding)
        padding = 4
        rect_left = max(tb_left - padding, 0)
        rect_top = max(tb_top - padding, 0)
        rect_right = min(tb_right + padding, img_w)
        rect_bottom = min(tb_bottom + padding, img_h)
        draw.rectangle([rect_left, rect_top, rect_right, rect_bottom], fill='white')

        # 3g) Finally draw the text in black
        draw.multiline_text((x, y), text_block, fill='black', font=font)

    # 4) Return PNG in memory
    buf = BytesIO()
    image.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


if __name__ == '__main__':
    app.run(debug=True)
