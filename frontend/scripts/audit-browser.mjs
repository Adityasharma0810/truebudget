import { chromium } from '@playwright/test';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const results=[];
for(const route of ['/app','/app/tree','/app/metrics','/app/intent','/app/decisions','/app/settings','/app/lab','/app/experiments','/app/ml','/app/devices','/app/conflicts','/app/health','/app/demand','/app/links','/app/agent','/app/about','/welcome','/install','/connect']){
 await page.goto('http://localhost:3000'+route);await page.waitForTimeout(500);
 results.push({route,heading:await page.locator('h1').first().textContent().catch(()=>null),overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)});
}
await page.goto('http://localhost:3000/app');await page.waitForTimeout(800);await page.screenshot({path:'tests/screenshots/overview-desktop.png',fullPage:true});
await page.setViewportSize({width:390,height:844});
for(const route of ['/app','/app/tree','/app/metrics','/app/intent','/app/settings','/app/lab','/app/experiments','/app/agent','/connect']) {await page.goto('http://localhost:3000'+route);await page.waitForTimeout(450);results.push({route,mobile:true,overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)});}
await page.goto('http://localhost:3000/app');await page.waitForTimeout(600);await page.screenshot({path:'tests/screenshots/overview-mobile.png',fullPage:true});
console.log(JSON.stringify({results,errors},null,2));await browser.close();if(errors.length||results.some(r=>r.overflow))process.exitCode=1;
