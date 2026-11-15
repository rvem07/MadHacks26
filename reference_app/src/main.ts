import './style.css';
import {businessDays,carryover,accruedPto,noticeValid,salesBlackout,consecutiveDays,overlaps} from './logic.ts';
import type {Employee,Request,Kind,Status} from './types.ts';
// All identities, balances and holidays below are explicitly fictional demo configuration.
const staff:Employee[]=[
 {email:'ceo@example.test',name:'Demo CEO',team:'Executive',manager:'',role:'ceo'},
 {email:'manager@example.test',name:'Demo Manager',team:'Engineering',manager:'ceo@example.test',role:'manager'},
 {email:'alex@example.test',name:'Alex Demo',team:'Engineering',manager:'manager@example.test',role:'employee'},
 {email:'sam@example.test',name:'Sam Demo',team:'Engineering',manager:'manager@example.test',role:'employee'},
 {email:'lee@example.test',name:'Lee Demo',team:'Engineering',manager:'manager@example.test',role:'employee'},
 {email:'sales@example.test',name:'Demo Sales Lead (Raj role)',team:'Sales',manager:'ceo@example.test',role:'manager'},
 {email:'salesperson@example.test',name:'Taylor Demo',team:'Sales',manager:'sales@example.test',role:'employee'},
 {email:'cs@example.test',name:'Casey Demo',team:'Customer Success',manager:'manager@example.test',role:'employee'}];
