import {test} from 'node:test';
import assert from 'node:assert/strict';
import {businessDays,carryover,accruedPto,noticeValid,salesBlackout} from '../src/logic.ts';
test('weekends and explicit holidays',()=>{assert.equal(businessDays('2026-11-13','2026-11-16',[]),2);assert.equal(businessDays('2026-11-13','2026-11-16',['2026-11-16']),1);assert.equal(businessDays('2026-11-14','2026-11-15',[]),0)});
test('reject invalid and reversed ranges',()=>{assert.throws(()=>businessDays('2026-11-16','2026-11-13'));assert.throws(()=>businessDays('bad','2026-11-13'))});
test('carryover and accrual caps',()=>{assert.equal(carryover(8),5);assert.equal(carryover(3),3);assert.equal(carryover(-1),0);assert.ok(Math.abs(accruedPto(1)-1.67)<.001);assert.ok(accruedPto(12)<=20)});
test('14 day PTO notice and sick exemption',()=>{assert.equal(noticeValid('2026-11-16','2026-11-18','PTO','2026-11-03',[]),false);assert.equal(noticeValid('2026-11-16','2026-11-18','PTO','2026-11-02',[]),true);assert.equal(noticeValid('2026-11-16','2026-11-18','sick','2026-11-16',[]),true)});
test('quarter blackout boundaries and spanning ranges',()=>{assert.equal(salesBlackout('2026-03-17','2026-03-17'),false);assert.equal(salesBlackout('2026-03-18','2026-03-18'),true);assert.equal(salesBlackout('2026-03-17','2026-04-01'),true);assert.equal(salesBlackout('2026-04-01','2026-04-02'),false);assert.equal(salesBlackout('2026-12-18','2026-12-31'),true)});
