import hashlib
import io
import warnings
from PIL import Image
from app.db import db
from app.settings import HOME


def store_image(content, real):
    if len(content) > 20_000_000:
        raise ValueError('图片超过 20MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as img:
                if img.format not in ('PNG', 'JPEG', 'WEBP') or img.width*img.height > 40_000_000:
                    raise ValueError()
                mime = Image.MIME[img.format]
                img.verify()
    except Exception:
        raise ValueError('无效图片；仅支持完整的 PNG / JPEG / WebP，最多 4000 万像素') from None
    digest = hashlib.sha256(content).hexdigest()
    with db() as c:
        old = c.execute('SELECT * FROM images WHERE id=?', (digest,)).fetchone()
        if old and bool(old['real']) != real:
            raise ValueError('该图片已有不同来源声明，禁止将 Mock 图片提升为真实图片')
        (HOME/'images'/digest).write_bytes(content)
        c.execute('INSERT OR IGNORE INTO images VALUES(?,?,?)', (digest, mime, int(real)))
    return {'id': digest, 'mime': mime, 'real': real}


def mock_image():
    # A visibly synthetic integration fixture, never a competition screenshot.
    from PIL import ImageDraw
    img = Image.new('RGB', (400, 220), '#eef3f8')
    draw = ImageDraw.Draw(img)
    for x in range(0, 400, 40):
        draw.line((x, 0, x, 220), fill='#ccd6e0')
    for y in range(0, 220, 40):
        draw.line((0, y, 400, y), fill='#ccd6e0')
    draw.rectangle((30, 70, 370, 150), fill='white')
    draw.text((60, 90), 'MOCK IMAGE - NOT A REAL BENCHMARK', fill='#a52020')
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    return store_image(buffer.getvalue(), False)


def map_images(items):
    with db() as c:
        for item in items:
            if not c.execute('SELECT 1 FROM images WHERE id=?', (item.image_id,)).fetchone():
                raise ValueError('图片资源不存在')
            if not c.execute('SELECT 1 FROM questions WHERE replay_id=? AND image_step=?', (item.replay_id, item.image_step)).fetchone():
                raise ValueError('不存在对应复盘与 step')
        for item in items:
            c.execute('INSERT OR REPLACE INTO image_map VALUES(?,?,?)', (item.replay_id, item.image_step, item.image_id))
    return {'mapped_frames': len(items)}
