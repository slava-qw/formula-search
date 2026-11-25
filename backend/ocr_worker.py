"""One image per JSON line, in the optional OCR environment's Python.

Model imports/diagnostics use stderr so stdout remains a framed protocol.
No web server, PDF parser or core dependencies are imported in this process.
"""
import base64
import contextlib
import io
import json
import sys


def main():
    model = None
    for line in sys.stdin:
        request = {}
        try:
            if len(line) > 24 * 1024 * 1024:
                raise ValueError('Equation crop is too large.')
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                from PIL import Image
                if model is None:
                    import torch
                    torch.set_num_threads(2)
                    from pix2tex.cli import LatexOCR
                    model = LatexOCR()
                image = Image.open(io.BytesIO(base64.b64decode(request['image'], validate=True)))
                latex = model(image).strip()
            if len(latex) > 20000:
                raise ValueError('Recognizer output exceeds the formula limit.')
            result = {'status': 'complete', 'latex': latex}
        except Exception as error:
            result = {'status': 'unsupported', 'latex': '', 'error': f'{type(error).__name__}: {str(error)[:400]}'}
        print(json.dumps({'id': request.get('id'), 'result': result}), flush=True)


if __name__ == '__main__':
    main()
