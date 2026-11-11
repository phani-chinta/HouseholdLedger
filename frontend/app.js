const root = document.querySelector('#app');
const dialog = document.querySelector('#dialog');
const toast = document.querySelector('#toast');
const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 });
const monthName = new Intl.DateTimeFormat('en-IN', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const monthShort = new Intl.DateTimeFormat('en-IN', { month: 'short', timeZone: 'UTC' });
const state = { session: null, profile: '', month: '2026-11', page: 1, q: '', kind: '', catalog: 'categories' };
let viewVersion = 0;
let toastTimer;
const names = { overview:'Overview', transactions:'Transactions', budgets:'Budgets', recurring:'Recurring payments',
  exceptions:'Exceptions', insights:'Budget insights', audit:'Audit trail', rules:'Category rules', evaluation:'Evaluation', catalog:'Reference data' };
const icons = { overview:'◫', transactions:'↔', budgets:'◈', recurring:'⟳', exceptions:'◇', insights:'↗',
  audit:'≡', rules:'⚙', evaluation:'◎', catalog:'▤', income:'↓', expense:'↑', balance:'≋',
  add:'+', close:'×', menu:'☰', signout:'↪', calendar:'▦', arrow:'→', wallet:'▣' };
const icon = name => '<span class="icon" aria-hidden="true">'+(icons[name] || '•')+'</span>';
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const money = cents => currency.format((cents || 0)/100);
const pretty = value => String(value || '—').replaceAll('_',' ').replace(/^./, char => char.toUpperCase());
const dateLabel = value => value ? new Date(value.slice(0,10)+'T00:00:00Z').toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}) : '—';
const periodLabel = value => monthName.format(new Date(value+'-01T00:00:00Z'));
const button = (label, action, options={}) => '<button type="button" class="'+(options.className || 'button')+'" data-action="'+action+'"'+
  (options.id != null ? ' data-id="'+esc(options.id)+'"' : '')+(options.disabled ? ' disabled' : '')+'>'+ (options.icon ? icon(options.icon) : '')+esc(label)+'</button>';
const pill = (value, variant='') => '<span class="pill '+variant+'">'+esc(pretty(value))+'</span>';
const empty = text => '<div class="empty"><span class="empty-icon">'+icon('overview')+'</span><p>'+esc(text)+'</p></div>';
const options = (items, current) => items.map(([value,label]) => '<option value="'+esc(value)+'"'+(String(current)===String(value)?' selected':'')+'>'+esc(label)+'</option>').join('');
const writable = () => state.session.role !== 'reviewer';
const staff = () => state.session.role !== 'customer';
const admin = () => state.session.role === 'administrator';

function themeControl() {
  const theme = document.documentElement.dataset.theme || 'light';
  return '<label class="theme-control"><span>Theme</span><select data-theme-select aria-label="Color theme">'+
    options([['light','Light'],['dark','Dark']],theme)+'</select></label>';
}

function skeleton(current) {
  return '<div class="skeleton" role="status" aria-label="Loading '+esc(names[current])+'">'+
    '<div class="skeleton-title"></div><div class="skeleton-grid">'+
    '<div></div><div></div><div></div><div></div></div><div class="skeleton-panel"></div></div>';
}

async function api(path, method='GET', data) {
  const headers = {Accept:'application/json'};
  if (method !== 'GET') {
    headers['X-CSRFToken'] = state.session?.csrf || '';
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch('/api/'+path, {method, headers, body:data === undefined ? undefined : JSON.stringify(data)});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (path==='register/' && response.status===404) {
      throw Error('Sign-up is unavailable on the running API. Stop both servers and start run_local.py from the updated HouseholdLedger folder.');
    }
    const details = result.fields ? Object.values(result.fields).flat().join(' ') : '';
    throw Error([result.error || result.detail || 'Request failed ('+response.status+').', details].filter(Boolean).join(' '));
  }
  return result;
}

function path(name, extra={}) {
  const params = new URLSearchParams({month:state.month, ...extra});
  if (state.profile) params.set('profile',state.profile);
  return name+'/?'+params;
}

function announce(message, error=false) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.className = 'show'+(error?' error':'');
  toastTimer = setTimeout(() => toast.className = '', 3600);
}

const demoRoles = ['customer', 'analyst', 'reviewer', 'administrator'];
const passwordRequirements = 'Use at least 10 characters. Avoid common passwords and personal details.';

function passwordField(label, name, autocomplete, fresh=false) {
  return '<div class="field"><label for="auth-'+name+'">'+label+'</label><div class="password-input">'+
    '<input id="auth-'+name+'" name="'+name+'" type="password" autocomplete="'+autocomplete+'" required '+
    (fresh?'minlength="10" maxlength="128"':'maxlength="1024"')+'>'+
    '<button type="button" class="link-button" data-action="show-password" data-id="auth-'+name+'" aria-label="Show '+label.toLowerCase()+'">Show</button></div></div>';
}

