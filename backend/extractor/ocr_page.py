from __future__ import annotations
import threading
import base64
import math
from typing import List
import fitz
from PIL import Image
from backend.extractor.text_layer import FormulaCandidate

_ocr_model = None
_ocr_model_lock = threading.Lock()
MAX_PIXELS = 4_000_000


def _region(page, region):
    if region is None or len(region) != 4 or not all(math.isfinite(v) for v in region):
        raise ValueError('Select one equation in the page preview before running OCR.')
    x0, y0, x1, y1 = region
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0 or x1 > page.rect.width or y1 > page.rect.height:
        raise ValueError('The equation region must lie inside the current page.')
    if (x1-x0) * (y1-y0) > page.rect.width * page.rect.height * .90:
        raise ValueError('Select a single equation rather than the whole page.')
    return fitz.Rect(region)


def page_preview(pdf_bytes, page_num):
    with fitz.open(stream=pdf_bytes, filetype='pdf') as doc:
        if not 0 <= page_num < len(doc):
            raise ValueError('Page is out of range.')
        page = doc[page_num]
        scale = min(1.5, 1200 / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
        return {'page_w': page.rect.width, 'page_h': page.rect.height,
                'image': 'data:image/png;base64,' + base64.b64encode(pix.tobytes('png')).decode('ascii')}


def render_page_image(pdf_bytes: bytes, page_num: int) -> Image.Image:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[page_num]
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def formula_preview(pdf_bytes, page_num, region=None):
    if region is None:
        return page_preview(pdf_bytes,page_num)
    with fitz.open(stream=pdf_bytes,filetype='pdf') as doc:
        if not 0 <= page_num < len(doc):
            raise ValueError('Page is out of range.')
        page = doc[page_num]
        target = _region(page,region)
        from backend.pdf_geometry import displayed_geometry, unrotated_page_size
        width,height = unrotated_page_size(page)
        lines = [fitz.Rect(displayed_geometry(line['bbox'],width,height,page.rotation)[0])
                 for block in page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']
                 for line in block.get('lines',[])]
        envelope = fitz.Rect(target)
        # Include the containing text line and nearby fraction/script fragments,
        # while keeping distant equation numbers and prose out of the crop.
        for _ in range(3):
            for line in lines:
                if (line.y0 <= target.y1+10 and line.y1 >= target.y0-10
                        and line.x0 <= envelope.x1+20 and line.x1 >= envelope.x0-20):
                    envelope |= line
        clip = (envelope + (-12,-10,12,10)) & page.rect
        for line in lines:
            if line.y0 < clip.y0 < line.y1 and line.y1 < envelope.y0:
                clip.y0 = line.y1+1
            if line.y0 < clip.y1 < line.y1 and line.y0 > envelope.y1:
                clip.y1 = line.y0-1
        scale = min(2,1200/max(clip.width,clip.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,colorspace=fitz.csRGB,alpha=False)
        image = Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
        from PIL import ImageDraw
        # Pixmap origins are rounded to device pixels; honor them exactly.
        rectangle = [target.x0*scale-pix.x,target.y0*scale-pix.y,
                     target.x1*scale-pix.x,target.y1*scale-pix.y]
        ImageDraw.Draw(image).rectangle(rectangle,outline='#2563eb',width=2)
        import io
        output=io.BytesIO();image.save(output,format='PNG')
        return {'image':'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode('ascii')}


def equation_crop(pdf_bytes: bytes, page_num: int, region=None):
    with fitz.open(stream=pdf_bytes, filetype='pdf') as doc:
        if not 0 <= page_num < len(doc):
            raise ValueError('Page is out of range.')
        page = doc[page_num]
        clip = _region(page, region)
        width, height = page.rect.width, page.rect.height
        scale = min(2.0, math.sqrt(MAX_PIXELS / (clip.width * clip.height)), 8192 / max(clip.width, clip.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, colorspace=fitz.csRGB, alpha=False)
        return {'image': base64.b64encode(pix.tobytes('png')).decode('ascii'),
                'bbox': tuple(clip), 'page_w': width, 'page_h': height}


def ocr_page(pdf_bytes: bytes, page_num: int, region=None) -> List[FormulaCandidate]:
    """Direct adapter for model tests; production uses the isolated recognizer."""
    import io
    global _ocr_model
    crop = equation_crop(pdf_bytes, page_num, region)
    img = Image.open(io.BytesIO(base64.b64decode(crop['image'])))
    if _ocr_model is None:
        with _ocr_model_lock:
            if _ocr_model is None:
                from pix2tex.cli import LatexOCR
                _ocr_model = LatexOCR()
    latex = _ocr_model(img)
    if not latex or not latex.strip():
        return []
    return [FormulaCandidate(latex=latex.strip(), source="ocr", page=page_num,
                            bbox=crop['bbox'], page_w=crop['page_w'], page_h=crop['page_h'],
                            bbox_quality='ocr-region')]
