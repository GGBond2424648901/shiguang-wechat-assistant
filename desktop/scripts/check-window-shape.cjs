const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { test } = require('node:test');
const { prepareTransparentWindow, isWindowFullscreen } = require('../electron/window-shape.cjs');
class Window extends EventEmitter {
  destroyed=false; fullScreen=false; paints=[];
  isDestroyed(){return this.destroyed;}
  isFullScreen(){return this.fullScreen;}
  setShape(){throw new Error('A binary region removes the antialiased edge');}
  setBackgroundColor(color){this.paints.push(['window',color]);}
  contentView={setBackgroundColor:color=>this.paints.push(['root',color]),children:[{setBackgroundColor:color=>this.paints.push(['content',color])}]};
  hookWindowMessage(message,callback){this.dpi=callback;}
}
test('all native backing layers stay transparent without a binary window region',()=>{
 const w=new Window();const ready=prepareTransparentWindow(w);ready();
 assert.deepEqual(w.paints,[['window','#00000000'],['root','#00000000'],['content','#00000000']]);
 w.emit('blur');w.emit('focus');w.emit('resize');
 assert.equal(w.paints.length,3,'ordinary input should not rebuild the compositor');
});
test('full screen tracking survives bounds-based Windows full screen',()=>{
 const w=new Window();prepareTransparentWindow(w);
 w.emit('enter-full-screen');assert.equal(isWindowFullscreen(w),true);
 w.emit('leave-full-screen');assert.equal(isWindowFullscreen(w),false);
});
test('DPI reset refreshes transparency and a closed window is never touched',async()=>{
 const w=new Window();const ready=prepareTransparentWindow(w);ready();w.dpi();await new Promise(setImmediate);
 assert.equal(w.paints.length,6);w.destroyed=true;ready();assert.equal(w.paints.length,6);
});