function loginScreen(mode='login', focus=false) {
  const register = mode==='register';
  const title = register?'Create your account':'Sign in';
  const form = '<form id="auth-form" data-mode="'+mode+'">'+
    (register?inputField('Full name','name','','text','autocomplete="name" maxlength="100" required'):'')+
    inputField('Username','username','','text',register?
      'autocomplete="username" autocapitalize="none" spellcheck="false" minlength="3" maxlength="30" pattern="[A-Za-z0-9][A-Za-z0-9._\\-]{2,29}" required':
      'autocomplete="username" autocapitalize="none" spellcheck="false" maxlength="150" required')+
    (register?'<p class="auth-hint">Use 3 to 30 letters, numbers, periods, underscores or hyphens. Start with a letter or number.</p>':'')+
    passwordField('Password','password',register?'new-password':'current-password',register)+
    (register?'<p class="auth-hint">'+passwordRequirements+'</p>'+passwordField('Confirm password','confirm_password','new-password',true):'')+
    '<p class="form-error" role="alert"></p><button class="button primary" type="submit">'+title+' '+icon('arrow')+'</button></form>';
  root.innerHTML = '<main class="login"><section class="login-art"><div class="brand"><span class="brand-mark">H</span> Household Ledger</div>'+
    '<h1>Know where your money goes.</h1><p>Track household transactions, recurring payments, and monthly budgets in one place.</p></section>'+
    '<section class="login-side" aria-labelledby="auth-title"><div class="login-box"><div class="login-heading"><h2 id="auth-title" tabindex="-1">'+title+'</h2>'+themeControl()+'</div>'+
    '<p class="muted">'+(register?'Set up your household and start tracking your spending.':'Open your household workspace.')+'</p>'+form+
    '<p class="auth-switch">'+(register?'Already have an account? ':'New to Household Ledger? ')+
    button(register?'Sign in':'Create an account','auth-mode',{id:register?'login':'register',className:'link-button'})+'</p>'+
    (register?'':'<details class="demo-access"><summary>Explore demo accounts</summary><div class="demo-buttons">'+
      demoRoles.map(role=>button(pretty(role),'demo',{id:role})).join('')+'</div></details>')+
    '</div></section></main>';
  if (focus) document.querySelector('#auth-title').focus();
}

function initialMonth(session) {
  if (session.demo) return '2026-11';
  const now = new Date();
  return now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0');
}

async function submitAuth(mode, fields) {
  const form = document.querySelector('#auth-form');
  const controls = root.querySelectorAll('button');
  const submit = form.querySelector('[type=submit]');
  const original = submit.innerHTML;
  const alert = form.querySelector('.form-error');
  const register = mode==='register';
  controls.forEach(control=>{ control.disabled = true; });
  submit.textContent = register?'Creating your account…':'Signing in…';
  alert.textContent = '';
  try {
    if (register) {
      if (fields.password !== fields.confirm_password) throw Error('The passwords do not match.');
      fields.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Kolkata';
    }
    state.session = await api(register?'register/':'login/','POST',fields);
    state.profile = '';
    state.month = initialMonth(state.session);
    await navigate('overview');
    if (register) announce('Your account is ready.');
  } catch (error) {
    alert.textContent = error.message;
    submit.innerHTML = original;
    controls.forEach(control=>{ control.disabled = false; });
  }
}

function section() {
  const key = location.hash.slice(1);
  if (!names[key] || (!staff() && ['rules','evaluation'].includes(key)) || (!admin() && key === 'catalog')) return 'overview';
  return key;
}

async function navigate(key) {
  state.page = 1;
  state.q = '';
  state.kind = '';
  if (location.hash !== '#'+key) history.replaceState(null,'','#'+key);
  await render();
}

function shell(current) {
  const nav = keys => keys.map(key => '<button type="button" class="nav-item '+(current===key?'active':'')+'" data-action="nav" data-id="'+key+'" '+
    (current===key?'aria-current="page"':'')+'>'+icon(key)+esc(names[key])+'</button>').join('');
  const who = state.session.name;
  const reference = state.reference;
  root.innerHTML = '<div class="app-layout"><aside class="sidebar" id="sidebar"><a href="#overview" class="brand"><span class="brand-mark">H</span>Household Ledger</a>'+
    '<p class="side-label">Workspace</p><nav class="nav-list" aria-label="Workspace">'+nav(['overview','transactions','budgets','recurring'])+'</nav>'+
    '<p class="side-label">Review</p><nav class="nav-list" aria-label="Review">'+nav(['exceptions','insights','audit',...(staff()?['rules','evaluation']:[]),...(admin()?['catalog']:[])])+'</nav>'+
    '<div class="sidebar-footer"><span class="avatar">'+esc(who.slice(0,1).toUpperCase())+'</span><div><strong>'+esc(who)+'</strong><div class="muted text-sm">'+esc(pretty(state.session.role))+'</div></div>'+
    (state.session.demo?'':'<button type="button" class="icon-button account-settings" data-action="account-settings" aria-label="Account settings" title="Account settings">'+icon('rules')+'</button>')+
    '<button type="button" class="icon-button" data-action="logout" aria-label="Sign out" title="Sign out">'+icon('signout')+'</button></div></aside>'+
    '<button type="button" class="sidebar-scrim" data-action="menu" aria-label="Close navigation" tabindex="-1"></button>'+
    '<div class="min-w-0"><header class="topbar"><div class="crumb"><button type="button" class="icon-button mobile-menu" data-action="menu" aria-label="Toggle navigation" aria-expanded="false" aria-controls="sidebar">'+icon('menu')+'</button><span>Workspace</span><span>/</span><strong>'+esc(names[current])+'</strong></div>'+
    '<div class="top-controls">'+themeControl()+(staff() && reference ? '<select id="profile" aria-label="Household">'+options(reference.profiles.map(p=>[p.id,p.name]),reference.profile.id)+'</select>':'')+
    '<input type="month" class="period" id="period" aria-label="Reporting month" min="2000-01" max="2099-12" value="'+esc(state.month)+'"></div></header>'+
    '<main class="content" id="content">'+skeleton(current)+'</main></div></div>';
}

