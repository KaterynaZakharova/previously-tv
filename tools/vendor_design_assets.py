"""Bundle the requested Feather family and Manrope locally; development only."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '433b7469020cba7a64d007c54b14b51e98a3bd25'
NAMES = ['Play', 'Pause', 'SkipBack', 'SkipForward', 'ArrowLeft', 'ArrowRight',
         'ArrowUp', 'ArrowDown', 'Info', 'GitMerge', 'MoreHorizontal', 'X',
         'RotateCcw', 'Check', 'HelpCircle', 'ChevronDown', 'Home', 'Tv',
         'Film', 'User', 'ExternalLink', 'List', 'RefreshCw', 'AlertCircle']


def fetch(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=25) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(.5)


def main():
    vendor = ROOT / 'frontend/vendor'
    native = ROOT / 'design/react-native'
    vendor.mkdir(parents=True, exist_ok=True)
    sources = native / 'upstream'
    sources.mkdir(parents=True, exist_ok=True)
    base = 'https://raw.githubusercontent.com/yigithanyucedag/react-native-feather/' + COMMIT

    def load(name):
        raw = fetch(base + '/src/icons/' + name + '.ts')
        (sources / (name + '.ts')).write_bytes(raw)
        parts = []
        for tag, attrs in re.findall(r"\['(\w+)', \{ (.*?) \}\]", raw.decode()):
            pairs = re.findall(r'(\w+): ("[^"]*"|[\d.-]+)', attrs)
            attrs = ' '.join(k + '=' + (v if v.startswith('"') else '"' + v + '"')
                             for k, v in pairs if k != 'key')
            parts.append('<' + tag + ' ' + attrs + '/>')
        assert parts, name
        return re.sub(r'(?<!^)(?=[A-Z])', '-', name).lower(), ''.join(parts)

    with ThreadPoolExecutor(max_workers=10) as pool:
        glyphs = dict(pool.map(load, NAMES))
    license_bytes = fetch(base + '/LICENSE')
    (vendor / 'react-native-feather-LICENSE.txt').write_bytes(license_bytes)
    (native / 'LICENSE.txt').write_bytes(license_bytes)
    js = '''/* Glyphs generated from react-native-feather. MIT; see vendor licence. */
(()=>{'use strict';
const glyphs=GLYPHS;
function svg(name){if(!Object.prototype.hasOwnProperty.call(glyphs,name))throw Error('Unknown Feather icon: '+name);return '<svg class="feather" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'+glyphs[name]+'</svg>';}
function set(element,name,label){if(!element)return;element.innerHTML=svg(name)+(element.dataset?.controlLabel?'<span class="control-label">'+(name==='play'?'Play':'Pause')+'</span>':'');if(label)element.setAttribute('aria-label',label);}
function hydrate(root=document){root.querySelectorAll('[data-icon]').forEach(e=>set(e,e.dataset.icon));}
window.TVIcons=Object.freeze({svg,set,hydrate});hydrate();
})();
'''.replace('GLYPHS', json.dumps(glyphs, separators=(',', ':')))
    (ROOT / 'frontend/icons.js').write_text(js)
    font_base = 'https://raw.githubusercontent.com/google/fonts/main/ofl/manrope/'
    ttf = fetch(font_base + 'Manrope%5Bwght%5D.ttf')
    (vendor / 'Manrope-Variable.ttf').write_bytes(ttf)
    (vendor / 'Manrope-OFL.txt').write_bytes(fetch(font_base + 'OFL.txt'))
    (vendor / 'provenance.json').write_text(json.dumps({
        'react_native_feather': {'repository': 'https://github.com/yigithanyucedag/react-native-feather',
                                 'commit': COMMIT, 'icons': NAMES},
        'font': {'family': 'Manrope', 'license': 'SIL OFL 1.1',
                 'url': font_base + 'Manrope%5Bwght%5D.ttf',
                 'sha256': hashlib.sha256(ttf).hexdigest()}}, indent=2))
    print('Bundled', len(glyphs), 'Feather icons and Manrope variable font.')


if __name__ == '__main__':
    main()
