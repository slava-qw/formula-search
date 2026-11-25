"""Attach edited captions, make a compact README GIF, and verify media outputs.

Input: the 32-second HyperFrames render. Outputs stay local in docs/media.
Requires FFmpeg/FFprobe; this does not upload, publish, or modify product code.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def finish(video: Path, ffmpeg: str, checks: Path | None):
    output=ROOT/'docs/media'
    binary=Path(ffmpeg)
    ffprobe=str(binary.with_name('ffprobe'+binary.suffix)) if binary.is_file() else shutil.which('ffprobe')
    if not ffprobe:
        raise FileNotFoundError('FFprobe is required beside FFmpeg or on PATH')
    def probe(path):
        return json.loads(subprocess.check_output([ffprobe,'-v','error','-show_streams',
            '-show_format','-of','json',str(path)],text=True))
    info=probe(video)
    stream=next(s for s in info['streams'] if s['codec_type']=='video')
    assert (stream['width'],stream['height'])==(1920,1080),stream
    assert abs(float(info['format']['duration'])-32)<.1,info['format']
    assert stream['codec_name']=='h264' and stream['pix_fmt']=='yuv420p',stream
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-i',str(video),
        '-i',str(output/'demo.vtt'),'-map','0:v:0','-map','1:0','-c:v','copy',
        '-c:s','mov_text','-metadata:s:s:0','language=eng','-movflags','+faststart',
        '-an',str(output/'demo.mp4')],check=True)
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-ss','4.2',
        '-i',str(output/'demo.mp4'),'-t','12','-filter_complex',
        '[0:v]fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4',
        '-loop','0',str(output/'demo-preview.gif')],check=True)
    final=probe(output/'demo.mp4')
    assert any(s['codec_type']=='subtitle' and s.get('tags',{}).get('language')=='eng' for s in final['streams'])
    capture=json.loads((ROOT/'docs/presentation/assets/capture.json').read_text())
    assert not capture['page_errors'] and not capture['network_errors']
    report={'video':{'width':1920,'height':1080,'fps':stream['r_frame_rate'],
                     'seconds':float(final['format']['duration']),'codec':stream['codec_name'],
                     'optional_english_subtitles':True,'sound':'deliberately silent for README playback'},
            'gif':{'width':960,'fps':8,'source_start':4.2,'seconds':12,'playback_speed':1},
            'product_capture':{'browser':capture['browser'],'checks':capture['checks'],
                               'page_errors':capture['page_errors'],'network_errors':capture['network_errors']},
            'files':[]}
    if checks:
        # json.loads also detects UTF-16 output from Windows PowerShell redirection.
        gate=json.loads(checks.read_bytes())
        assert gate['ok'] and not gate.get('browserSkipped'),gate
        report['hyperframes']={k:{field:gate[k].get(field) for field in ['errorCount','warningCount']}
                               for k in ['lint','runtime','layout','motion','contrast','craft']}
    for name in ['demo.mp4','demo-preview.gif','walkthrough.mp4','overview.png',
                 'workflow.png','architecture.png','demo-poster.png','social-preview.png']:
        data=(output/name).read_bytes()
        report['files'].append({'path':'docs/media/'+name,'bytes':len(data),
                                'sha256':hashlib.sha256(data).hexdigest()})
    (output/'presentation-checks.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--ffmpeg',default='ffmpeg')
    parser.add_argument('--checks',type=Path)
    args=parser.parse_args()
    finish(args.video,args.ffmpeg,args.checks)
