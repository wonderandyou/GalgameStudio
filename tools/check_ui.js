// 静态自检：JS 里引用的 id / class 是否都真实存在
const fs = require('fs');
const path = require('path');

const dir = 'D:/软件/GalgameStudio/ui';
const html = fs.readFileSync(path.join(dir, 'index.html'), 'utf8');
const js = fs.readFileSync(path.join(dir, 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(dir, 'app.css'), 'utf8');

const htmlIds = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
const jsIds = new Set([...js.matchAll(/\$\('([^']+)'\)/g)].map(m => m[1]));
const missingIds = [...jsIds].filter(i => !htmlIds.has(i));

const jsClasses = new Set();
for (const m of js.matchAll(/querySelector(?:All)?\('([^']+)'\)/g)) {
  m[1].split(/[\s,>]+/).forEach(sel => {
    if (sel.startsWith('.')) jsClasses.add(sel.slice(1).split(':')[0]);
  });
}
const cssClasses = new Set([...css.matchAll(/\.([a-zA-Z][\w-]*)/g)].map(m => m[1]));
const htmlClasses = new Set();
for (const m of html.matchAll(/\bclass="([^"]+)"/g)) m[1].split(/\s+/).forEach(c => htmlClasses.add(c));

const missingClasses = [...jsClasses].filter(c => !cssClasses.has(c) && !htmlClasses.has(c));

console.log('HTML ids:', htmlIds.size, '| JS 引用:', jsIds.size);
console.log('缺失 id:', missingIds.length ? missingIds.join(', ') : '无');
console.log('JS 选择器类:', jsClasses.size, '| CSS 类:', cssClasses.size);
console.log('缺失 class:', missingClasses.length ? missingClasses.join(', ') : '无');

// 结构检查：标签配对（粗检）
const open = (html.match(/<div\b/g) || []).length;
const close = (html.match(/<\/div>/g) || []).length;
const openS = (html.match(/<section\b/g) || []).length;
const closeS = (html.match(/<\/section>/g) || []).length;
console.log('div 开/闭:', open, '/', close, open === close ? 'OK' : '不匹配');
console.log('section 开/闭:', openS, '/', closeS, openS === closeS ? 'OK' : '不匹配');

// 关键函数存在性
['function render', 'function drawWires', 'function openPlayer', 'function fitView', 'function showNode'].forEach(f => {
  console.log(f, js.includes(f) ? 'OK' : 'MISSING');
});
