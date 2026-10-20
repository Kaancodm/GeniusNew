// Run from the repository with: node tests/control_deck_ui.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '..', 'tools', 'control_deck', 'static', 'index.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];

async function pendingAction(kind, closeMode) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {dataset:{},hidden:true,disabled:false,textContent:'',value:'',innerHTML:'',
        classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c)}});
    }
    return elements.get(id);
  }
  const quickChecks = [element('quickGit'),element('quickTests'),element('quickDemo')];
  let requests = 0;
  const context = vm.createContext({document:{getElementById:element,querySelectorAll(selector){return selector==='[data-quick-check]'?quickChecks:[];}},
    localStorage:{getItem(){return null;}},setInterval(){},setTimeout(){},
    fetch(){requests++; return new Promise(()=>{});}});
  // Exclude only page startup; exercise the actual UI functions and event handlers.
  const startup = 'notesLoad();load();loadAgents();setInterval(load,30000);setInterval(loadAgents,30000);';
  assert.ok(script.includes(startup), 'Locate page startup before isolating action behavior');
  vm.runInContext(script.replace(startup,''),context);
  vm.runInContext("state={};$('runAll').dataset.actions=JSON.stringify(['git_status','tests','demo']);",context);
  if(kind==='single')vm.runInContext("openPanel('Git','Check','git_status');runAction('git_status');",context);
  else element('runAll').onclick();
  await Promise.resolve();
  assert.equal(requests,1,`${kind}: exactly one initial dispatch`);
  assert.equal(element('panel').classList.contains('open'),true);
  if(closeMode==='button')element('pclose').onclick();
  else element('panel').onclick({target:element('panel')});
  assert.equal(element('panel').classList.contains('open'),false,`${kind}/${closeMode}: pending request must not trap the page`);
  assert.equal(vm.runInContext(kind==='single'?'actionRunning':'runAllRunning',context),true);
  assert.equal(element('runAll').disabled,true);
  assert.ok(quickChecks.every(button=>button.disabled));
  assert.equal(element('poutput').textContent.includes('PASS'),false);
  assert.equal(element('poutput').textContent.includes('FAIL'),false);
  await context.runAction('demo');
  await element('runAll').onclick();
  assert.equal(requests,1,`${kind}/${closeMode}: dismissal must not permit another dispatch`);
}
(async()=>{
  for(const kind of ['single','batch'])for(const closeMode of ['button','backdrop'])await pendingAction(kind,closeMode);
  console.log('PASS: four pending-fetch dismissal cases retain action locks and dispatch exactly once');
})().catch(error=>{console.error(error);process.exitCode=1;});