function intro(key, description, actions='') {
  return '<div class="intro"><div><div class="eyebrow">'+esc(state.reference.profile.name)+'’s household</div><h1>'+esc(names[key])+'</h1><p>'+esc(description)+'</p></div>'+actions+'</div>';
}

function table(headers, rows, noRows='Nothing to show for this selection.') {
  if (!rows.length) return empty(noRows);
  return '<div class="table-wrap"><table><thead><tr>'+headers.map(name=>'<th scope="col"'+(name.startsWith('~')?' class="num"':'')+'>'+esc(name.replace(/^~/,''))+'</th>').join('')+
    '</tr></thead><tbody>'+rows.map(cells=>'<tr>'+cells.map((cell,index)=>'<td'+(headers[index].startsWith('~')?' class="num"':'')+'>'+cell+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
}

function pager(data) {
  if (data.pages <= 1) return '';
  return '<div class="pager">'+button('Previous','page',{id:data.page-1,disabled:data.page<=1})+
    '<span>Page '+data.page+' of '+data.pages+'</span>'+button('Next','page',{id:data.page+1,disabled:data.page>=data.pages})+'</div>';
}

function merchantCell(row) {
  return '<div class="merchant-cell"><span class="merchant-icon">'+icon(row.kind==='transfer'?'transactions':'wallet')+'</span><div><strong>'+
    esc(row.merchant || row.descriptor)+'</strong><div class="muted">'+esc(row.account)+'</div></div></div>';
}

function amount(cents) {
  return '<strong class="'+(cents>=0?'positive':'')+'">'+(cents>0?'+':'')+money(cents)+'</strong>';
}

function category(row) {
  return row.category ? pill(row.category, row.kind==='transfer'?'neutral':'') : pill('Needs review','warn');
}

function budgetItem(row, showCategory=true) {
  const usage = row.limit_cents > 0 ? row.spent_cents*100/row.limit_cents : 0;
  const progress = Math.max(0,Math.min(100,usage));
  return '<div class="budget-item"><div class="budget-meta">'+(showCategory?'<strong>'+esc(row.category)+'</strong>':'<strong>Monthly limit</strong>')+
    '<span class="muted">'+(showCategory?money(row.spent_cents)+' / ':'')+money(row.limit_cents)+'</span></div>'+
    '<div class="track" role="progressbar" aria-label="'+esc(row.category)+' budget used" aria-valuemin="0" aria-valuemax="100" aria-valuenow="'+Math.round(progress)+
    '" aria-valuetext="'+Math.round(usage)+'% of limit"><div class="fill '+esc(row.status)+'" style="width:'+progress+'%"></div></div>'+
    '<span class="budget-usage">'+Math.round(usage)+'% used</span></div>';
}

function chart(trend) {
  if (!trend.length) return empty('No cash-flow history for this period.');
  const values = trend.flatMap(point=>[point.income_cents,point.spending_cents]);
  const minimum = Math.min(0,...values);
  const maximum = Math.max(0,...values);
  const rough = Math.max(10000,(maximum-minimum)/4);
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1,2,2.5,5,10].find(size=>size*magnitude >= rough)*magnitude;
  const top = Math.max(0,Math.ceil(maximum/step)*step);
  const bottom = Math.floor(minimum/step)*step;
  const range = top-bottom || step;
  const baseline = 100*(0-bottom)/range;
  const compact = new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',notation:'compact',maximumFractionDigits:1});
  const axis = [1,.75,.5,.25,0].map(part=>'<span>'+esc(compact.format((bottom+range*part)/100))+'</span>').join('');
  const groups = trend.map((point,index)=>{
    const label = monthShort.format(new Date(point.month+'-01T00:00:00Z'));
    const fullLabel = periodLabel(point.month);
    const bar = (value,kind) => {
      const position = 100*(Math.min(0,value)-bottom)/range;
      const height = 100*Math.abs(value)/range;
      return '<span class="chart-bar '+kind+'" style="bottom:'+position+'%;height:'+height+'%"></span>';
    };
    const description = fullLabel+', income '+money(point.income_cents)+', spending '+money(point.spending_cents);
    return '<button type="button" class="bar-group" data-action="chart-month" aria-pressed="false" aria-label="'+esc(description)+'"'+
      ' style="--bar-delay:'+index*35+'ms"><span class="chart-tooltip" aria-hidden="true"><strong>'+esc(fullLabel)+'</strong>'+
      '<span><i class="dot"></i> Income <b>'+money(point.income_cents)+'</b></span>'+
      '<span><i class="dot light"></i> Spending <b>'+money(point.spending_cents)+'</b></span></span>'+
      bar(point.income_cents,'income')+bar(point.spending_cents,'spending')+
      '<span class="bar-label" aria-hidden="true">'+esc(label)+'</span></button>';
  }).join('');
  return '<div class="chart" role="group" aria-label="Monthly income and spending"><div class="axis" aria-hidden="true">'+axis+
    '</div><div class="plot" style="--months:'+trend.length+';--baseline:'+baseline+'%">'+groups+'</div></div>';
}

function panelHead(title, subtitle='', action='') {
  return '<div class="panel-head"><div><h2>'+esc(title)+'</h2>'+(subtitle?'<p>'+esc(subtitle)+'</p>':'')+'</div>'+action+'</div>';
}

function overview(data) {
  const metrics = [
    ['Net cash flow', data.net_cents, 'balance', 'Income after net spending'],
    ['Income', data.income_cents, 'income', 'Salary and other credits'],
    ['Net spending', data.spending_cents, 'expense', 'Expenses after refunds'],
    ['Budgeted', data.budget_cents, 'budgets', data.budgets.length+' active category limits'],
  ];
  const cards = metrics.map(([label,value,symbol,subtitle])=>'<section class="kpi"><div class="kpi-label">'+esc(label)+'<span class="kpi-icon">'+icon(symbol)+'</span></div>'+
    '<p class="kpi-value">'+money(value)+'</p><p class="kpi-sub">'+esc(subtitle)+'</p></section>').join('');
  const focus = [...data.budgets].sort((a,b)=>b.spent_cents/b.limit_cents-a.spent_cents/a.limit_cents).slice(0,4);
  const go = (label,key) => button(label,'nav',{id:key,className:'link-button'});
  const rows = data.recent.slice(0,4).map(row=>[merchantCell(row),esc(dateLabel(row.local_date)),category(row),amount(row.cents)]);
  const setup = !state.session.demo && writable() && !data.transaction_count && !data.budgets.length
    ? '<section class="panel getting-started"><div><h2>Start tracking this month</h2><p class="muted">Add income or spending to your ledger and set a monthly limit for a category.</p></div><div class="getting-started-actions">'+
      button('Add transaction','create-transaction',{className:'button primary'})+button('Set a budget','add-budget')+'</div></section>'
    : '';
  return intro('overview','Household cash flow, budgets, and activity for '+periodLabel(state.month)+'.',
    writable()?button('Add transaction','create-transaction',{className:'button primary',icon:'add'}):'')+
    setup+'<div class="kpis">'+cards+'</div><div class="dash-grid"><section class="panel">'+panelHead('Cash flow','Last 6 months','<div class="legend"><span><i class="dot"></i>Income</span><span><i class="dot light"></i>Spending</span></div>')+chart(data.trend)+'</section>'+
    '<section class="panel">'+panelHead('Budget watch','Categories closest to their limits',go('All budgets →','budgets'))+
    (focus.length?'<div class="budget-list">'+focus.map(row=>budgetItem(row)).join('')+'</div>':empty('No budget limits set this month.'))+'</section></div>'+
    '<div class="dash-grid"><section class="panel">'+panelHead('Recent transactions',data.transaction_count+' '+(data.transaction_count===1?'ledger entry':'ledger entries')+' this month',go('View ledger →','transactions'))+
    table(['Merchant / Account','Posted','Category','~Amount'],rows)+'</section><section class="panel">'+panelHead('Household activity','',writable()?button('Add account','add-ledger-account',{className:'link-button'}):'')+
    '<div class="snapshot"><div class="snapshot-row"><span>'+icon('recurring')+' Recurring payments</span>'+go(String(data.recurring_count)+' →','recurring')+'</div>'+
    '<div class="snapshot-row"><span>'+icon('exceptions')+' Open exceptions</span>'+go(String(data.open_exceptions)+' →','exceptions')+'</div>'+
    state.reference.accounts.map(account=>'<div class="snapshot-row"><span>'+esc(account.name)+'</span><strong>'+money(account.balance_cents)+'</strong></div>').join('')+
    '<p class="footer-note">Account balances include the full ledger.</p></div></section></div>'+
    '<section class="panel">'+panelHead('Spending by category','Net spending after refunds')+
    table(['Category','~Spending'],data.category_spending.map(row=>[esc(row.category),money(row.cents)]))+'</section>';
}

function transactions(data) {
  const rows = data.items.map(row=>[merchantCell(row),esc(dateLabel(row.local_date)),category(row),pill(row.decision,row.category?'neutral':'warn'),
    amount(row.cents),button('Details','transaction-detail',{id:row.id})]);
  return intro('transactions','Search and review ledger entries for '+periodLabel(state.month)+'.',
    writable()?button('Add transaction','create-transaction',{className:'button primary',icon:'add'}):'')+
    '<section class="panel"><form id="search-form" class="toolbar"><input name="q" aria-label="Search transactions" placeholder="Search merchant or ID" value="'+esc(state.q)+'">'+
    '<select name="kind" aria-label="Transaction type">'+options([['','All types'],['expense','Expenses'],['income','Income'],['refund','Refunds'],['transfer','Transfers']],state.kind)+'</select>'+
    '<button class="button" type="submit">Filter</button><span class="muted text-sm">'+data.total+' results</span></form>'+
    table(['Merchant / Account','Posted','Category','Decision','~Amount',''],rows)+pager(data)+'</section>';
}

function budgets(data) {
  const cards = data.items.map(row=>'<article class="panel detail-card"><div class="flex items-center justify-between gap-2"><h2>'+esc(row.category)+'</h2>'+
    pill(row.status,row.status==='over'?'bad':row.status==='near'?'warn':'')+'</div><div class="kpi-value">'+money(row.spent_cents)+'</div>'+
    budgetItem(row,false)+(writable()?'<div class="actions">'+button('Edit limit','edit-budget',{id:row.id})+button('Archive','archive-budget',{id:row.id,className:'button danger'})+'</div>':'')+'</article>');
  return intro('budgets','Compare category spending against monthly limits for '+periodLabel(state.month)+'.',
    writable()?button('Add budget','add-budget',{className:'button primary',icon:'add'}):'')+
    (cards.length?'<div class="list-grid">'+cards.join('')+'</div>':'<div class="panel">'+empty('No budgets for this month.')+'</div>');
}

function recurring(data) {
  const cards = data.items.map(row=>'<article class="panel detail-card recurring-card"><div class="flex items-center justify-between gap-2"><h2>'+esc(row.name)+'</h2>'+pill(row.typical_cents>0?'Income':'Payment')+'</div>'+
    '<div class="recurring-amount"><span class="kpi-value">'+money(Math.abs(row.typical_cents))+'</span><span class="muted">'+row.count+' occurrences</span></div>'+
    '<ol class="recurring-timeline">'+[['First seen',row.first_date],['Last seen',row.last_date],['Next expected',row.next_date]].map(([label,date])=>
      '<li><span>'+label+'</span><strong>'+esc(dateLabel(date))+'</strong></li>').join('')+'</ol>'+
    (row.missing_months.length?'<p class="warning">Missing '+esc(row.missing_months.map(periodLabel).join(', '))+'</p>':'')+button('View occurrences','occurrences',{id:row.id})+'</article>');
  return intro('recurring','Detected monthly income and payments across the household ledger.',
    writable()?button('Run detection','detect',{className:'button primary',icon:'recurring'}):'')+
    (cards.length?'<div class="list-grid">'+cards.join('')+'</div>':'<div class="panel">'+empty('No monthly series detected.')+'</div>');
}

function exceptions(data) {
  const rows = data.items.map(row=>[esc(pretty(row.issue_type)),esc(row.details.message || 'Review required.'),
    esc(row.transaction_id || row.series_id || row.details.code || '—'),pill(row.state,row.state==='open'?'warn':''),
    row.state==='open' && staff()?button('Resolve','resolve',{id:row.id}):'']);
  return intro('exceptions','Review ambiguous categories, rejected budgets, and posting or recurrence anomalies.')+
    '<section class="panel">'+table(['Issue','Reason','Reference','Status',''],rows)+pager(data)+'</section>';
}

function insights(data) {
  return intro('insights','Review the recorded budget comparisons for '+periodLabel(state.month)+'.')+
    '<section class="panel">'+table(['Category','Revision','~Net spending','~Limit','Status','Recorded'],data.items.map(row=>[
      esc(row.category),esc(row.revision),money(row.spent_cents),money(row.limit_cents),pill(row.status,row.status==='over'?'bad':row.status==='near'?'warn':''),
      esc(dateLabel(row.created_at))]))+pager(data)+'</section>';
}

function audit(data) {
  const values = row => '<details class="audit-disclosure"><summary>View changes</summary><pre class="audit-values">'+esc(JSON.stringify({before:row.before,after:row.after},null,2))+'</pre></details>';
  return intro('audit','Trace actions, actors, and previous values in the household ledger.')+
    '<section class="panel">'+table(['Action','Record','Actor','Recorded','Changes'],data.items.map(row=>[
      pill(row.action,'neutral'),esc(row.entity)+'<div class="muted">'+esc(row.entity_id)+'</div>',esc(row.actor),esc(dateLabel(row.created_at)),values(row)]))+pager(data)+'</section>';
}
function rules(data) {
  const rows = data.items.map(row=>[esc(row.pattern),esc(row.category_id),esc(row.priority),esc(dateLabel(row.starts)),
    esc(dateLabel(row.ends)),pill(row.state,row.state==='archived'?'neutral':''),admin()?
    button('Edit','edit-rule',{id:row.id})+(row.state==='active'?' '+button('Archive','archive-rule',{id:row.id}):''):'']);
  return intro('rules','Inspect descriptor matching, priority, and effective dates.',
    admin()?button('Add rule','add-rule',{className:'button primary',icon:'add'}):'')+
    '<section class="panel">'+table(['Pattern','Category','Priority','Starts','Ends','State',''],rows)+pager(data)+'</section>';
}

function catalog(data) {
  return intro('catalog','Manage households, accounts, merchants, and the category taxonomy.',
    button('Add record','add-catalog',{className:'button primary',icon:'add'}))+
    '<section class="panel"><div class="toolbar"><label for="catalog">Catalog</label><select id="catalog">'+
    options([['categories','Categories'],['merchants','Merchants'],['households','Households'],['accounts','Accounts']],state.catalog)+
    '</select></div>'+table(['Record','ID','Details','State',''],data.items.map(row=>[
      esc(row.name),esc(row.id || row.code),esc(row.kind || row.timezone || row.profile_id || '—'),pill(row.state,row.state==='archived'?'neutral':''),
      button('Edit','edit-catalog',{id:row.id || row.code})+(row.state==='active'?' '+button('Archive','archive-catalog',{id:row.id || row.code}):'')]))+pager(data)+'</section>';
}

function evaluation(data) {
  const percent = value => value == null ? '—' : (value*100).toFixed(2)+'%';
  const cards = [['Category accuracy',percent(data.categorization_accuracy)],['Category coverage',percent(data.categorization_coverage)],
    ['Recurring precision',percent(data.recurring_precision)],['Recurring recall',percent(data.recurring_recall)]];
  return intro('evaluation','Offline validation against separately generated ground truth.')+
    (data.available?'<div class="kpis">'+cards.map(([name,value])=>'<section class="kpi"><div class="kpi-label">'+name+'</div><p class="kpi-value">'+value+'</p></section>').join('')+
    '</div><section class="panel">'+panelHead('Dataset validation','Seed '+data.configuration.seed)+table(['Check','~Value'],[
      ['Profiles',data.configuration.profiles],['Transactions',data.configuration.transactions],['Ledger reconciliation delta',data.checks.ledger_delta],
      ['Budget reconciliation delta',data.checks.budget_delta],['Recurring true positives',data.checks.recurring_tp],
      ['Recurring false positives',data.checks.recurring_fp],['Recurring false negatives',data.checks.recurring_fn],
    ].map(([name,value])=>[name,esc(Number(value).toLocaleString('en-IN'))]))+'</section>':'<div class="panel">'+empty('Validation evidence is unavailable.')+'</div>');
}

const screens = {overview,transactions,budgets,recurring,exceptions,insights,audit,rules,catalog,evaluation};
async function render() {
  if (!state.session?.authenticated) return;
  const version = ++viewVersion;
  const current = section();
  shell(current);
  try {
    const endpoint = current === 'catalog' ? 'catalog/'+state.catalog : current;
    const [reference,data] = await Promise.all([api(path('reference')),api(path(endpoint,{page:state.page,q:state.q,kind:state.kind}))]);
    if (version !== viewVersion) return;
    state.reference = reference;
    state.data = data;
    shell(current);
    document.querySelector('#content').innerHTML = screens[current](data);
  } catch (error) {
    if (version !== viewVersion) return;
    document.querySelector('#content').innerHTML = '<div class="panel error-box" role="alert">'+esc(error.message)+' '+button('Retry','retry')+'</div>';
  }
}

function selectField(label,name,items,current='') {
  return '<label class="field">'+esc(label)+'<select name="'+name+'" required>'+options(items,current)+'</select></label>';
}

function inputField(label,name,value='',type='text',attrs='required') {
  return '<label class="field">'+esc(label)+'<input name="'+name+'" type="'+type+'" value="'+esc(value)+'" '+attrs+'></label>';
}

function modal(title,body,submitLabel,handler) {
  dialog.innerHTML = '<form id="modal-form"><header class="dialog-head"><h2 id="dialog-title">'+esc(title)+'</h2>'+
    '<button type="button" class="icon-button" data-action="close" aria-label="Close">'+icon('close')+'</button></header>'+
    '<div class="dialog-body">'+body+'<p class="form-error" role="alert"></p></div><footer class="dialog-actions">'+
    button(submitLabel?'Cancel':'Close','close')+(submitLabel?'<button type="submit" class="button primary">'+esc(submitLabel)+'</button>':'')+'</footer></form>';
  dialog.showModal();
  if (!submitLabel) return;
  const form = dialog.querySelector('form');
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    const submit = form.querySelector('[type=submit]');
    submit.disabled = true;
    form.querySelector('.form-error').textContent = '';
    try {
      await handler(Object.fromEntries(new FormData(form)));
      dialog.close();
      await render();
    } catch (error) {
      form.querySelector('.form-error').textContent = error.message;
      submit.disabled = false;
    }
  });
}

