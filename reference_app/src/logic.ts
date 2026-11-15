const DAY=86400000;
function date(value:string):Date {const d=new Date(value+'T00:00:00Z');if(!/^\d{4}-\d{2}-\d{2}$/.test(value)||!Number.isFinite(+d)||d.toISOString().slice(0,10)!==value)throw Error('Invalid date');return d}
function range(start:string,end:string):[Date,Date] {const s=date(start),e=date(end);if(s>e)throw Error('End date precedes start date');if(+e-+s>DAY*366)throw Error('Choose a range within one year');return [s,e]}
export function businessDays(start:string,end:string,holidays:string[]=[]):number {const [s,e]=range(start,end);let n=0;for(let t=+s;t<=+e;t+=DAY){const d=new Date(t);if(d.getUTCDay()!==0&&d.getUTCDay()!==6&&!holidays.includes(d.toISOString().slice(0,10)))n++}return n}
export function carryover(unused:number):number {return Math.min(5,Math.max(0,unused))}
export function accruedPto(months:number):number {return Math.min(20,Math.max(0,Math.round(months*1.67*100)/100))}
export function noticeValid(start:string,end:string,type:string,today:string,holidays:string[]=[]):boolean {return type.toLowerCase()==='sick'||businessDays(start,end,holidays)<3||+date(start)-+date(today)>=14*DAY}
export function salesBlackout(start:string,end:string):boolean {const [s,e]=range(start,end);for(let y=s.getUTCFullYear();y<=e.getUTCFullYear();y++)for(const m of [3,6,9,12]){const q=Date.UTC(y,m,0),first=q-13*DAY;if(+s<=q&&+e>=first)return true}return false}
export function consecutiveDays(start:string,end:string):number {const [s,e]=range(start,end);return (+e-+s)/DAY+1}
export function overlaps(a:string,b:string,c:string,d:string):boolean {return a<=d&&b>=c}
