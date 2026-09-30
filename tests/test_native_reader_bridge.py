"""Regression checks execute the native reader's shipped JavaScript bridge."""
import json
import pathlib
import shutil
import subprocess
import unittest


class NativeReaderBridgeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js needed to execute reader bridge')
    def test_outline_language_search_and_missing_restore(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / 'macos-app/Sources/KPaperMac/ReaderWebPreview.swift').read_text()
        bridge = source.split('static let bridge = #"""', 1)[1].split('"""#', 1)[0]
        harness = r'''
const assert = require('assert');
const reports=[], jobs=[], clicks=[];
const heading={id:'section-one',tagName:'H4',textContent:'1. 세부 절',closest:()=>null,getBoundingClientRect:()=>({top:60})};
const anchor={id:'paragraph-one',closest:()=>null,getClientRects:()=>[{}],getBoundingClientRect:()=>({top:60})};
const root={closest:()=>null,innerText:'Alpha alpha beta',querySelectorAll:q=>q==='h2,h3,h4,h5,h6'?[heading]:[anchor],querySelector:()=>null};
global.CSS={escape:x=>x};
global.getComputedStyle=()=>({overflowY:'auto'});
global.window={webkit:{messageHandlers:{reader:{postMessage:x=>reports.push(x)}}},addEventListener:()=>{}};
global.document={head:{append:()=>{}},createElement:()=>({}),scrollingElement:{scrollTop:0},addEventListener:()=>{},querySelectorAll:()=>[],querySelector:q=>q.includes('codex_tab_button')?{click:()=>clicks.push(q)}:root};
global.setTimeout=f=>{jobs.push(f);return jobs.length}; global.clearTimeout=()=>{};
function flush(){while(jobs.length)jobs.shift()();}
BRIDGE
window.kpaperReader.mode(1); flush();
assert(clicks[0].includes('codex-panel-parallel'));
assert.strictEqual(reports.at(-1).headings.length,1);
assert.strictEqual(reports.at(-1).headings[0].level,4);
assert.strictEqual(reports.at(-1).current,'section-one');
assert.strictEqual(window.kpaperReader.count('ALPHA'),2);
assert.strictEqual(window.kpaperReader.count(''),0);
reports.length=0;
window.kpaperReader.restore('missing-anchor',30); flush();
assert(reports.every(x=>!('anchor' in x)), 'Failed restore must not overwrite saved position');
'''.replace('BRIDGE', bridge)
        completed = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