const categoryChoices = kind => state.reference.categories.filter(row=>!kind || row.kind===kind).map(row=>[row.code,row.name]);
const merchantChoices = () => [['','No merchant'],...state.reference.merchants.map(row=>[row.id,row.name])];
const accountChoices = () => state.reference.accounts.map(row=>[row.id,row.name]);
const lookup = id => state.data.items.find(row=>String(row.id || row.code)===String(id));

function paise(input) {
  if (!/^-?\d+(\.\d{1,2})?$/.test(input)) throw Error('Use an amount with up to two decimal places.');
  const [whole,fraction=''] = input.replace(/^-/,'').split('.');
  return (input.startsWith('-')?-1:1)*(Number(whole)*100+Number(fraction.padEnd(2,'0')));
}

function householdTimestamp(value) {
  const target = Date.parse(value+'Z');
  const formatter = new Intl.DateTimeFormat('en-CA',{timeZone:state.reference.profile.timezone,
    year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  let time = target;
  for (let round=0;round<3;round++) {
    const fields = Object.fromEntries(formatter.formatToParts(new Date(time)).map(part=>[part.type,part.value]));
    const displayed = Date.parse(fields.year+'-'+fields.month+'-'+fields.day+'T'+fields.hour+':'+fields.minute+'Z');
    if (displayed===target) return new Date(time).toISOString();
    time += target-displayed;
  }
  throw Error('This local time is unavailable in the household timezone.');
}

function addTransaction() {
  if (!accountChoices().length) return announce('Create an active account first.',true);
  modal('Add transaction',selectField('Type','kind',[['expense','Expense'],['income','Income'],['refund','Refund'],['transfer','Transfer']])+
    selectField('Account','account_id',accountChoices())+
    '<div id="destination-field" hidden>'+selectField('Destination','destination_id',accountChoices())+'</div>'+
    '<div id="refund-field" hidden>'+inputField('Original expense ID','related_id','','text','placeholder="TXN-2026-00000001"')+'</div>'+
    selectField('Merchant','merchant_id',merchantChoices()).replace(' required','')+
    inputField('Statement description','descriptor','','text','required maxlength="120"')+
    inputField('Amount (₹)','amount','','number','required min="0.01" step="0.01"')+
    inputField('Household posting time','occurred_at',state.month+'-15T12:00','datetime-local')+
    '<p class="muted text-sm">'+esc(state.reference.profile.timezone)+'</p>','Save transaction',async fields=>{
      const cents = paise(fields.amount);
      await api(path('transactions'),'POST',{...fields,cents:fields.kind==='expense'?-cents:cents,occurred_at:householdTimestamp(fields.occurred_at)});
      announce('Transaction saved.');
    });
  const form = dialog.querySelector('form');
  const kind = form.elements.kind;
  const toggle = () => {
    const transfer = kind.value === 'transfer';
    const refund = kind.value === 'refund';
    form.querySelector('#destination-field').hidden = !transfer;
    form.elements.destination_id.disabled = !transfer;
    form.querySelector('#refund-field').hidden = !refund;
    form.elements.related_id.required = refund;
  };
  kind.addEventListener('change',toggle);
  toggle();
}

async function transactionDetail(id) {
  const row = await api(path('transactions/'+encodeURIComponent(id)));
  const edit = writable() && ['expense','income'].includes(row.kind);
  const trace = '<p><strong>'+esc(row.merchant || row.descriptor)+'</strong> · '+amount(row.cents)+'</p>'+
    '<p class="muted">'+esc(row.id)+' · '+esc(row.account)+'</p><p>'+esc(row.descriptor)+'</p>'+
    '<p>Posted '+esc(dateLabel(row.local_date))+' · '+esc(pretty(row.decision))+(row.rule_id?' · Rule '+esc(row.rule_id):'')+'</p>'+
    (row.related_id?'<p>Linked to '+esc(row.related_id)+'</p>':'');
  modal('Transaction details',trace+(edit?selectField('Category','category_id',[['','Choose a category'],...categoryChoices(row.kind)],row.category_id || ''):'')+
    (writable()?button(row.kind==='transfer'?'Archive transfer pair':'Archive transaction','archive-transaction',{id:row.id,className:'button danger'}):''),
    edit?'Save category':'',async fields=>{
      await api(path('transactions/'+encodeURIComponent(id)),'PATCH',fields);
      announce('Category updated.');
    });
}

function budgetForm(id) {
  const row = id ? lookup(id) : null;
  const used = new Set((state.data.items || state.data.budgets || []).map(item=>item.code));
  const choices = categoryChoices('expense').filter(([code])=>!used.has(code));
  if (!row && !choices.length) return announce('All active expense categories already have a budget.',true);
  modal(row?'Edit budget':'Add budget',
    (row?'<p>'+esc(row.category)+' · '+esc(periodLabel(state.month))+'</p>':selectField('Category','category_id',choices))+
    inputField('Monthly limit (₹)','amount',row ? row.limit_cents/100 : '','number','required min="0.01" step="0.01"'),
    'Save budget',async fields=>{
      await api(path('budgets'+(id?'/'+encodeURIComponent(id):'')),id?'PATCH':'POST',{...fields,limit_cents:paise(fields.amount)});
      announce('Budget saved.');
    });
}

function ruleForm(id) {
  const row = id ? lookup(id) : {};
  modal(id?'Edit rule':'Add rule',inputField('Descriptor pattern','pattern',row.pattern)+
    selectField('Category','category_id',categoryChoices().filter(([code])=>code!=='transfer'),row.category_id)+
    selectField('Merchant restriction','merchant_id',merchantChoices(),row.merchant_id || '').replace(' required','')+
    inputField('Priority (lower runs first)','priority',row.priority ?? 100,'number','required min="0" max="10000"')+
    inputField('Effective from','starts',row.starts || state.month+'-01','date')+
    inputField('Effective through','ends',row.ends || '','date',''),'Save rule',async fields=>{
      await api(path('rules'+(id?'/'+id:'')),id?'PATCH':'POST',{...fields,priority:Number(fields.priority)});
      announce('Rule saved.');
    });
}

function catalogForm(id) {
  const row = id ? lookup(id) : {};
  let fields = inputField('Name','name',row.name);
  if (state.catalog==='categories' && !id) fields += inputField('Code','code')+
    selectField('Type','kind',[['expense','Expense'],['income','Income']]);
  if (state.catalog==='households') fields += inputField('Timezone','timezone',row.timezone || 'Asia/Kolkata');
  if (state.catalog==='accounts' && !id) fields += selectField('Household','profile_id',state.reference.profiles.map(p=>[p.id,p.name]),state.reference.profile.id)+
    inputField('Opening balance (₹)','amount','0','number','required step="0.01"');
  modal(id?'Edit record':'Add record',fields,'Save record',async values=>{
    if ('amount' in values) values.opening_cents = paise(values.amount);
    await api(path('catalog/'+state.catalog+(id?'/'+encodeURIComponent(id):'')),id?'PATCH':'POST',values);
    announce('Record saved.');
  });
}

function archive(endpoint,id) {
  if (dialog.open) dialog.close();
  modal('Archive record','<p>Archive this record from active views? Its history will remain in the audit trail.</p>','Archive',async()=>{
    await api(path(endpoint+'/'+encodeURIComponent(id)),'DELETE');
    announce('Record archived.');
  });
}

function toggleMenu(close=false) {
  const sidebar = document.querySelector('#sidebar');
  const toggle = document.querySelector('.mobile-menu');
  if (!sidebar || !toggle) return;
  const wasOpen = sidebar.classList.contains('open');
  const open = !close && !wasOpen;
  sidebar.classList.toggle('open',open);
  toggle.setAttribute('aria-expanded',String(open));
  if (open) sidebar.querySelector('.brand').focus();
  else if (wasOpen) toggle.focus();
}

const actions = {
  nav: navigate,
  demo: username=>submitAuth('login',{username,password:'LedgerDemo2026!'}),
  'auth-mode':mode=>loginScreen(mode,true),
  'show-password':(id, trigger)=>{
    const field = document.getElementById(id);
    const showing = field.type==='password';
    field.type = showing?'text':'password';
    const label = field.labels[0].textContent.toLowerCase();
    trigger.textContent = showing?'Hide':'Show';
    trigger.setAttribute('aria-label',(showing?'Hide ':'Show ')+label);
  },
  'account-settings':()=>modal('Account settings',
    '<p class="muted">Signed in as <strong>'+esc(state.session.username)+'</strong></p>'+
    passwordField('Current password','current_password','current-password')+
    passwordField('New password','password','new-password',true)+
    '<p class="auth-hint">'+passwordRequirements+'</p>'+passwordField('Confirm new password','confirm_password','new-password',true),
    'Change password',async fields=>{
      if (fields.password !== fields.confirm_password) throw Error('The passwords do not match.');
      state.session = await api('password/','POST',fields);
      announce('Password changed.');
    }),

  menu: ()=>toggleMenu(),
  'chart-month':(_,trigger)=>{
    const selected = trigger.getAttribute('aria-pressed') === 'true';
    trigger.parentElement.querySelectorAll('[aria-pressed]').forEach(month=>month.setAttribute('aria-pressed','false'));
    trigger.setAttribute('aria-pressed',String(!selected));
  },
  close: ()=>dialog.close(),
  retry: render,
  page: id=>{state.page=Number(id);return render();},
  logout: async()=>{
    state.session=await api('logout/','POST',{});
    ++viewVersion;
    history.replaceState(null,'',location.pathname);
    loginScreen();
  },
  'add-ledger-account':()=>modal('Add ledger account',
    inputField('Account name','name','','text','required maxlength="80"')+
    inputField('Opening balance (₹)','balance','0','number','required step="0.01"'),
    'Add account',async fields=>{
      await api(path('accounts'),'POST',{name:fields.name,opening_cents:paise(fields.balance)});
      announce('Ledger account added.');
    }),
  'create-transaction':addTransaction,
  'transaction-detail':transactionDetail,
  'archive-transaction':id=>archive('transactions',id),
  'add-budget':()=>budgetForm(),
  'edit-budget':budgetForm,
  'archive-budget':id=>archive('budgets',id),
  'add-rule':()=>ruleForm(),
  'edit-rule':ruleForm,
  'archive-rule':id=>archive('rules',id),
  'add-catalog':()=>catalogForm(),
  'edit-catalog':catalogForm,
  'archive-catalog':id=>archive('catalog/'+state.catalog,id),
  occurrences: async id=>{
    const result = await api(path('recurring/'+encodeURIComponent(id)));
    modal(result.name,table(['Posted','~Amount'],result.items.map(row=>[esc(dateLabel(row.local_date)),amount(row.cents)])));
  },
  detect: async()=>{await api(path('recurring'),'POST',{});announce('Detection complete.');await render();},
  resolve: id=>modal('Resolve exception',inputField('Resolution note','note','','text','required maxlength="240"'),'Resolve',async fields=>{
    await api(path('exceptions/'+id),'PATCH',fields);announce('Exception resolved.');
  }),
};

document.addEventListener('click',async event=>{
  const trigger = event.target.closest('[data-action]');
  if (!trigger || trigger.disabled) return;
  const action = actions[trigger.dataset.action];
  if (!action) return;
  const hold = ['detect','demo','logout','transaction-detail'].includes(trigger.dataset.action);
  if (hold) trigger.disabled = true;
  try { await action(trigger.dataset.id,trigger); }
  catch (error) { announce(error.message,true); }
  finally { if (hold) trigger.disabled = false; }
});

document.addEventListener('change',event=>{
  const input = event.target;
  if (input.id==='period' && input.validity.valid && input.value) state.month=input.value;
  else if (input.id==='profile') state.profile=input.value;
  else if (input.id==='catalog') state.catalog=input.value;
  else return;
  state.page=1;
  render();
});

document.addEventListener('submit',event=>{
  if (event.target.id==='auth-form') {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target));
    if (!event.target.querySelector('[type=submit]').disabled) submitAuth(event.target.dataset.mode,values);
  } else if (event.target.id==='search-form') {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target));
    Object.assign(state,values,{page:1});
    render();
  }
});

window.addEventListener('hashchange',()=>{state.page=1;state.q='';state.kind='';render();});
window.addEventListener('keydown',event=>{
  if (event.key==='Escape' && !dialog.open) toggleMenu(true);
});

try {
  state.session = await api('session/');
  if (state.session.authenticated) {
    state.month = initialMonth(state.session);
    await render();
  } else loginScreen();
} catch (error) {
  root.innerHTML = '<main class="boot"><p role="alert">'+esc(error.message)+'</p><button class="button" type="button" id="reload">Retry</button></main>';
  document.querySelector('#reload').addEventListener('click',()=>location.reload());
}