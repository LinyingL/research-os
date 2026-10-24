"""Drive the real shared panel resizer through pointer and keyboard events."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
APP = Path(__file__).parents[1] / "src/researchos/webapp/app.html"
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
DRIVER = r"""
const input=JSON.parse(process.argv[1]);
class Target {
  constructor(){this.events={};this.attrs={};this.properties={};this.captured=new Set();this.classes=new Set();
    this.style={setProperty:(k,v)=>{this.properties[k]=v;}};
    this.classList={add:k=>this.classes.add(k),remove:k=>this.classes.delete(k)};}
  addEventListener(k,fn){(this.events[k]||=[]).push(fn);}
  removeEventListener(k,fn){this.events[k]=(this.events[k]||[]).filter(f=>f!==fn);}
  emit(k,more={}){const e=Object.assign({button:0,isPrimary:true,pointerId:1,preventDefault(){},stopPropagation(){}},more);
    [...(this.events[k]||[])].forEach(fn=>fn(e));}
  setAttribute(k,v){this.attrs[k]=v;}
  setPointerCapture(id){this.captured.add(id);}
  hasPointerCapture(id){return this.captured.has(id);}
  releasePointerCapture(id){this.captured.delete(id);this.emit('lostpointercapture',{pointerId:id});}
  focus(){}
}
const handle=new Target(),body=new Target();global.panel=new Target();
const store=input.saved==null?{}:{'researchos.panel-width':String(input.saved)},writes=[];
global.window=Object.assign(new Target(),{innerWidth:input.viewport||1600,localStorage:{
  getItem:k=>{if(input.blocked)throw Error('Storage disabled');return store[k]??null;},
  setItem:(k,v)=>{if(input.blocked)throw Error('Storage disabled');store[k]=v;writes.push(['set',v]);},
  removeItem:k=>{if(input.blocked)throw Error('Storage disabled');delete store[k];writes.push(['remove']);}
}});
global.document={body,getElementById:id=>id==='panel-resizer'?handle:null};
eval(require('fs').readFileSync(0,'utf8')+';global.finishPanel=endPanelResize;');
const width=()=>Number.parseInt(panel.properties['--panel-width']);
const snapshot=()=>({width:width(),saved:store['researchos.panel-width']??null,dragging:body.classes.has('panel-resizing'),captured:handle.captured.size,aria:handle.attrs,writes});
eval(input.scenario);
"""


def run(scenario, **options):
    source = APP.read_text()
    start = source.index("// -- Panel resizing:")
    block = source[start:source.index("// -- End panel resizing.", start)]
    result = subprocess.run([NODE, "-e", DRIVER, json.dumps(dict(options, scenario=scenario))],
                            input=block, text=True, capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


def test_dragging_left_expands_the_panel_and_remembers_release_position():
    out = run("""
handle.emit('pointerdown',{clientX:1040});
window.emit('pointermove',{clientX:640});const during=width();
window.emit('pointerup',{clientX:600});
console.log(JSON.stringify({during,after:snapshot()}));
""")
    assert out["during"] == 960
    assert out["after"]["width"] == 1000 and out["after"]["saved"] == "1000"
    assert not out["after"]["dragging"] and out["after"]["captured"] == 0
    assert out["after"]["aria"]["aria-valuenow"] == "1000"


def test_drag_bounds_and_other_pointer_cannot_move_the_panel():
    out = run("""
handle.emit('pointerdown',{clientX:1040});
window.emit('pointermove',{pointerId:2,clientX:0});const other=width();
window.emit('pointermove',{clientX:3000});const min=width();
window.emit('pointermove',{clientX:-3000});const max=width();
window.emit('pointerup',{clientX:-3000});console.log(JSON.stringify({other,min,max}));
""")
    assert out == {"other": 560, "min": 320, "max": 1576}


def test_saved_width_survives_a_narrow_window_then_returns_when_widened():
    out = run("""
const initial=width();window.innerWidth=480;window.emit('resize');const narrow=width();
window.innerWidth=1600;window.emit('resize');console.log(JSON.stringify({initial,narrow,after:snapshot()}));
""", saved=1000)
    assert (out["initial"], out["narrow"], out["after"]["width"]) == (1000, 456, 1000)
    assert out["after"]["writes"] == []


def test_keyboard_resize_and_double_click_restore_default():
    out = run("""
handle.emit('keydown',{key:'ArrowLeft'});const left=width();
handle.emit('keydown',{key:'ArrowRight',shiftKey:true});const right=width();
handle.emit('keydown',{key:'Home'});const home=width();
handle.emit('keydown',{key:'End'});const end=width();
handle.emit('dblclick');console.log(JSON.stringify({left,right,home,end,after:snapshot()}));
""")
    assert (out["left"], out["right"], out["home"], out["end"]) == (592, 528, 320, 1576)
    assert out["after"]["width"] == 560 and out["after"]["saved"] is None


@pytest.mark.parametrize("event", ["pointercancel", "blur", "lostpointercapture"])
def test_cancel_cleans_up_drag_and_keeps_the_previous_width(event):
    target = "handle" if event == "lostpointercapture" else "window"
    out = run(f"""
handle.emit('pointerdown',{{clientX:700}});window.emit('pointermove',{{clientX:200}});
{target}.emit('{event}');window.emit('pointermove',{{clientX:0}});
console.log(JSON.stringify(snapshot()));
""", saved=900)
    assert out["width"] == 900 and out["saved"] == "900" and not out["dragging"]
    assert out["captured"] == 0 and out["writes"] == []


@pytest.mark.parametrize("saved,blocked", [(None, False), ("bad", False), ("Infinity", False), (-10, False), (900, True)])
def test_bad_or_blocked_storage_does_not_prevent_resizing(saved, blocked):
    out = run("""
const before=width();handle.emit('keydown',{key:'ArrowLeft'});
console.log(JSON.stringify({before,after:snapshot()}));
""", saved=saved, blocked=blocked)
    assert out["before"] == 560 and out["after"]["width"] == 592


def test_narrow_viewport_keeps_the_whole_panel_on_screen():
    out = run("handle.emit('keydown',{key:'End'});console.log(JSON.stringify(snapshot()));", viewport=280)
    assert out["width"] == 256
    assert out["aria"]["aria-valuemin"] == out["aria"]["aria-valuemax"] == "256"


def test_closing_during_a_drag_releases_capture_and_retains_the_width():
    out = run("""
handle.emit('pointerdown',{clientX:1000});window.emit('pointermove',{clientX:500});
finishPanel();window.emit('pointermove',{clientX:0});console.log(JSON.stringify(snapshot()));
""")
    assert out["width"] == 1060 and out["saved"] == "1060"
    assert not out["dragging"] and out["captured"] == 0
