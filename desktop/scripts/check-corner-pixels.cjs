const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {_electron}=require('../node_modules/playwright');const {fixture}=require('./check-desktop.cjs');
const desktop=path.resolve(__dirname,'..'),root=path.dirname(desktop);
async function main(){
 const out=path.resolve(process.argv[2]||path.join(root,'build/ui-audit/corners'));await fs.mkdir(out,{recursive:true});
 const result=[];
 for(const scale of [1,1.25,1.5,2]){
  const dest=path.join(out,String(scale));await fs.mkdir(dest,{recursive:true});const test=fixture(dest,true);await test.ready;
  const profile=path.join(dest,'profile');
  if(process.env.SHIGUANG_QA_WALLPAPER)await fs.copyFile(process.env.SHIGUANG_QA_WALLPAPER,path.join(profile,'runtime/ui-background.png'));
  const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
  let app;try{
   app=await _electron.launch({args:[desktop,'--profile='+profile,'--writable','--force-device-scale-factor='+scale],env,timeout:45000});
   const page=await app.firstWindow();await page.getByText('这周有约拍吗？',{exact:true}).waitFor();
   async function capture(p,name){
    await p.waitForFunction(() => document.fonts.status === 'loaded'); await p.waitForTimeout(250); const hash=await p.evaluate(()=>location.hash); const encoded=await app.evaluate(async({BrowserWindow},hash)=>{const w=BrowserWindow.getAllWindows().find(w=>new URL(w.webContents.getURL()).hash===hash);return (await w.webContents.capturePage()).toPNG().toString('base64')},hash);const png=Buffer.from(encoded,'base64');await fs.writeFile(path.join(dest,name+'.png'),png);
    const pixels=await app.evaluate(({nativeImage},base64)=>{
     const im=nativeImage.createFromBuffer(Buffer.from(base64,'base64')),s=im.getSize(),b=im.toBitmap();
     const gold=[136,184,217]; // nativeImage.toBitmap is BGRA
     const goldEdges=[0,1,2,3].map(edge=>[.25,.5,.75].every(f=>[0,1,2].some(d=>{
      const x=edge<2?Math.floor(s.width*f):edge===2?d:s.width-1-d;
      const y=edge>=2?Math.floor(s.height*f):edge===0?d:s.height-1-d;
      const offset=(y*s.width+x)*4;
      return gold.every((v,i)=>Math.abs(b[offset+i]-v)<42);
     })));
     return {size:s,goldEdges,corners:[[0,0],[s.width-40,0],[0,s.height-40],[s.width-40,s.height-40]].map(([x,y])=>{
      const a=[];for(let yy=y;yy<y+40;yy++)for(let xx=x;xx<x+40;xx++)a.push(b[(yy*s.width+xx)*4+3]);
      return {transparent:a.filter(v=>v===0).length,solid:a.filter(v=>v===255).length,blended:[...new Set(a.filter(v=>v>0&&v<255))].length};
     })};
    },png.toString('base64'));
    for(const c of pixels.corners){assert.ok(c.transparent>20,name+' opaque corner');assert.ok(c.solid>100,name+' missing content');assert.ok(c.blended>=10,name+' jagged corner');}
    assert.ok(pixels.goldEdges.every(Boolean),name+' incomplete gold frame at scale '+scale);
    result.push({scale,name,...pixels});
   }
   await app.evaluate(({BrowserWindow})=>{const w=BrowserWindow.getAllWindows()[0];w.unmaximize();w.setBounds({x:30,y:30,width:1000,height:600});});await page.waitForTimeout(250);await capture(page,'workbench');
   const [popup]=await Promise.all([app.waitForEvent('window'),page.getByRole('button',{name:'关闭助手',exact:true}).click()]);
   await popup.getByRole('button',{name:'取消关闭'}).waitFor();
   await capture(popup,'close');await capture(page,'blurred-workbench');
   await popup.getByRole('button',{name:'取消关闭'}).click();
   await page.setViewportSize({width:1100,height:740});await capture(page,'resized');
   await page.getByRole('button',{name:'会话',exact:true}).click();
   const [calibration]=await Promise.all([app.waitForEvent('window'),page.getByRole('button',{name:'重新校准',exact:true}).click()]);
   await calibration.getByRole('button',{name:'唤起并校准'}).click();
   await calibration.getByAltText('当前微信窗口校准截图').waitFor();
   await capture(calibration,'calibration');
  }finally{if(app)await app.close();test.child.stdin.end();}
 }
 await fs.writeFile(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({images:result.length,allFourCorners:true,completeGoldFrames:true,scales:[1,1.25,1.5,2],pass:true}));
}
main().catch(e=>{console.error(e);process.exitCode=1});
