"""Exercise native info-card copying with real mouse/keyboard input.

The native PDF origin receives no clipboard permission grant. Readback is
granted only to a temporary loopback page on the test backend. Failure is
injected into the native clipboard API, then restored; the selectable fallback
is copied with the actual keyboard shortcut. PDF page, scroll and URL must stay
unchanged throughout. Invoked after a real-paper native typography jump.
"""
import time


def verify_native_copy(view,paper,match,out,report,name,exercise_failure=False):
    report['native_copy_method']=__doc__
    context=view.context
    context.clear_permissions()
    origin=view.evaluate('async()=> (await chrome.storage.local.get("fs_settings")).fs_settings.backendUrl')
    context.grant_permissions(['clipboard-read','clipboard-write'],origin=origin)
    reader=context.new_page()
    reader.goto(origin+'/__clipboard_readback__',wait_until='load')
    def native(expression):
        return view.evaluate('''async expression=>FormulaNativeBridge.withViewer(_activeTab.id,async evaluate=>{
            const result=await evaluate(expression);
            if(result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
            return result.result?.value;
        },{url:_activeTab.url})''',expression)
    state_js='''(()=>{
        const button=document.querySelector('.fs-info-card-copy'),status=document.querySelector('.fs-info-card-copy-status'),
            source=document.querySelector('.fs-info-card-copy-source'),viewer=document.querySelector('pdf-viewer'),r=button?.getBoundingClientRect();
        return {status:status?.textContent,button:r?{x:r.x+r.width/2,y:r.y+r.height/2}:null,
            page:viewer.viewport.getMostVisiblePage(),position:viewer.viewport.position,
            focused:document.activeElement?.className,source:source?{value:source.value,readOnly:source.readOnly,
                start:source.selectionStart,end:source.selectionEnd}:null};
    })()'''
    def state():return native(state_js)
    def wait_status(text):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            current=state()
            if text in current['status']:return current
            time.sleep(.05)
        raise AssertionError(state())
    def record(case,**data):report['checks'].append({'surface':'native','case':case,'paper':name,**data})
    def seed_clipboard():
        reader.bring_to_front()
        reader.evaluate('navigator.clipboard.writeText("formula-copy-audit-sentinel")')
        paper.bring_to_front()
    def clipboard():
        reader.bring_to_front()
        text=reader.evaluate('navigator.clipboard.readText()').replace('\r\n','\n')
        paper.bring_to_front()
        return text
    def assert_position(current):
        assert current['page']==before['page'] and current['position']==before['position'],(before,current)
        assert paper.url==url
    url=paper.url
    paper.bring_to_front()
    before=state()
    assert before['button']
    seed_clipboard()
    paper.mouse.click(before['button']['x'],before['button']['y'])
    after=wait_status('Copied')
    assert clipboard()==match['latex'].replace('\r\n','\n')
    assert_position(after)
    paper.screenshot(path=str(out/f'{name}-copy-success.png'))
    record('Real native Copy LaTeX click preserves exact source and PDF position',before=before,after=after)
    seed_clipboard()
    native("document.querySelector('.fs-info-card-close').focus({preventScroll:true})")
    paper.keyboard.press('Tab')
    assert state()['focused']=='fs-info-card-copy'
    paper.keyboard.press('Enter')
    after=wait_status('Copied')
    assert clipboard()==match['latex'].replace('\r\n','\n')
    assert_position(after)
    record('Tab and Enter activate native formula copying without moving the PDF')
    if exercise_failure:
        native('''(()=>{
            globalThis.__copyClipboardDescriptor=Object.getOwnPropertyDescriptor(navigator,'clipboard');
            Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('audit denied');}}});
        })()''')
        try:
            seed_clipboard()
            button=state()['button']
            paper.mouse.click(button['x'],button['y'])
            after=wait_status('Could not copy')
            assert after['source']['value']==match['latex'] and after['source']['readOnly']
            assert after['source']['start']==0 and after['source']['end']==len(match['latex'])
            assert after['focused']=='fs-info-card-copy-source'
            assert_position(after)
            paper.screenshot(path=str(out/f'{name}-copy-fallback.png'))
            paper.keyboard.press('Control+C')
            assert clipboard()==match['latex'].replace('\r\n','\n')
            assert_position(state())
            record('Denied clipboard shows selected original TeX and real keyboard copy works',fallback=after)
        finally:
            native('''(()=>{
                if(globalThis.__copyClipboardDescriptor) Object.defineProperty(navigator,'clipboard',__copyClipboardDescriptor);
                else delete navigator.clipboard;
                delete globalThis.__copyClipboardDescriptor;
            })()''')
        seed_clipboard()
        button=state()['button'];paper.mouse.click(button['x'],button['y'])
        after=wait_status('Copied')
        assert clipboard()==match['latex'].replace('\r\n','\n')
        assert_position(after)
        record('Retry after clipboard rejection succeeds and keeps the manual source available')
    # Restore the parent typography harness's permissions for subsequent UI checks.
    reader.close()
    context.grant_permissions(['clipboard-read','clipboard-write'])
    view.bring_to_front()
