const {chromium}=require(process.env.HOME+'/projects/cook/node_modules/playwright');
(async()=>{const b=await chromium.launch({executablePath:'/opt/ms-playwright/chromium-1234/chrome-linux64/chrome'});const errs=[];
for(const [w,vw] of [['desk',1440],['phone',430]]){
 const p=await b.newPage({viewport:{width:vw,height:900},colorScheme:'dark'});p.on('pageerror',e=>errs.push(e.message));
 await p.goto('file://'+process.cwd()+'/mockup/instrument-pages.html');await p.waitForTimeout(1200);
 await p.click(`[data-w="${w}"]`);
 for(let i=0;i<8;i++){await p.click(`[data-p="${i}"]`);await p.waitForTimeout(150);
  const txt=await p.$eval('#frame',e=>e.innerText.slice(0,60));if(txt.startsWith('Could not'))errs.push(i+' '+txt);
  const ov=await p.$eval('#frame',e=>{const r=e.getBoundingClientRect();return [...e.querySelectorAll('*')].filter(x=>{const q=x.getBoundingClientRect();return q.right>r.right+1&&!x.closest('.scroll,.presets')}).length});
  if(ov)errs.push(`${w} p${i} overflow ${ov}`);
  await (await p.$('#frame')).screenshot({path:`stills/r01/P${i}-${w}.png`});}
 await p.close();}
console.log(errs);await b.close();})();
