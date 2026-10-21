// Run from the repository with: node tests/control_deck_ui.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '..', 'tools', 'control_deck', 'static', 'index.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];

function page(fetch) {
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
  const context = vm.createContext({document:{getElementById:element,querySelectorAll(selector){return selector==='[data-quick-check]'?quickChecks:[];}},
    localStorage:{getItem(){return null;}},setInterval(){},setTimeout(){},fetch,
    window:{isSecureContext:true},navigator:{clipboard:{async writeText(){throw Error('clipboard unavailable');}}}});
  // Exclude only page startup; exercise the actual UI functions and event handlers.
  const startup = 'notesLoad();load();loadAgents();setInterval(load,30000);setInterval(loadAgents,30000);';
  assert.ok(script.includes(startup), 'Locate page startup before isolating action behavior');
  vm.runInContext(script.replace(startup,''),context);
  vm.runInContext("state={};$('runAll').dataset.actions=JSON.stringify(['git_status','tests','demo']);",context);
  return {context,element,quickChecks};
}

async function pendingAction(kind, closeMode) {
  let requests = 0;
  const {context,element,quickChecks} = page(()=>{requests++; return new Promise(()=>{});});
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

async function failedStatusRefresh(panelKind) {
  const {context,element,quickChecks} = page(async()=>{throw Error('status unavailable');});
  const command = 'example fixed terminal command';
  if(panelKind==='copy')assert.equal(await context.copyText(command),false);
  else {
    context.openPanel('Details','Old snapshot',null);
    if(panelKind==='output') {
      element('poutput').hidden=false;
      element('poutput').textContent='SYNTHETIC ACTION RESULT';
    }
  }
  assert.equal(element('panel').classList.contains('open'),true);
  await context.loadStatus();
  assert.equal(vm.runInContext('state',context),null);
  assert.equal(element('runAll').disabled,true);
  assert.ok(quickChecks.every(button=>button.disabled));
  assert.equal(element('prun').hidden,true);
  assert.equal(element('prun').dataset.action,'');
  assert.equal(element('panel').classList.contains('open'),panelKind!=='idle',`${panelKind}: preserve copy/output but clear stale details`);
  if(panelKind==='copy') {
    assert.equal(element('pcopy').hidden,false);
    assert.equal(element('pcopy').value,command);
    assert.equal(element('ptitle').textContent,'TERM-Befehl');
    assert.ok(element('pbody').textContent.includes('Automatisches Kopieren fehlgeschlagen'));
  } else if(panelKind==='output') {
    assert.equal(element('poutput').hidden,false);
    assert.equal(element('poutput').textContent,'SYNTHETIC ACTION RESULT');
  } else {
    assert.equal(element('ptitle').textContent,'');
    assert.equal(element('pbody').textContent,'');
  }
}

(async()=>{
  for(const kind of ['single','batch'])for(const closeMode of ['button','backdrop'])await pendingAction(kind,closeMode);
  for(const panelKind of ['copy','idle','output'])await failedStatusRefresh(panelKind);
  console.log('PASS: four pending-fetch dismissal cases and three failed-status-refresh panel cases');
})().catch(error=>{console.error(error);process.exitCode=1;});
