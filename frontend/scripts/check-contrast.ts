import { readFileSync } from 'node:fs';
export function luminance(hex:string) {
  const raw=hex.replace('#','');const color=raw.length===3?raw.split('').map(c=>c+c).join(''):raw;
  const rgb=[0,2,4].map(i=>parseInt(color.slice(i,i+2),16)/255).map(c=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4);
  return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
}
export function contrast(a:string,b:string){const l=[luminance(a),luminance(b)].sort((a,b)=>b-a);return (l[0]+.05)/(l[1]+.05);}
const pairs=[['Primary text','#202b3d','#ffffff'],['Muted text','#60718c','#ffffff'],['Muted on inset','#60718c','#f1f5fb'],['Primary button','#ffffff','#3e6bde'],['Accent link','#345dcc','#ffffff'],['QoE excellent','#19734f','#e8f7f0'],['QoE degraded','#8a5a00','#fff3da'],['QoE poor','#a32a1e','#fdeae6'],['Emulated badge','#916423','#fff8ed'],['Experimental','#7253ad','#f1edff'],['Matrix text','#142d58','#a0b9ef']];
let failed=false;
for(const [name,fg,bg] of pairs){const ratio=contrast(fg,bg);console.log(`${name}: ${ratio.toFixed(2)}:1`);if(ratio<4.5)failed=true;}
const css=readFileSync('src/index.css','utf8');
const foregrounds=[...new Set([...css.matchAll(/(?<![-\w])color:\s*(#[\da-f]{6}|#[\da-f]{3})(?![\da-f])/gi)].map(m=>m[1]))];
for(const fg of foregrounds){if(luminance(fg)>.95)continue;if(contrast(fg,'#ffffff')<4.5){console.error(`CSS foreground ${fg} fails on white`);failed=true;}}
console.log(`Audited ${pairs.length} semantic pairs and ${foregrounds.length} concrete CSS foregrounds. SVG decorative strokes excluded.`);
if(failed)process.exitCode=1;
