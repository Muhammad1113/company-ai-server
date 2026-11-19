// Audit log: filter groups aur har event ko padhne layak jumla (content/password kabhi nahi, sirf kaun/kya/kis par).
export const AUDIT_GROUPS = [
  ['', 'All events'],
  ['auth', 'Sign-ins and passwords'],
  ['access', 'Access denied'],
  ['user', 'Users'],
  ['company', 'Companies'],
  ['document', 'Documents'],
  ['regulatory', 'Regulatory'],
];

const LABELS = {
  'auth.login': 'Signed in',
  'auth.login_failed': 'Failed sign-in',
  'auth.logout': 'Signed out',
  'access.denied': 'Blocked from a collection',
  'auth.password_change': 'Changed own password',
  'auth.password_change_failed': 'Failed password change',
  'user.create': 'Created user',
  'user.update': 'Edited user',
  'user.password_reset': 'Reset password of',
  'user.disable': 'Disabled user',
  'user.enable': 'Enabled user',
  'company.create': 'Created company',
  'company.update': 'Edited company',
  'company.disable': 'Disabled company',
  'company.enable': 'Enabled company',
  'document.ingest': 'Added document',
  'document.delete': 'Deleted document',
  'regulatory.version_upload': 'Uploaded regulatory version',
  'regulatory.version_restore': 'Restored regulatory version',
  'regulatory.version_retire': 'Retired regulatory version',
  'regulatory.fda_code_add': 'Added FDA product code',
  'regulatory.fda_code_remove': 'Removed FDA product code',
  'regulatory.fda_sync': 'Checked openFDA',
  'regulatory.cfr_part_add': 'Added CFR part',
  'regulatory.cfr_part_remove': 'Removed CFR part',
  'regulatory.ecfr_sync': 'Checked eCFR',
  'regulatory.law_item_add': 'Added UK/EU law item',
  'regulatory.law_item_remove': 'Removed UK/EU law item',
  'regulatory.law_sync': 'Checked UK/EU law',
  'regulatory.approve': 'Approved update',
  'regulatory.reject': 'Rejected update',
};

export function actionLabel(action) {
  return LABELS[action] || String(action || '').replace(/[._]/g, ' ');
}

// detail ke chhote safe fields ko ek line mein
export function detailText(detail) {
  if (!detail || typeof detail !== 'object') return '';
  return Object.entries(detail)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${Array.isArray(v) ? v.join(', ') : v}`)
    .join(' · ');
}

export function actorLabel(e) {
  if (!e) return '';
  return e.company ? `${e.actor} (${e.company})` : e.role === 'admin' ? `${e.actor} (super admin)` : e.actor;
}
