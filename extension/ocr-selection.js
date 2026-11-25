/* Selection is expressed in the PDF's initial display coordinates. */
(function (root) {
  'use strict';
  async function selectEquation({backend, url, page, signal}) {
    const response = await FormulaRequests.request(`${backend}/ocr-preview`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url, page}), signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not preview this page.');
    signal.throwIfAborted();
    const image = new Image();
    image.src = data.image;
    await image.decode();
    signal.throwIfAborted();
    return new Promise(resolve => {
      const dialog = document.createElement('dialog');
      dialog.className = 'ocr-selection';
      const title = document.createElement('h3');
      title.textContent = `Select one equation on page ${page + 1}`;
      const help = document.createElement('p');
      help.textContent = data.ocr.installed ? 'Drag a rectangle around the equation. Check the recognized result against the paper.' : data.ocr.message;
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
      canvas.style.cssText = 'width:100%;height:auto;touch-action:none;cursor:crosshair;display:block';
      const zoomLabel = document.createElement('label');
      zoomLabel.textContent = 'Zoom page ';
      const zoom = document.createElement('input');
      zoom.type = 'range'; zoom.min = '100'; zoom.max = '300'; zoom.step = '25'; zoom.value = '100';
      zoom.setAttribute('aria-label', 'Zoom page');
      zoomLabel.append(zoom);
      zoom.oninput = () => { canvas.style.width = `${zoom.value}%`; };
      const scroller = document.createElement('div');
      scroller.style.cssText = 'max-height:55vh;overflow:auto';
      scroller.append(canvas);
      const actions = document.createElement('div');
      const cancel = document.createElement('button');
      cancel.textContent = 'Cancel'; cancel.type = 'button';
      const run = document.createElement('button');
      run.textContent = 'Recognize equation'; run.type = 'button'; run.disabled = true;
      actions.append(cancel, run); dialog.append(title, help, zoomLabel, scroller, actions);
      const ctx = canvas.getContext('2d');
      let start = null, region = null;
      const draw = () => {
        ctx.drawImage(image, 0, 0);
        if (region) {
          ctx.strokeStyle = '#2563eb'; ctx.lineWidth = 3;
          ctx.strokeRect(region[0], region[1], region[2]-region[0], region[3]-region[1]);
        }
      };
      const point = event => {
        const rect = canvas.getBoundingClientRect();
        return [Math.max(0, Math.min(canvas.width, (event.clientX-rect.left)*canvas.width/rect.width)),
          Math.max(0, Math.min(canvas.height, (event.clientY-rect.top)*canvas.height/rect.height))];
      };
      canvas.onpointerdown = event => {
        start = point(event); region = null; run.disabled = true; draw(); canvas.setPointerCapture(event.pointerId);
      };
      canvas.onpointermove = event => {
        if (!start) return;
        const end = point(event);
        region = [Math.min(start[0],end[0]), Math.min(start[1],end[1]), Math.max(start[0],end[0]), Math.max(start[1],end[1])];
        draw();
      };
      canvas.onpointerup = () => {
        start = null;
        run.disabled = !data.ocr.installed || !region || region[2]-region[0] < 5 || region[3]-region[1] < 5 ||
          (region[2]-region[0])*(region[3]-region[1]) > canvas.width*canvas.height*.9;
      };
      canvas.onpointercancel = () => { start = region = null; run.disabled = true; draw(); };
      const abort = () => finish(null);
      const finish = value => {
        signal.removeEventListener('abort', abort); dialog.remove(); resolve(value);
      };
      cancel.onclick = () => finish(null);
      dialog.oncancel = event => { event.preventDefault(); finish(null); };
      run.onclick = () => {
        const crop = document.createElement('canvas');
        crop.width = Math.max(1,Math.ceil(region[2]-region[0])); crop.height = Math.max(1,Math.ceil(region[3]-region[1]));
        crop.getContext('2d').drawImage(image,region[0],region[1],region[2]-region[0],region[3]-region[1],0,0,crop.width,crop.height);
        finish({region: region.map((v,i) => v*(i%2 ? data.page_h/canvas.height : data.page_w/canvas.width)),
                context_revision:data.context_revision, preview_image:crop.toDataURL('image/png')});
      };
      signal.addEventListener('abort', abort, {once: true});
      document.body.append(dialog); draw(); dialog.showModal(); cancel.focus();
    });
  }
  function reviewEquation(latex, signal, previewImage, errorMessage = '') {
    signal.throwIfAborted();
    return new Promise(resolve => {
      const dialog = document.createElement('dialog'); dialog.className = 'ocr-selection';
      const title = document.createElement('h3'); title.textContent = 'Check the recognized equation';
      const help = document.createElement('p'); help.textContent = 'OCR can misread symbols and mathematical fonts. Correct the LaTeX before adding it to search.';
      if (errorMessage) { help.textContent = `Could not add the equation: ${errorMessage} Your correction is retained below.`; help.setAttribute('role','alert'); }
      const original = document.createElement('img'); original.alt = 'Selected equation from the PDF'; original.src = previewImage;
      original.style.cssText = 'max-width:100%;max-height:180px;object-fit:contain;background:white';
      const input = document.createElement('textarea'); input.value = latex; input.maxLength = 20000;
      input.rows = 5; input.style.cssText = 'box-sizing:border-box;width:100%'; input.setAttribute('aria-label','Recognized LaTeX');
      const preview = document.createElement('div'); preview.style.cssText = 'overflow:auto;max-height:180px;padding:8px';
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel';
      const save = document.createElement('button'); save.type = 'button'; save.textContent = 'Add equation to search';
      const redraw = () => {
        save.disabled = !input.value.trim();
        try { katex.render(input.value, preview, {displayMode:true,throwOnError:false,trust:false,macros:{},maxExpand:1000}); }
        catch { preview.textContent = input.value; }
      };
      const abort = () => finish(null);
      const finish = value => {signal.removeEventListener('abort',abort); dialog.remove(); resolve(value);};
      cancel.onclick = () => finish(null); save.onclick = () => finish(input.value.trim());
      dialog.oncancel = event => {event.preventDefault();finish(null);};
      input.oninput = redraw; signal.addEventListener('abort',abort,{once:true});
      dialog.append(title,help,original,input,preview,cancel,save); document.body.append(dialog); redraw(); dialog.showModal(); input.focus();
    });
  }
  root.FormulaOcrSelection = {selectEquation, reviewEquation};
})(globalThis);
