"""Convert unrotated extraction coordinates to a PDF's initial display space.

Glyph extraction and source matching stay in PyMuPDF's unrotated crop space.
Search results (and therefore saved navigation targets) use the page as Chrome
initially displays it, including the PDF's own /Rotate entry. Browser rotation
is an additional transform, handled by the extension.
"""


def unrotated_page_size(page):
    # Page.rect includes /UserUnit scaling; CropBox dimensions do not. Text
    # coordinates are scaled too, so undo orientation without losing that scale.
    rect = page.rect
    return (rect.height, rect.width) if page.rotation in (90, 270) else (rect.width, rect.height)


def displayed_geometry(bbox, width, height, rotation=0):
    rotation %= 360
    display_width, display_height = (height, width) if rotation in (90, 270) else (width, height)
    if bbox is not None and rotation:
        x0, y0, x1, y1 = bbox
        if rotation == 90:
            bbox = (height-y1, x0, height-y0, x1)
        elif rotation == 180:
            bbox = (width-x1, height-y1, width-x0, height-y0)
        elif rotation == 270:
            bbox = (y0, width-x1, y1, width-x0)
    return bbox, display_width, display_height