const holidays:string[]=[];const storage='fernwood-timeoff-demo-v1';
let requests:Request[]=JSON.parse(localStorage.getItem(storage)||'[]'),user=staff[2];
const app=document.querySelector<HTMLElement>('#app')!;
const today=()=>new Date().toISOString().slice(0,10);
const el=(tag:string,text='',cls='')=>{const e=document.createElement(tag);e.textContent=text;e.className=cls;return e};
function button(text:string,fn:()=>void){const b=el('button',text) as HTMLButtonElement;b.type='button';b.onclick=fn;return b}
function used(email:string,type:Kind,exclude=''):number {return requests.filter(r=>r.id!==exclude&&r.employee_email===email&&r.type===type&&r.status==='approved'&&r.start_date.slice(0,4)===today().slice(0,4)).reduce((n,r)=>n+businessDays(r.start_date,r.end_date,holidays),0)}
function allowance(type:Kind):number {return type==='sick'?5:accruedPto(new Date().getUTCMonth()+1)+carryover(0)}
function save(){localStorage.setItem(storage,JSON.stringify(requests));render()}
function check(r:Request,approving=false):string {
 const employee=staff.find(s=>s.email===r.employee_email)!;const days=businessDays(r.start_date,r.end_date,holidays),length=consecutiveDays(r.start_date,r.end_date);
 if(!days)return 'Choose at least one business day.';
 if(r.start_date.slice(0,4)!==today().slice(0,4)||r.end_date.slice(0,4)!==today().slice(0,4))return 'Demo supports the current calendar year only.';
 if(days>allowance(r.type)-used(r.employee_email,r.type,r.id))return 'Insufficient demo balance.';
 if(!noticeValid(r.start_date,r.end_date,r.type,r.created_at.slice(0,10),holidays))return 'PTO of 3+ business days needs 14 calendar days notice.';
 if(approving&&length>=6&&length<=10)return 'Approval for 6–10 consecutive days is unresolved. Ask People/CEO; automatic approval is blocked.';
 if(approving&&r.approver_email!==user.email)return 'Switch to the designated mock approver.';
 const team=staff.filter(s=>s.team===employee.team);const concurrent=requests.filter(x=>x.id!==r.id&&x.status==='approved'&&team.some(s=>s.email===x.employee_email));
 if(approving)for(let t=+new Date(r.start_date+'T00:00Z');t<=+new Date(r.end_date+'T00:00Z');t+=86400000){const d=new Date(t).toISOString().slice(0,10);if(!businessDays(d,d,holidays))continue;const away=new Set([r.employee_email,...concurrent.filter(x=>x.start_date<=d&&x.end_date>=d).map(x=>x.employee_email)]);if(away.size>2)return 'No more than 2 people may be out on a team.';if(employee.team==='Customer Success'&&away.size>=team.length)return 'At least one Customer Success teammate must be working.'}
 if(approving&&employee.team==='Sales'&&r.type==='PTO'&&salesBlackout(r.start_date,r.end_date)&&user.email!=='sales@example.test')return 'Sales blackout requires separate Sales-lead approval; this demo cannot combine that with CEO approval.';
 return '';
}
function render(){app.replaceChildren();const header=el('header');header.append(el('h1','Time off, together.'),el('p','FICTIONAL DEMO · mock login · localStorage · no real employee data'));app.append(header);
 const label=el('label','Mock identity '),login=el('select') as HTMLSelectElement;login.id='mock-login';for(const s of staff){const o=el('option',s.name) as HTMLOptionElement;o.value=s.email;login.append(o)}login.value=user.email;login.onchange=()=>{user=staff.find(s=>s.email===login.value)!;render()};label.append(login);app.append(label);
 app.append(el('p',`Demo balances: ${Math.max(0,allowance('PTO')-used(user.email,'PTO')).toFixed(2)} PTO / ${Math.max(0,5-used(user.email,'sick'))} sick. 20 PTO annual, 1.67/month capped20; 5 sick; carryover cap5. Starting carryover0, no configured holidays. December31 forfeits amounts above cap5. Configure balances/holidays before real use.`));
 const form=el('form') as HTMLFormElement;form.id='request-form';form.append(el('h2','Request time off'));
 for(const [name,text]of [['start','Start date'],['end','End date']]){const l=el('label',text),i=el('input') as HTMLInputElement;i.type='date';i.name=name;i.required=true;i.min=today();l.append(i);form.append(l)}
 const typeLabel=el('label','Type'),type=el('select') as HTMLSelectElement;type.name='type';for(const kind of ['PTO','sick']){const o=el('option',kind) as HTMLOptionElement;o.value=kind;type.append(o)}typeLabel.append(type);form.append(typeLabel);
 const submit=el('button','Request time off') as HTMLButtonElement;submit.type='submit';submit.className='primary';form.append(submit);const message=el('p');message.setAttribute('role','alert');form.append(message);
 form.onsubmit=e=>{e.preventDefault();try{const f=new FormData(form),start=String(f.get('start')),end=String(f.get('end')),kind=String(f.get('type')) as Kind;
 const approver=consecutiveDays(start,end)>10?'ceo@example.test':user.team==='Sales'&&kind==='PTO'&&salesBlackout(start,end)?'sales@example.test':user.manager;
 const r:Request={id:crypto.randomUUID(),employee_email:user.email,start_date:start,end_date:end,type:kind,status:'pending',approver_email:approver,created_at:new Date().toISOString()};const problem=check(r);if(problem)throw Error(problem);requests.push(r);save()}catch(e){message.textContent=(e as Error).message}};app.append(form);
 const list=el('section');list.id='request-list';list.append(el('h2','Requests'));
 for(const r of requests){const row=el('article');row.append(el('span',`${staff.find(s=>s.email===r.employee_email)?.name}: ${r.start_date} → ${r.end_date} · ${r.type} `),el('span',r.status,'status '+r.status));if(r.employee_email===user.email&&['pending','approved'].includes(r.status))row.append(button('Cancel',()=>{r.status='cancelled';save()}));list.append(row)}app.append(list);
 const approvals=el('section');approvals.id='approver-view';approvals.append(el('h2','Approver view'));
 for(const r of requests.filter(r=>r.status==='pending'&&r.approver_email===user.email)){const row=el('article');row.append(el('p',`${r.employee_email} · ${r.start_date} → ${r.end_date}`));const note=el('p');note.setAttribute('role','alert');row.append(button('Approve',()=>{const problem=check(r,true);if(problem){note.textContent=problem;return}r.status='approved';save()}),button('Deny',()=>{r.status='denied';save()}),note);approvals.append(row)}app.append(approvals);
 const calendar=el('section');calendar.id='team-calendar';calendar.append(el('h2','Team calendar · overlaps'));
 const month=el('input') as HTMLInputElement;month.type='month';month.value=today().slice(0,7);month.setAttribute('aria-label','Calendar month');calendar.append(month);const grid=el('div','','calendar');calendar.append(grid);
 function draw(){grid.replaceChildren();const [y,m]=month.value.split('-').map(Number);if(!y||!m)return;for(let day=1;day<=new Date(Date.UTC(y,m,0)).getUTCDate();day++){const date=`${month.value}-${String(day).padStart(2,'0')}`,cell=el('div',String(day),'day');for(const r of requests.filter(r=>['pending','approved'].includes(r.status)&&r.start_date<=date&&r.end_date>=date))cell.append(el('p',`${staff.find(s=>s.email===r.employee_email)?.name} · ${r.status}`,r.status));grid.append(cell)}}month.onchange=draw;draw();app.append(calendar);
 app.append(button('Export approved days CSV',()=>{const lines=['employee_email,start_date,end_date,type,business_days',...requests.filter(r=>r.status==='approved').map(r=>[r.employee_email,r.start_date,r.end_date,r.type,businessDays(r.start_date,r.end_date,holidays)].join(','))];const url=URL.createObjectURL(new Blob([lines.join('\n')],{type:'text/csv'}));const a=el('a') as HTMLAnchorElement;a.href=url;a.download='approved-days.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}));
 app.append(el('footer','Slack-first is a requested future integration. Slack notifications, Google Calendar sync and Google Workspace login are NOT connected. Payroll export is manual; payroll runs on the 1st/15th.'));
}
render();
