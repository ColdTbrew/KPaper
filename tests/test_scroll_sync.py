from __future__ import annotations
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from translate_html_blocks import BILINGUAL_SCRIPT


class ScrollSyncTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required to execute viewer JavaScript')
    def test_real_viewer_script_maps_scroll_without_jumps_or_feedback(self):
        script = BILINGUAL_SCRIPT.split('>', 1)[1].rsplit('</script>', 1)[0]
        script = script.replace('  activate("codex-panel-ko", false);', '''
        globalThis.syncTest = {syncFrom, setSyncEnabled, mappedScrollTop, captureScrollPositions, scheduleParallelAlignment,
          setMap: (value) => {scrollMap = value;}};
        ''')
        harness = r'''
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const frames = new Map(); let frameId = 0;
function column(total) {
  const listeners = {};
  return {scrollTop:0, scrollHeight:total, clientHeight:100,
    querySelector: () => ({querySelectorAll: () => []}),
    querySelectorAll: () => [],
    addEventListener: (name, fn) => {listeners[name] = fn;},
    fire: name => listeners[name]?.(),
    getBoundingClientRect: () => ({top:0})};
}
const columns = [column(1100), column(2100)];
const button = {classList:{toggle(){}}, setAttribute(){}, addEventListener(){}};
const panel = {id:'codex-panel-parallel', hidden:false};
const context = {console, document:{
  addEventListener: (_name, callback) => callback(),
  querySelectorAll: selector => selector === '.codex_parallel_column' ? columns : selector === '.codex_panel' ? [panel] : [],
  querySelector: selector => selector === '.codex_sync_button' ? button : null,
  getElementById: () => panel,
}, window:{
  addEventListener(){}, setTimeout:fn=>{fn(); return 1;}, clearTimeout(){},
  requestAnimationFrame: fn=>{frames.set(++frameId,fn); return frameId;},
  cancelAnimationFrame: id=>frames.delete(id),
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(0,'utf8'), context);
const api=context.syncTest;
// Unequal block lengths: preserve progress within corresponding blocks.
api.setMap([[0,0],[100,200],[400,500]]);
assert.equal(api.mappedScrollTop(columns[0],50),100);
assert.equal(api.mappedScrollTop(columns[0],250),350);
assert.equal(api.mappedScrollTop(columns[1],350),250);
// Toggle on keeps both independently positioned views exactly where they are.
api.setSyncEnabled(false);
columns[0].scrollTop=100; columns[1].scrollTop=700;
api.setSyncEnabled(true);
assert.equal(columns[0].scrollTop,100); assert.equal(columns[1].scrollTop,700);
columns[0].scrollTop=150; api.syncFrom(columns[0]);
assert.equal(columns[1].scrollTop,800);
// Programmatic target scroll must not generate another synchronization frame.
columns[1].fire('scroll'); assert.equal(frames.size,0);
// Coalesce fast user scroll events into one frame, retaining the full distance.
columns[0].scrollTop=175; columns[0].fire('scroll');
columns[0].scrollTop=200; columns[0].fire('scroll');
assert.equal(frames.size,1);
for (const fn of frames.values()) fn(); frames.clear();
assert.equal(columns[1].scrollTop,900);
// Disabled sync leaves the other pane unchanged and cancels queued movement.
columns[0].scrollTop=220; columns[0].fire('scroll');
api.setSyncEnabled(false); assert.equal(frames.size,0);
columns[0].scrollTop=300; columns[0].fire('scroll');
assert.equal(columns[1].scrollTop,900);
// Scrolling either pane works, including clamping at document boundaries.
api.setSyncEnabled(true);
columns[1].scrollTop=1000; api.syncFrom(columns[1]);
assert.equal(columns[0].scrollTop,350);
columns[0].scrollTop=1000; api.syncFrom(columns[0]);
assert.equal(columns[1].scrollTop,2000);
// An atomic heading jump cancels queued user deltas and absorbs both native
// programmatic scroll events before establishing the next user-scroll baseline.
columns[0].scrollTop=900; columns[0].fire('scroll');
assert.equal(frames.size,1);
context.window.kpaperViewer.performNavigation(() => {
  columns[0].scrollTop=200; columns[0].fire('scroll');
  columns[1].scrollTop=500; columns[1].fire('scroll');
  assert.equal(frames.size,0);
});
assert.equal(columns[0].scrollTop,200); assert.equal(columns[1].scrollTop,500);
columns[0].scrollTop=250; api.syncFrom(columns[0]);
assert.equal(columns[1].scrollTop,600);
// Keep a semantic snapshot from BEFORE resize reflows text. Cancel the queued
// user-scroll frame immediately, and preserve independently positioned panes.
const timers = new Map(); let timerId=0;
context.window.setTimeout=fn=>{timers.set(++timerId,fn);return timerId;};
context.window.clearTimeout=id=>timers.delete(id);
columns.forEach(col => {
  col.contentY=800;
  const anchor={id:'semantic', children:[], matches:()=>false, closest:()=>null,
    getClientRects:()=>[{}], getBoundingClientRect:()=>({top:col.contentY-col.scrollTop,bottom:col.contentY-col.scrollTop+30,height:30})};
  const article={querySelectorAll:()=>[anchor]};
  col.querySelector=q=>q.startsWith('#')?anchor:article;
});
columns[0].scrollTop=200; columns[1].scrollTop=500;
api.captureScrollPositions();
columns[0].scrollTop=225; columns[0].fire('scroll');
assert.equal(frames.size,1);
columns.forEach(col=>{col.contentY=1100;}); // layout has already changed
api.scheduleParallelAlignment(true);
assert.equal(frames.size,0);
columns[1].fire('scroll'); assert.equal(frames.size,0);
while(timers.size){const pending=[...timers.values()];timers.clear();pending.forEach(fn=>fn());}
assert.equal(columns[0].scrollTop,500);
assert.equal(columns[1].scrollTop,800);
// Disabled sync still caches each independent reading position for resize.
api.setSyncEnabled(false);
columns[0].scrollTop=600; columns[0].fire('scroll');
columns[1].scrollTop=900; columns[1].fire('scroll');
columns.forEach(col=>{col.contentY=1200;});
api.scheduleParallelAlignment(true);
while(timers.size){const pending=[...timers.values()];timers.clear();pending.forEach(fn=>fn());}
assert.equal(columns[0].scrollTop,700);
assert.equal(columns[1].scrollTop,1000);
api.setSyncEnabled(true);
assert.equal(columns[0].scrollTop,700); assert.equal(columns[1].scrollTop,1000);

console.log('viewer synchronization behavior passed');
'''
        result = subprocess.run([shutil.which('node'), '-e', harness], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
