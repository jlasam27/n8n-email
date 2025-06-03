from flask import Flask, request, send_file, jsonify
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
import os
import tempfile

app = Flask(__name__)

@app.route('/translate-image', methods=['POST'])
def translate_image():
    data = request.get_json()

    image_url = data.get("imageUrl")
    ocr_results = data.get("ocrResults", [])

    if not image_url or not ocr_results:
        return jsonify({"error": "Missing imageUrl or ocrResults"}), 400

    # Use a browser-like User-Agent to avoid 403s
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/90.0.4430.93 Safari/537.36",
    }

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

    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=20)
    except:
        font = ImageFont.load_default()

    for item in ocr_results:
        box = item.get("box", [])
        translation = item.get("translation", "")
        if len(box) == 4:
            x, y, w, h = box
            draw.rectangle([x, y, x + w, y + h], fill="white")
            draw.text((x, y), translation, fill="black", font=font)

    temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    image.save(temp_file.name, format="JPEG")
    temp_file.seek(0)
    return send_file(temp_file.name, mimetype='image/jpeg')

if __name__ == '__main__':
    # Replit provides the port via the PORT env var
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
