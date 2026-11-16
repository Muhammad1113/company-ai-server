import test from 'node:test';
import assert from 'node:assert/strict';
import { actionLabel, actorLabel, detailText, AUDIT_GROUPS } from '../src/lib/audit.js';

test('known actions get a readable label, unknown ones fall back safely', () => {
  assert.equal(actionLabel('user.password_reset'), 'Reset password of');
  assert.equal(actionLabel('regulatory.approve'), 'Approved update');
  assert.equal(actionLabel('something.new_thing'), 'something new thing');
  assert.equal(actionLabel(undefined), '');
});

test('access denied and logout have labels', () => {
  assert.equal(actionLabel('access.denied'), 'Blocked from a collection');
  assert.equal(actionLabel('auth.logout'), 'Signed out');
});

test('detail is a single line and skips empty values', () => {
  assert.equal(detailText({ role: 'user', company: 'acme', note: '' }), 'role: user · company: acme');
  assert.equal(detailText({ fields: ['password', 'disabled'] }), 'fields: password, disabled');
  assert.equal(detailText(null), '');
});

test('actor label shows company or super admin', () => {
  assert.equal(actorLabel({ actor: 'ali', company: 'acme', role: 'user' }), 'ali (acme)');
  assert.equal(actorLabel({ actor: 'root', company: null, role: 'admin' }), 'root (super admin)');
  assert.equal(actorLabel({ actor: 'mallory', company: null, role: null }), 'mallory');
});

test('filter groups start with All events', () => {
  assert.deepEqual(AUDIT_GROUPS[0], ['', 'All events']);
});
